"""
Опрос Carrot Quest на новые сообщения от посетителей — замена вебхука
(см. carrotquest_client.py про то, почему вебхука для этого нет).
"""

import logging
import time
import uuid

logger = logging.getLogger(__name__)


def poll_once(cq_client, suvvy, seen) -> int:
    """Один проход опроса. Возвращает число сообщений, переданных в Suvvy."""
    forwarded = 0
    for conversation in cq_client.list_conversations_with_unread():
        conversation_id = conversation["id"]

        try:
            parts = cq_client.get_visitor_parts(conversation_id)
        except Exception:
            logger.exception(
                "Не удалось получить реплики диалога (conversation_id=%s)", conversation_id
            )
            continue

        for part in parts:
            part_id = part.get("id")
            text = part.get("body")
            if part_id is None or not text:
                continue

            dedup_key = f"cq:part:{part_id}"
            if seen.already_seen(dedup_key):
                continue
            seen.mark_seen(dedup_key)

            try:
                suvvy.send_message(
                    chat_id=str(conversation_id),
                    text=text,
                    message_id=str(uuid.uuid4()),
                    sender="customer",
                )
                forwarded += 1
            except Exception:
                logger.exception(
                    "Не удалось передать сообщение в Suvvy (conversation_id=%s, part_id=%s)",
                    conversation_id,
                    part_id,
                )

    return forwarded


def run_forever(cq_client, suvvy, seen, interval_seconds: int = 5) -> None:
    """Бесконечный цикл опроса — для постоянного хостинга (не подходит для
    serverless-рантайма вроде Vercel, там для опроса используется
    отдельный HTTP-эндпоинт /poll/tick + внешний cron, см. bridge/README.md).
    """
    while True:
        try:
            poll_once(cq_client, suvvy, seen)
        except Exception:
            logger.exception("Ошибка при опросе Carrot Quest")
        time.sleep(interval_seconds)
