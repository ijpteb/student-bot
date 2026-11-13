"""Настройки бота. Читаются из переменных окружения и файла .env."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
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

    @field_validator("bot_token", mode="before")
    @classmethod
    def _strip_token(cls, v):
        # Лишние пробелы или кавычки при вставке токена в TextEdit.
        return v.strip().strip("\"'").strip() if isinstance(v, str) else v

    @field_validator("telegram_api_url")
    @classmethod
    def _check_api_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("TELEGRAM_API_URL должен начинаться с https:// (например, https://api.telegram.org)")
        return v.rstrip("/")

    @field_validator("default_timezone")
    @classmethod
    def _check_tz(cls, v: str) -> str:
        v = v.strip()
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("такого часового пояса нет. Пример: Europe/Moscow, Asia/Yekaterinburg") from None
        return v


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
