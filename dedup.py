"""
Простое хранилище "уже обработанных" ключей — защита от повторной
пересылки, если Carrot Quest или Suvvy пришлют один и тот же вебхук
ещё раз (обычное дело при ретраях). Храним последние ключи в JSON-файле —
для моста такого размера отдельная база не нужна.

На Vercel файловая система только для чтения, кроме /tmp, а сам /tmp
живёт только пока тёплый инстанс функции — после холодного старта
дедупликация просто начинается заново. Для тестового прогона это ок:
хуже не будет, максимум иногда пролетит повторное сообщение при ретрае.
"""

import json
import os
from collections import deque

_MAX_KEEP = 2000


class SeenStore:
    def __init__(self, path: str):
        # На Vercel (или любом другом read-only рантайме) VERCEL=1 в
        # окружении — пишем во временную папку вместо корня проекта.
        if os.environ.get("VERCEL"):
            path = os.path.join("/tmp", os.path.basename(path))
        self.path = path
        self._items: deque = deque(maxlen=_MAX_KEEP)
        self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self._items = deque(json.load(f), maxlen=_MAX_KEEP)
        except OSError:
            # Файла ещё нет / диск недоступен — просто стартуем с пустого списка.
            pass

    def _save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(list(self._items), f)
        except OSError:
            # Не удалось сохранить — не критично, дедупликация всего лишь
            # не переживёт следующий холодный старт.
            pass

    def already_seen(self, key: str) -> bool:
        return key in self._items

    def mark_seen(self, key: str) -> None:
        self._items.append(key)
        self._save()
