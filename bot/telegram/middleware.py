"""Для каждого входящего сообщения открывает соединение с базой и находит студента."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.core.users import get_or_create_user
from bot.messenger import MessageRejected, TemporaryFailure
from bot.telegram.errors import is_transport_error


def reply_failed(exc: BaseException) -> bool:
    """Ошибка случилась при ответе в Telegram (а не в логике бота): изменения к этому моменту сделаны."""
    return isinstance(exc, (TelegramAPIError, TemporaryFailure, MessageRejected)) or is_transport_error(exc)


class DbMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker: async_sessionmaker, default_tz: str):
        self.sessionmaker = sessionmaker
        self.default_tz = default_tz

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        chat = data.get("event_chat")
        # Бот работает только в личной переписке.
        if tg_user is None or (chat is not None and chat.type != "private"):
            return None
        async with self.sessionmaker() as session:
            data["session"] = session
            data["user"] = await get_or_create_user(session, "telegram", str(tg_user.id), self.default_tz)
            try:
                result = await handler(event, data)
            except Exception as e:
                if reply_failed(e):
                    # Не удалось только ответить в Telegram. Изменения обработчики делают до ответа,
                    # и студент их уже запросил — сохраняем, а не откатываем.
                    await session.commit()
                else:
                    await session.rollback()
                raise
            await session.commit()
            return result
