"""
Опрос Carrot Quest на новые сообщения от посетителей — замена вебхука
(см. carrotquest_client.py про то, почему вебхука для этого нет).
"""

import logging
import re
import time
import uuid

logger = logging.getLogger(__name__)

# Российский номер в любом привычном написании: +7..., 8..., с пробелами/
# скобками/дефисами или без. Этого достаточно для того, что клиент печатает
# руками в чат — не парсер произвольных международных номеров.
_PHONE_RE = re.compile(r"(?:\+7|8|7)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")


def extract_phone(text: str) -> str | None:
    """Достаёт из текста сообщения российский номер телефона, если он там есть.

    Без "+" — проверено вживую 2026-07-30: Carrot Quest молча отклоняет
    значение свойства $phone с ведущим "+" (API отвечает 200, но кладёт
    ключ в "not_changed_props"), а чистые цифры принимает нормально.
    """
    match = _PHONE_RE.search(text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group())
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    return None


def poll_once(cq_client, suvvy, seen) -> int:
    """Один проход опроса. Возвращает число сообщений, переданных в Suvvy."""
    forwarded = 0
    for conversation in cq_client.list_conversations_with_unread():
        conversation_id = conversation["id"]
        user_id = conversation.get("user", {}).get("id")

        try:
            parts = cq_client.get_visitor_parts(conversation_id)
        except Exception:
            logger.exception(
                "Не удалось получить реплики диалога (conversation_id=%s)", conversation_id
            )
            continue

        # Carrot Quest отдаёт реплики от новых к старым — а нам нужно
        # обработать их в хронологическом порядке. Иначе при нескольких
        # новых репликах за один проход (например, после сброса
        # дедупликации на холодном старте serverless) $phone запишется по
        # САМОЙ СТАРОЙ реплике, а не по последней — проверено вживую.
        parts = sorted(parts, key=lambda p: p.get("created", 0))

        for part in parts:
            part_id = part.get("id")
            text = part.get("body")
            if part_id is None or not text:
                continue

            dedup_key = f"cq:part:{part_id}"
            if seen.already_seen(dedup_key):
                continue
            seen.mark_seen(dedup_key)

            # Если посетитель оставил телефон — записываем его в свойство
            # $phone пользователя. Это триггерит системное событие Carrot
            # Quest "$phone_changed", на которое настраивается готовая
            # интеграция Интеграции → CRM → Битрикс24 (без своего кода для
            # самого Битрикса, см. bridge/README.md).
            phone = extract_phone(text)
            if phone and user_id:
                try:
                    cq_client.set_user_phone(user_id, phone)
                except Exception:
                    logger.exception(
                        "Не удалось записать телефон пользователя (user_id=%s)", user_id
                    )

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
