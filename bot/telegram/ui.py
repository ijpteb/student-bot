"""Общие помощники для обработчиков."""

from datetime import date, datetime

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.core.timeutil import local_now
from bot.core.users import week1_of
from bot.models import User

MAX_LEN = 4000


def now_l(user: User) -> datetime:
    return local_now(user.timezone)


def today(user: User) -> date:
    return now_l(user).date()


def week1(user: User) -> date:
    return week1_of(user, today(user))


def clip(text: str) -> str:
    if len(text) <= MAX_LEN:
        return text
    return text[: MAX_LEN - 40].rsplit("\n", 1)[0] + "\n\n<i>…список не поместился целиком</i>"


async def show(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Из кнопки — заменить текущее сообщение, из текста — отправить новое."""
    text = clip(text)
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                await event.message.answer(text, reply_markup=markup)
        await event.answer()
    else:
        await event.answer(text, reply_markup=markup)
