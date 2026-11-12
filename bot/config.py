"""Настройки бота. Читаются из переменных окружения и файла .env."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    bot_token: SecretStr
    # Telegram ID владельца бота: только ему доступны служебные команды.
    admin_id: int | None = None
    database_url: str = "postgresql+asyncpg://bot:bot@localhost:5432/bot"
    # Адрес сервера Telegram Bot API. С некоторых российских серверов стандартный
    # адрес бывает недоступен — тогда здесь указывают свой сервер Bot API.
    telegram_api_url: str = "https://api.telegram.org"
    # Необязательный прокси для связи с Telegram, например socks5://host:port.
    telegram_proxy: str | None = None
    default_timezone: str = "Europe/Moscow"
    log_level: str = "INFO"


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
