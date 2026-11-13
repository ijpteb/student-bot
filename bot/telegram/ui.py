"""Общие помощники для обработчиков."""

import logging
from datetime import date, datetime

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.core.render import visible_len
from bot.core.timeutil import local_now
from bot.core.users import week1_of
from bot.models import User

log = logging.getLogger(__name__)

MAX_LEN = 4000  # Telegram принимает до 4096 видимых знаков


def now_l(user: User) -> datetime:
    return local_now(user.timezone)


def today(user: User) -> date:
    return now_l(user).date()


def week1(user: User) -> date:
    return week1_of(user, today(user))


def clip(text: str) -> str:
    """Обрезает слишком длинный текст по строкам (каждый тег закрывается в своей строке)."""
    if visible_len(text) <= MAX_LEN:
        return text
    kept, n = [], 0
    for line in text.split("\n"):
        n += visible_len(line) + 1
        if n > MAX_LEN - 60:
            break
        kept.append(line)
    return "\n".join(kept) + "\n\n<i>…не поместилось целиком</i>"


async def answer_cb(cb: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """Ответить на нажатие кнопки. Не страшно, если не получилось (кнопку нажали давно,
    пока бот был выключен): сделанное уже сохранено, откатывать его нельзя."""
    try:
        await cb.answer(text, show_alert=show_alert)
    except Exception as e:  # устаревшее нажатие, нет связи, прокси — ответ не обязателен
        log.info("Ответ на нажатие кнопки не доставлен: %s", type(e).__name__)


async def show(
    event: Message | CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    toast: str | None = None,
) -> None:
    """Из кнопки — заменить текущее сообщение (и ответить на нажатие ровно один раз, toast —
    короткая всплывающая подпись), из текста — отправить новое."""
    text = clip(text)
    if isinstance(event, CallbackQuery):
        msg = event.message
        if isinstance(msg, Message):
            try:
                await msg.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as e:
                if "not modified" not in str(e):
                    await msg.answer(text, reply_markup=markup)
        elif msg is not None:  # старое сообщение, которое уже нельзя изменить
            await msg.answer(text, reply_markup=markup)
        await answer_cb(event, toast)
    else:
        await event.answer(text, reply_markup=markup)
