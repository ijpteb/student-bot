"""Отправка сообщений из ядра через Telegram."""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.messenger import MessageRejected, RecipientUnavailable, TemporaryFailure
from bot.models import Attachment, User
from bot.telegram.errors import is_transport_error
from bot.telegram.kb import HwCb

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot: Bot, admin_id: int | None):
        self.bot = bot
        self.admin_id = admin_id

    async def _call(self, coro_factory, max_wait: float = 60):
        """Ошибки Telegram переводим в понятные ядру: недоступен получатель / нет связи / отказ.
        max_wait — сколько можно подождать, если Telegram просит сделать паузу (лимит сообщений)."""
        for attempt in range(2):
            try:
                return await coro_factory()
            except TelegramForbiddenError as e:
                raise RecipientUnavailable() from e
            except TelegramBadRequest as e:
                if "chat not found" in str(e).lower():
                    raise RecipientUnavailable() from e
                raise MessageRejected(type(e).__name__) from e
            except TelegramRetryAfter as e:
                if attempt or e.retry_after > max_wait:
                    raise TemporaryFailure("TelegramRetryAfter") from e
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                if is_transport_error(e):  # нет связи, прокси недоступен, Telegram отвечает ошибкой 5xx/HTML
                    raise TemporaryFailure(type(e).__name__) from e
                raise

    async def send_text(self, user: User, text: str, homework_id: int | None = None) -> None:
        markup = None
        if homework_id is not None:
            b = InlineKeyboardBuilder()
            b.button(text="✅ Выполнено", callback_data=HwCb(action="done", id=homework_id))
            b.button(text="Открыть", callback_data=HwCb(action="open", id=homework_id))
            markup = b.as_markup()
        await self._call(lambda: self.bot.send_message(int(user.external_id), text, reply_markup=markup))

    async def send_attachments(self, user: User, files: list[Attachment]) -> None:
        # Студент ждёт ответа и не может ничего делать, пока идёт отправка, — долго не ждём.
        chat = int(user.external_id)
        for f in files:
            if f.kind == "photo":
                await self._call(lambda f=f: self.bot.send_photo(chat, f.file_id), max_wait=3)
            else:
                await self._call(lambda f=f: self.bot.send_document(chat, f.file_id), max_wait=3)

    async def notify_admin(self, text: str) -> None:
        if self.admin_id:
            await self.bot.send_message(self.admin_id, text)
