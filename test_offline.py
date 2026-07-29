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
# 7. Ответ ИИ от Suvvy должен уйти обратно в диалог Carrot Quest
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
# 8. Неверный секрет Suvvy — запрос отклоняется
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
# 9. test_request от Suvvy (проверка при настройке канала) — просто 200, без пересылки
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
