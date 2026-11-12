"""Для каждого входящего сообщения открывает соединение с базой и находит студента."""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.core.users import get_or_create_user


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
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
