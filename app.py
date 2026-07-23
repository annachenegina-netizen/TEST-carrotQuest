"""
Bridge-сервер между Carrot Quest и Suvvy.ai (схема — в ../docs/integration.md).

Как это работает:
1. Посетитель пишет в виджет Carrot Quest на сайте.
2. Carrot Quest шлёт вебхук сюда, в /webhook/carrotquest.
3. Мы пересылаем текст в Suvvy через её API.
4. Suvvy обрабатывает вопрос ИИ и присылает готовый ответ сюда,
   в /webhook/suvvy (это отдельный, асинхронный запрос).
5. Мы кладём этот ответ обратно в диалог через API Carrot Quest.

conversation_id из Carrot Quest используется как chat_id в Suvvy —
он и связывает две системы между собой, отдельной базы сопоставлений не нужно.
"""

import logging
import time
import uuid

from flask import Flask, request, jsonify

import config
import carrotquest_client
import suvvy_client
from dedup import SeenStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
cfg = config.load_config()

cq_client = carrotquest_client.CarrotQuestClient(cfg.carrotquest)
suvvy = suvvy_client.SuvvyClient(cfg.suvvy)

# Защита от повторной пересылки одного и того же сообщения при ретраях
seen = SeenStore("seen_messages.json")


@app.route("/", methods=["GET"])
def health():
    return "OK"


@app.route("/webhook/carrotquest", methods=["POST"], strict_slashes=False)
def webhook_from_carrotquest():
    """Carrot Quest шлёт form-urlencoded данные о новом сообщении посетителя."""
    data = request.form

    if data.get("token") != cfg.carrotquest.webhook_token:
        logger.warning("Carrot Quest webhook: неверный token, запрос отклонён")
        return jsonify({"error": "invalid token"}), 403

    conversation_id = data.get("conversation_id")
    message_text = data.get("conversation_body")

    # Другие типы событийных вебхуков (не сообщение от посетителя) —
    # просто подтверждаем получение и ничего не пересылаем.
    if not conversation_id or not message_text:
        return "OK", 200

    # Ключ дедупликации грубый (по времени с округлением до 5 сек), но для
    # защиты от ретраев этого достаточно — точного message_id Carrot Quest не даёт.
    dedup_key = f"cq:{conversation_id}:{hash(message_text)}:{int(time.time() // 5)}"
    if seen.already_seen(dedup_key):
        return "OK", 200
    seen.mark_seen(dedup_key)

    try:
        suvvy.send_message(
            chat_id=conversation_id,
            text=message_text,
            message_id=str(uuid.uuid4()),
            sender="customer",
        )
    except Exception:
        logger.exception(
            "Не удалось передать сообщение в Suvvy (conversation_id=%s)", conversation_id
        )
        # Отвечаем 200 всё равно: проблема не в самом вебхуке, а в недоступности
        # Suvvy, и ретрай того же вебхука от Carrot Quest её не решит.

    return "OK", 200


@app.route("/webhook/suvvy", methods=["POST"], strict_slashes=False)
def webhook_from_suvvy():
    """Suvvy шлёт JSON с готовым ответом ИИ на конкретный chat_id."""
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {cfg.suvvy.webhook_secret}"
    if auth_header != expected:
        logger.warning("Suvvy webhook: неверный секрет, запрос отклонён")
        return jsonify({"error": "invalid secret"}), 403

    payload = request.get_json(silent=True) or {}
    event_type = payload.get("event_type")

    # Suvvy шлёт test_request при настройке канала — просто подтверждаем.
    if event_type != "new_messages":
        return "OK", 200

    conversation_id = payload.get("chat_id")
    for message in payload.get("new_messages", []):
        if message.get("message_sender") != "ai" or message.get("type") != "text":
            continue
        text = message.get("text")
        if not text:
            continue
        try:
            cq_client.send_reply(conversation_id, text)
        except Exception:
            logger.exception(
                "Не удалось отправить ответ в Carrot Quest (conversation_id=%s)", conversation_id
            )

    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=cfg.port)
