"""
Клиент для Carrot Quest API — только то, что нужно мосту: отправить
ответ бота обратно в диалог с посетителем.
"""

import requests

_REPLY_URL = "https://api.carrotquest.io/v1/conversations/{conversation_id}/reply"


class CarrotQuestClient:
    def __init__(self, cfg):
        self.auth_token = cfg.auth_token

    def send_reply(self, conversation_id: str, text: str) -> None:
        url = _REPLY_URL.format(conversation_id=conversation_id)
        response = requests.post(
            url,
            data={"auth_token": self.auth_token, "body": text},
            timeout=15,
        )
        response.raise_for_status()
