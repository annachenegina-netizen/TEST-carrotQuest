"""
Настройки моста. Все секреты — из .env (см. .env.example), чтобы токены
не попадали в git.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    # .strip() — защита от лишнего пробела/переноса строки, которые легко
    # случайно вставить, копируя значение в поле в дашборде Vercel/Suvvy.
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Не задана переменная окружения {name}. Добавь её в .env (см. .env.example)."
        )
    return value


@dataclass(frozen=True)
class CarrotQuestConfig:
    # Токен приложения (админка Carrot Quest → "Разработчикам", формат
    # "app.<id>.<секрет>") — им же подписываем запросы к Web API при
    # поллинге диалогов и отвечаем в диалог.
    auth_token: str


@dataclass(frozen=True)
class SuvvyConfig:
    # Токен для наших запросов К Suvvy (Bearer).
    api_token: str
    # Секретное слово, которым Suvvy подписывает свои вебхуки К НАМ.
    webhook_secret: str


@dataclass(frozen=True)
class Config:
    carrotquest: CarrotQuestConfig
    suvvy: SuvvyConfig
    port: int
    # Секрет для защиты /poll/tick — этот эндпоинт дёргает внешний cron
    # (Vercel Cron/cron-job.org), без проверки кто угодно мог бы им спамить
    # наши запросы в Suvvy.
    poll_secret: str
    poll_interval_seconds: int


def load_config() -> Config:
    return Config(
        carrotquest=CarrotQuestConfig(
            auth_token=_required("CARROTQUEST_AUTH_TOKEN"),
        ),
        suvvy=SuvvyConfig(
            api_token=_required("SUVVY_API_TOKEN"),
            webhook_secret=_required("SUVVY_WEBHOOK_SECRET"),
        ),
        port=int(os.environ.get("PORT", "8000")),
        poll_secret=_required("POLL_SECRET"),
        poll_interval_seconds=int(os.environ.get("POLL_INTERVAL_SECONDS", "5")),
    )
