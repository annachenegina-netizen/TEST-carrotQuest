"""
Офлайн-проверка моста БЕЗ реальных аккаунтов Carrot Quest/Suvvy.

Подменяет исходящие HTTP-запросы (requests.post) фейковыми ответами и
прогоняет реальный код обработчиков вебхуков — чтобы поймать логические
ошибки ДО того, как появятся настоящие доступы.

Запуск: python test_offline.py
"""

import os
import sys
from unittest.mock import patch, Mock

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

# Тестовые переменные окружения — чтобы config.py не падал на "нет .env"
os.environ["CARROTQUEST_WEBHOOK_TOKEN"] = "test-cq-token"
os.environ["CARROTQUEST_AUTH_TOKEN"] = "test-cq-auth"
os.environ["SUVVY_API_TOKEN"] = "test-suvvy-token"
os.environ["SUVVY_WEBHOOK_SECRET"] = "test-suvvy-secret"

import app as bridge_app  # noqa: E402

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


import tempfile

tmp = tempfile.mkdtemp()

# ---------------------------------------------------------------------
# 1. Сообщение от посетителя в Carrot Quest должно уйти в Suvvy
# ---------------------------------------------------------------------
client = fresh_client(tmp)

with patch("suvvy_client.requests.post") as mocked_post:
    mocked_post.return_value = Mock(raise_for_status=Mock())

    response = client.post(
        "/webhook/carrotquest",
        data={
            "token": "test-cq-token",
            "conversation_id": "conv-1",
            "conversation_body": "Сколько стоит линия раздачи?",
        },
    )

    check("carrotquest webhook: отвечает 200", response.status_code == 200, str(response.status_code))
    check("carrotquest webhook: запрос в Suvvy ушёл", mocked_post.called)

    sent_json = mocked_post.call_args.kwargs["json"]
    check("carrotquest webhook: chat_id = conversation_id", sent_json["chat_id"] == "conv-1", sent_json["chat_id"])
    check("carrotquest webhook: текст сообщения передан как есть", sent_json["text"] == "Сколько стоит линия раздачи?")
    check("carrotquest webhook: sender = customer", sent_json["message_sender"] == "customer")

# ---------------------------------------------------------------------
# 2. Неверный token — запрос должен отклоняться, в Suvvy ничего не улетает
# ---------------------------------------------------------------------
client = fresh_client(tmp)

with patch("suvvy_client.requests.post") as mocked_post:
    response = client.post(
        "/webhook/carrotquest",
        data={"token": "wrong", "conversation_id": "conv-1", "conversation_body": "hi"},
    )
    check("carrotquest webhook: неверный token → 403", response.status_code == 403, str(response.status_code))
    check("carrotquest webhook: неверный token → в Suvvy ничего не ушло", not mocked_post.called)

# ---------------------------------------------------------------------
# 3. Повторный такой же вебхук (ретрай) не должен дублировать пересылку
# ---------------------------------------------------------------------
client = fresh_client(tmp)

with patch("suvvy_client.requests.post") as mocked_post:
    mocked_post.return_value = Mock(raise_for_status=Mock())
    payload = {"token": "test-cq-token", "conversation_id": "conv-2", "conversation_body": "Привет"}

    client.post("/webhook/carrotquest", data=payload)
    client.post("/webhook/carrotquest", data=payload)  # тот же самый запрос повторно

    check("carrotquest webhook: ретрай не дублирует пересылку в Suvvy", mocked_post.call_count == 1, f"вызовов: {mocked_post.call_count}")

# ---------------------------------------------------------------------
# 4. Ответ ИИ от Suvvy должен уйти обратно в диалог Carrot Quest
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
    check("suvvy webhook: auth_token подставлен верно", sent_data["auth_token"] == "test-cq-auth")
    check("suvvy webhook: текст ответа передан как есть", sent_data["body"] == "Линия раздачи стоит от 150 000 руб.")

# ---------------------------------------------------------------------
# 5. Неверный секрет Suvvy — запрос отклоняется
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
# 6. test_request от Suvvy (проверка при настройке канала) — просто 200, без пересылки
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
