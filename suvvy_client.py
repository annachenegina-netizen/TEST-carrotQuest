"""
Клиент для Suvvy.ai — персональный канал (API). Сюда отправляем сообщение
посетителя; ответ ИИ прилетает отдельным вебхуком к нам же (см. app.py).
"""

import requests

_SEND_MESSAGE_URL = "https://api.suvvy.ai/api/webhook/custom/message"


class SuvvyClient:
    def __init__(self, cfg):
        self.api_token = cfg.api_token

    def send_message(self, chat_id: str, text: str, message_id: str, sender: str = "customer") -> None:
        response = requests.post(
            _SEND_MESSAGE_URL,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            json={
                "api_version": 1,
                "message_id": message_id,
                "chat_id": chat_id,
                "text": text,
                "message_sender": sender,
                # Значение для колонки "Источник" в интерфейсе Suvvy
                "source": "refettorio.ru",
            },
            timeout=15,
        )
        response.raise_for_status()
