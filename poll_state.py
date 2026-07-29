"""
Последний результат опроса — только для тестовой панели /status, чтобы
видеть, жив ли сервер и когда был последний опрос, не залезая в логи Vercel.
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional


class PollState:
    def __init__(self, path: str):
        # Тот же приём, что и в dedup.py: на Vercel диск read-only, кроме /tmp.
        if os.environ.get("VERCEL"):
            path = os.path.join("/tmp", os.path.basename(path))
        self.path = path

    def record(self, forwarded: Optional[int], error: Optional[str]) -> None:
        data = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "forwarded": forwarded,
            "error": error,
        }
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def read(self) -> Optional[dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
