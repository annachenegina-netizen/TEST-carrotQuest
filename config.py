"""
Настройки моста. Все секреты — из .env (см. .env.example), чтобы токены
не попадали в git.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Не задана переменная окружения {name}. Добавь её в .env (см. .env.example)."
        )
    return value


@dataclass(frozen=True)
class CarrotQuestConfig:
    # Токен, которым Carrot Quest подписывает вебхуки — сверяем на входе,
    # чтобы никто чужой не мог слать нам поддельные сообщения посетителей.
    webhook_token: str
    # Токен приложения для ответа в диалог (админка Carrot Quest → "Разработчикам").
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


def load_config() -> Config:
    return Config(
        carrotquest=CarrotQuestConfig(
            webhook_token=_required("CARROTQUEST_WEBHOOK_TOKEN"),
            auth_token=_required("CARROTQUEST_AUTH_TOKEN"),
        ),
        suvvy=SuvvyConfig(
            api_token=_required("SUVVY_API_TOKEN"),
            webhook_secret=_required("SUVVY_WEBHOOK_SECRET"),
        ),
        port=int(os.environ.get("PORT", "8000")),
    )
