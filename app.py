"""
Мост между Carrot Quest и Suvvy.ai (схема — в ../docs/integration.md).

Как это работает:
1. Посетитель пишет в виджет Carrot Quest на сайте.
2. Мы САМИ периодически опрашиваем Carrot Quest на новые сообщения
   (см. poller.py) — вебхука на "новое сообщение от посетителя" в Carrot
   Quest нет (проверено вручную по всем разделам админки и по докам,
   есть только статистика по исходящим триггерным рассылкам).
3. Найденный текст пересылаем в Suvvy через её API.
4. Suvvy обрабатывает вопрос ИИ и присылает готовый ответ сюда,
   в /webhook/suvvy (это отдельный, асинхронный запрос — тут вебхук
   у Suvvy есть и он реально работает, проверено).
5. Мы кладём этот ответ обратно в диалог через API Carrot Quest.

conversation_id из Carrot Quest используется как chat_id в Suvvy —
он и связывает две системы между собой, отдельной базы сопоставлений не нужно.
"""

import logging
import threading

from flask import Flask, request, jsonify

import config
import carrotquest_client
import suvvy_client
import poller
from dedup import SeenStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
cfg = config.load_config()

cq_client = carrotquest_client.CarrotQuestClient(cfg.carrotquest)
suvvy = suvvy_client.SuvvyClient(cfg.suvvy)

# Защита от повторной пересылки одного и того же сообщения/реплики
seen = SeenStore("seen_messages.json")


@app.route("/", methods=["GET"])
def health():
    return "OK"


@app.route("/poll/tick", methods=["GET", "POST"], strict_slashes=False)
def poll_tick():
    """Один проход опроса Carrot Quest на новые сообщения от посетителей.

    Для постоянного хостинга (не serverless) это не нужно — там достаточно
    фонового потока (см. run_forever ниже). А вот на serverless-рантайме
    (Vercel и т.п.) фоновых процессов между запросами нет, поэтому опрос
    дёргается снаружи по расписанию (внешний cron, см. bridge/README.md).
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header != f"Bearer {cfg.poll_secret}":
        return jsonify({"error": "invalid secret"}), 403

    forwarded = poller.poll_once(cq_client, suvvy, seen)
    return jsonify({"forwarded": forwarded}), 200


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
    # Локально/на постоянном хостинге (не serverless) опрос удобнее не
    # ждать снаружи через cron, а крутить фоновым потоком прямо тут.
    poll_thread = threading.Thread(
        target=poller.run_forever,
        args=(cq_client, suvvy, seen, cfg.poll_interval_seconds),
        daemon=True,
    )
    poll_thread.start()

    app.run(host="0.0.0.0", port=cfg.port)
