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

import html
import logging
import threading

from flask import Flask, request, jsonify, redirect

import config
import carrotquest_client
import suvvy_client
import poller
from dedup import SeenStore
from poll_state import PollState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
cfg = config.load_config()

cq_client = carrotquest_client.CarrotQuestClient(cfg.carrotquest)
suvvy = suvvy_client.SuvvyClient(cfg.suvvy)

# Защита от повторной пересылки одного и того же сообщения/реплики
seen = SeenStore("seen_messages.json")

# Для тестовой панели /status — когда был последний опрос и что нашёл
poll_state = PollState("last_poll.json")


def _is_authorized() -> bool:
    auth_header = request.headers.get("Authorization", "")
    key_param = request.args.get("key", "")
    return auth_header == f"Bearer {cfg.poll_secret}" or key_param == cfg.poll_secret


@app.route("/", methods=["GET"])
def health():
    return "OK"


@app.route("/poll/tick", methods=["GET", "POST"], strict_slashes=False)
def poll_tick():
    """Один проход опроса Carrot Quest на новые сообщения от посетителей.

    Для постоянного хостинга (не serverless) это не нужно — там достаточно
    фонового потока (см. run_forever ниже). А вот на serverless-рантайме
    (Vercel и т.п.) фоновых процессов между запросами нет, поэтому опрос
    дёргается снаружи: либо кнопкой на /status (тест), либо по расписанию
    внешним cron с заголовком Authorization (см. bridge/README.md).
    """
    if not _is_authorized():
        return jsonify({"error": "invalid secret"}), 403

    try:
        forwarded = poller.poll_once(cq_client, suvvy, seen)
        poll_state.record(forwarded=forwarded, error=None)
    except Exception as exc:
        logger.exception("Ошибка при опросе Carrot Quest")
        poll_state.record(forwarded=None, error=str(exc))
        forwarded = None

    key_param = request.args.get("key", "")
    if key_param:
        # Запущено кнопкой с тестовой панели — вернуться туда же
        return redirect(f"/status?key={key_param}")
    return jsonify({"forwarded": forwarded}), 200


@app.route("/status", methods=["GET"])
def status_page():
    """Тестовая панель: жив ли сервер, когда был последний опрос, кнопка
    запустить опрос вручную — чтобы не звать curl/cron-job.org для теста.
    """
    key_param = request.args.get("key", "")
    if key_param != cfg.poll_secret:
        return "Доступ запрещён", 403

    last = poll_state.read()
    if last is None:
        last_line = "Опросов ещё не было"
    elif last.get("error"):
        last_line = f"Последний опрос: {last['at']} — ОШИБКА: {html.escape(last['error'])}"
    else:
        last_line = f"Последний опрос: {last['at']} — переслано в Suvvy сообщений: {last['forwarded']}"

    safe_key = html.escape(key_param, quote=True)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>ЧТТ bridge — тестовая панель</title>
</head>
<body style="font-family: sans-serif; max-width: 640px; margin: 40px auto; line-height: 1.6; padding: 0 16px;">
  <h1>Мост Suvvy ⇆ Carrot Quest</h1>
  <p>Сервер жив.</p>
  <p>{last_line}</p>
  <form method="post" action="/poll/tick?key={safe_key}">
    <button type="submit" style="font-size: 16px; padding: 8px 16px; cursor: pointer;">
      Опросить сейчас
    </button>
  </form>
</body>
</html>"""


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
