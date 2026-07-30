"""
Клиент для Carrot Quest API. Раньше мост ждал вебхук от Carrot Quest на
"новое сообщение от посетителя" — но такого системного события в Carrot
Quest нет (проверено вручную по всем разделам админки и по документации,
см. docs/carrotquest.md). Есть только статистика по ИСХОДЯЩИМ триггерным
рассылкам (доставлено/прочитано/был клик), а не событие на входящее
сообщение от живого посетителя в открытом диалоге.

Поэтому вместо вебхука — поллинг (см. poller.py): сами спрашиваем API,
у каких диалогов есть непрочитанные реплики от посетителя, и забираем их.
"""

import re

import requests

_API_BASE = "https://api.carrotquest.io/v1"
_REPLY_URL = _API_BASE + "/conversations/{conversation_id}/reply"
_APP_CONVERSATIONS_URL = _API_BASE + "/apps/{app_id}/conversations"
_CONVERSATION_PARTS_URL = _API_BASE + "/conversations/{conversation_id}/parts"

# Реплика посетителя в диалоге — единственный интересующий нас тип части
# диалога (bcost: reply_admin/auto_reply/note/tag_added и т.п. — не наши).
_VISITOR_PART_TYPE = "reply_user"


class CarrotQuestClient:
    def __init__(self, cfg):
        self.auth_token = cfg.auth_token
        # app_id зашит в сам auth_token как "app.<id>.<секрет>" (видно в
        # админке, раздел "Разработчикам") — отдельно в конфиге не храним.
        match = re.match(r"^app\.(\d+)\.", self.auth_token)
        if not match:
            raise RuntimeError(
                "CARROTQUEST_AUTH_TOKEN должен быть в формате 'app.<id>.<секрет>' "
                "(как в админке Carrot Quest, раздел «Разработчикам»)"
            )
        self.app_id = match.group(1)

    def send_reply(self, conversation_id: str, text: str) -> None:
        url = _REPLY_URL.format(conversation_id=conversation_id)
        response = requests.post(
            url,
            data={"auth_token": self.auth_token, "body": text},
            timeout=15,
        )
        response.raise_for_status()

    def list_conversations_with_unread(self) -> list[dict]:
        """Диалоги приложения, где есть реплики посетителя, не прочитанные оператором.

        include_not_assigned=true обязателен: без него API по умолчанию не
        отдаёт диалоги без назначенного оператора — а у нас ВСЕ диалоги
        именно такие (ни один оператор ими не занимается, отвечает только
        Suvvy). Проверено вживую: без этого параметра список всегда пуст,
        даже если диалог с сообщением реально существует.
        """
        url = _APP_CONVERSATIONS_URL.format(app_id=self.app_id)
        response = requests.get(
            url,
            params={
                "auth_token": self.auth_token,
                "closed": "false",
                "include_not_assigned": "true",
            },
            timeout=15,
        )
        response.raise_for_status()
        conversations = response.json().get("data", [])
        return [c for c in conversations if c.get("admin_unread_count", 0) > 0]

    def get_visitor_parts(self, conversation_id: str) -> list[dict]:
        """Реплики посетителя (не оператора/бота) в диалоге, в порядке от API."""
        url = _CONVERSATION_PARTS_URL.format(conversation_id=conversation_id)
        response = requests.get(
            url,
            params={"auth_token": self.auth_token},
            timeout=15,
        )
        response.raise_for_status()
        parts = response.json().get("data", [])
        return [p for p in parts if p.get("type") == _VISITOR_PART_TYPE]
