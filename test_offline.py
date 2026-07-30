"""
Офлайн-проверка моста БЕЗ реальных аккаунтов Carrot Quest/Suvvy.

Подменяет исходящие HTTP-запросы (requests.get/post) фейковыми ответами и
прогоняет реальный код обработчиков — чтобы поймать логические ошибки ДО
того, как появятся настоящие доступы.

Запуск: python test_offline.py
"""

import os
import sys
from unittest.mock import patch, Mock

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

# Тестовые переменные окружения — чтобы config.py не падал на "нет .env"
os.environ["CARROTQUEST_AUTH_TOKEN"] = "app.71969.test-cq-auth"
os.environ["SUVVY_API_TOKEN"] = "test-suvvy-token"
os.environ["SUVVY_WEBHOOK_SECRET"] = "test-suvvy-secret"
os.environ["POLL_SECRET"] = "test-poll-secret"

import app as bridge_app  # noqa: E402
import poller  # noqa: E402

failures = []


def check(name: str, condition: bool, detail: str = ""):
    status = "OK " if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def fresh_client(tmp_dir: str):
    # Каждый тест — со своим файлом дедупликации, чтобы тесты не влияли друг на друга
    bridge_app.seen.path = os.path.join(tmp_dir, "seen_messages.json")
    bridge_app.seen._items.clear()
    return bridge_app.app.test_client()


def mock_cq_get(conversations_response, parts_by_conversation):
    """Подменяет carrotquest_client.requests.get под оба метода (список
    диалогов и реплики диалога) по URL запроса."""

    def _fake_get(url, params=None, timeout=None):
        if url.endswith("/conversations") and "apps" in url:
            return Mock(
                raise_for_status=Mock(),
                json=Mock(return_value={"data": conversations_response}),
            )
        if "/parts" in url:
            conversation_id = url.split("/conversations/")[1].split("/parts")[0]
            return Mock(
                raise_for_status=Mock(),
                json=Mock(return_value={"data": parts_by_conversation.get(conversation_id, [])}),
            )
        raise AssertionError(f"неожиданный GET-запрос: {url}")

    return _fake_get


# ---------------------------------------------------------------------
# 1. app_id корректно извлекается из auth_token формата "app.<id>.<секрет>"
# ---------------------------------------------------------------------
check("carrotquest_client: app_id извлечён из auth_token", bridge_app.cq_client.app_id == "71969", bridge_app.cq_client.app_id)

# ---------------------------------------------------------------------
# 1a. include_not_assigned=true обязателен — без него Carrot Quest молча
# не отдаёт диалоги без назначенного оператора (у нас все диалоги такие).
# Проверено вживую 2026-07-30: без этого параметра список всегда пуст.
# ---------------------------------------------------------------------
with patch("carrotquest_client.requests.get") as mocked_get:
    mocked_get.return_value = Mock(raise_for_status=Mock(), json=Mock(return_value={"data": []}))
    bridge_app.cq_client.list_conversations_with_unread()
    sent_params = mocked_get.call_args.kwargs["params"]
    check(
        "carrotquest_client: include_not_assigned=true передан в запросе",
        sent_params.get("include_not_assigned") == "true",
        str(sent_params),
    )

# ---------------------------------------------------------------------
# 2. Поллинг: диалог с непрочитанной репликой посетителя → сообщение уходит в Suvvy
# ---------------------------------------------------------------------
tmp = __import__("tempfile").mkdtemp()
client = fresh_client(tmp)

conversations = [{"id": "conv-1", "admin_unread_count": 1}]
parts = {"conv-1": [{"id": "part-1", "type": "reply_user", "body": "Сколько стоит линия раздачи?"}]}

with patch("carrotquest_client.requests.get", side_effect=mock_cq_get(conversations, parts)):
    with patch("suvvy_client.requests.post") as mocked_post:
        mocked_post.return_value = Mock(raise_for_status=Mock())

        forwarded = poller.poll_once(bridge_app.cq_client, bridge_app.suvvy, bridge_app.seen)

        check("poller: одно сообщение передано в Suvvy", forwarded == 1, str(forwarded))
        check("poller: запрос в Suvvy ушёл", mocked_post.called)

        sent_json = mocked_post.call_args.kwargs["json"]
        check("poller: chat_id = conversation_id", sent_json["chat_id"] == "conv-1", sent_json["chat_id"])
        check("poller: текст реплики передан как есть", sent_json["text"] == "Сколько стоит линия раздачи?")
        check("poller: sender = customer", sent_json["message_sender"] == "customer")

# ---------------------------------------------------------------------
# 3. Повторный опрос той же самой реплики не должен дублировать пересылку
# ---------------------------------------------------------------------
with patch("carrotquest_client.requests.get", side_effect=mock_cq_get(conversations, parts)):
    with patch("suvvy_client.requests.post") as mocked_post:
        mocked_post.return_value = Mock(raise_for_status=Mock())
        poller.poll_once(bridge_app.cq_client, bridge_app.suvvy, bridge_app.seen)
        check("poller: повторный опрос не дублирует пересылку", not mocked_post.called)

# ---------------------------------------------------------------------
# 3a. extract_phone: достаёт номер телефона из текста в разных форматах
# ---------------------------------------------------------------------
check("extract_phone: +7 с пробелами", poller.extract_phone("+7 912 345 67 89") == "79123456789")
check("extract_phone: 8 слитно", poller.extract_phone("мой номер 89123456789, жду звонка") == "79123456789")
check("extract_phone: с дефисами и скобками", poller.extract_phone("8(912)345-67-89") == "79123456789")
check("extract_phone: без номера в тексте", poller.extract_phone("сколько стоит линия раздачи?") is None)

# ---------------------------------------------------------------------
# 3b. Поллинг: телефон в сообщении посетителя записывается в свойство $phone
#
# ВАЖНО: carrotquest_client.py и suvvy_client.py оба делают "import requests" —
# это один и тот же объект модуля в sys.modules. Патчить
# "carrotquest_client.requests.post" и "suvvy_client.requests.post" ОДНОВРЕМЕННО
# двумя вложенными patch() нельзя — они бьют по одному и тому же атрибуту
# requests.post, и внутренний patch молча перекрывает внешний на время своего
# действия. Поэтому патчим requests.post один раз и различаем вызовы по URL.
# ---------------------------------------------------------------------
client = fresh_client(tmp)
conversations_phone = [{"id": "conv-3", "admin_unread_count": 1, "user": {"id": 555}}]
parts_phone = {"conv-3": [{"id": "part-phone-1", "type": "reply_user", "body": "Мой телефон +7 912 345 67 89"}]}

with patch("carrotquest_client.requests.get", side_effect=mock_cq_get(conversations_phone, parts_phone)):
    with patch("carrotquest_client.requests.post") as mocked_post:
        mocked_post.return_value = Mock(raise_for_status=Mock())
        poller.poll_once(bridge_app.cq_client, bridge_app.suvvy, bridge_app.seen)

    props_calls = [c for c in mocked_post.call_args_list if c.args[0].endswith("/users/555/props")]
    check("poller: телефон посетителя ушёл в set_user_phone", len(props_calls) == 1, str(mocked_post.call_args_list))
    if props_calls:
        sent_json = props_calls[0].kwargs["json"]
        check(
            "poller: телефон нормализован в операции обновления свойства",
            sent_json["operations"][0]["value"] == "79123456789",
            str(sent_json),
        )
        check(
            "poller: by_user_id=false (иначе Carrot Quest создаёт фантомного пользователя)",
            sent_json["by_user_id"] is False,
            str(sent_json),
        )

# ---------------------------------------------------------------------
# 3c. Несколько реплик с телефоном за один проход (например, после сброса
# дедупликации на холодном старте) — побеждает телефон из САМОЙ ПОЗДНЕЙ
# реплики. API отдаёт реплики от новых к старым (как в реальном Carrot
# Quest) — специально кладём в mock именно в этом порядке.
# ---------------------------------------------------------------------
client = fresh_client(tmp)
conversations_multi = [{"id": "conv-4", "admin_unread_count": 1, "user": {"id": 777}}]
parts_multi = {
    "conv-4": [
        {"id": "part-new", "type": "reply_user", "body": "Актуальный телефон 79111112222", "created": 200},
        {"id": "part-old", "type": "reply_user", "body": "Старый телефон 79999998888", "created": 100},
    ]
}

with patch("carrotquest_client.requests.get", side_effect=mock_cq_get(conversations_multi, parts_multi)):
    with patch("carrotquest_client.requests.post") as mocked_post:
        mocked_post.return_value = Mock(raise_for_status=Mock())
        poller.poll_once(bridge_app.cq_client, bridge_app.suvvy, bridge_app.seen)

    props_calls = [c for c in mocked_post.call_args_list if c.args[0].endswith("/users/777/props")]
    check("poller: обе реплики с телефоном обработаны", len(props_calls) == 2, str(len(props_calls)))
    if len(props_calls) == 2:
        check(
            "poller: последней ушла реплика с АКТУАЛЬНЫМ телефоном (не самая старая)",
            props_calls[-1].kwargs["json"]["operations"][0]["value"] == "79111112222",
            str(props_calls[-1].kwargs["json"]),
        )

# ---------------------------------------------------------------------
# 4. Диалоги без непрочитанных реплик посетителя — игнорируются
# ---------------------------------------------------------------------
client = fresh_client(tmp)
conversations_read = [{"id": "conv-2", "admin_unread_count": 0}]

with patch("carrotquest_client.requests.get", side_effect=mock_cq_get(conversations_read, {})):
    with patch("suvvy_client.requests.post") as mocked_post:
        forwarded = poller.poll_once(bridge_app.cq_client, bridge_app.suvvy, bridge_app.seen)
        check("poller: диалог без непрочитанных реплик пропущен", forwarded == 0 and not mocked_post.called)

# ---------------------------------------------------------------------
# 5. /poll/tick: без верного секрета — 403, ничего не опрашивается
# ---------------------------------------------------------------------
client = fresh_client(tmp)

with patch("poller.poll_once") as mocked_poll:
    response = client.post("/poll/tick", headers={"Authorization": "Bearer wrong"})
    check("poll/tick: неверный секрет → 403", response.status_code == 403, str(response.status_code))
    check("poll/tick: неверный секрет → опрос не запущен", not mocked_poll.called)

# ---------------------------------------------------------------------
# 6. /poll/tick: с верным секретом — 200, опрос запущен
# ---------------------------------------------------------------------
with patch("poller.poll_once", return_value=3) as mocked_poll:
    response = client.post("/poll/tick", headers={"Authorization": "Bearer test-poll-secret"})
    check("poll/tick: верный секрет → 200", response.status_code == 200, str(response.status_code))
    check("poll/tick: верный секрет → опрос запущен", mocked_poll.called)
    check("poll/tick: возвращает количество переданных сообщений", response.get_json()["forwarded"] == 3)

# ---------------------------------------------------------------------
# 7. /status: без верного ключа — 403, с верным — 200 и кнопка теста
# ---------------------------------------------------------------------
client = fresh_client(tmp)

response = client.get("/status?key=wrong")
check("status: неверный ключ → 403", response.status_code == 403, str(response.status_code))

response = client.get("/status?key=test-poll-secret")
check("status: верный ключ → 200", response.status_code == 200, str(response.status_code))
check("status: страница содержит кнопку запуска опроса", b"\xd0\x9e\xd0\xbf\xd1\x80\xd0\xbe\xd1\x81\xd0\xb8\xd1\x82\xd1\x8c \xd1\x81\xd0\xb5\xd0\xb9\xd1\x87\xd0\xb0\xd1\x81" in response.data)

# ---------------------------------------------------------------------
# 7a. /poll/tick через query-параметр key (кнопка на /status) — редиректит обратно
# ---------------------------------------------------------------------
with patch("carrotquest_client.requests.get", side_effect=mock_cq_get([], {})):
    response = client.post("/poll/tick?key=test-poll-secret")
    check("poll/tick через key: редирект обратно на /status", response.status_code == 302 and "/status" in response.headers.get("Location", ""))

# ---------------------------------------------------------------------
# 7b. /poll/tick: если Carrot Quest API падает — не 500, ошибка сохраняется в состояние
# ---------------------------------------------------------------------
def _broken_get(url, params=None, timeout=None):
    raise ConnectionError("Carrot Quest недоступен")


with patch("carrotquest_client.requests.get", side_effect=_broken_get):
    response = client.post("/poll/tick", headers={"Authorization": "Bearer test-poll-secret"})
    check("poll/tick: сбой Carrot Quest API не роняет эндпоинт", response.status_code == 200, str(response.status_code))
    check("poll/tick: при сбое возвращает forwarded=null", response.get_json()["forwarded"] is None)

response = client.get("/status?key=test-poll-secret")
check("status: после сбоя показывает ошибку", "ОШИБКА" in response.get_data(as_text=True))

# ---------------------------------------------------------------------
# 9. Ответ ИИ от Suvvy должен уйти обратно в диалог Carrot Quest
# ---------------------------------------------------------------------
client = fresh_client(tmp)

with patch("carrotquest_client.requests.post") as mocked_post:
    mocked_post.return_value = Mock(raise_for_status=Mock())

    response = client.post(
        "/webhook/suvvy",
        json={
            "event_type": "new_messages",
            "chat_id": "conv-1",
            "new_messages": [
                {"type": "text", "message_sender": "ai", "text": "Линия раздачи стоит от 150 000 руб."}
            ],
        },
        headers={"Authorization": "Bearer test-suvvy-secret"},
    )

    check("suvvy webhook: отвечает 200", response.status_code == 200, str(response.status_code))
    check("suvvy webhook: ответ ушёл в Carrot Quest", mocked_post.called)

    sent_data = mocked_post.call_args.kwargs["data"]
    check("suvvy webhook: auth_token подставлен верно", sent_data["auth_token"] == "app.71969.test-cq-auth")
    check("suvvy webhook: текст ответа передан как есть", sent_data["body"] == "Линия раздачи стоит от 150 000 руб.")

# ---------------------------------------------------------------------
# 10. Неверный секрет Suvvy — запрос отклоняется
# ---------------------------------------------------------------------
client = fresh_client(tmp)

with patch("carrotquest_client.requests.post") as mocked_post:
    response = client.post(
        "/webhook/suvvy",
        json={"event_type": "test_request"},
        headers={"Authorization": "Bearer wrong"},
    )
    check("suvvy webhook: неверный секрет → 403", response.status_code == 403, str(response.status_code))
    check("suvvy webhook: неверный секрет → в Carrot Quest ничего не ушло", not mocked_post.called)

# ---------------------------------------------------------------------
# 11. test_request от Suvvy (проверка при настройке канала) — просто 200, без пересылки
# ---------------------------------------------------------------------
client = fresh_client(tmp)

with patch("carrotquest_client.requests.post") as mocked_post:
    response = client.post(
        "/webhook/suvvy",
        json={"event_type": "test_request"},
        headers={"Authorization": "Bearer test-suvvy-secret"},
    )
    check("suvvy webhook: test_request → 200", response.status_code == 200, str(response.status_code))
    check("suvvy webhook: test_request → ничего не пересылается", not mocked_post.called)

# ---------------------------------------------------------------------
print()
if failures:
    print(f"ПРОВАЛЕНО ПРОВЕРОК: {len(failures)} — {failures}")
    sys.exit(1)
else:
    print("Все проверки пройдены.")
    sys.exit(0)
