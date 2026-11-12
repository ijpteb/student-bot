"""Кнопки и данные, которые кнопки передают боту."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# Кнопки главного меню (внизу экрана).
TODAY = "📅 Сегодня"
TOMORROW = "📅 Завтра"
WEEK = "🗓 Неделя"
NEXT = "⏭ Ближайшая пара"
HW = "📚 Задания"
ADD_HW = "➕ Задание"
SETTINGS = "⚙️ Настройки"
MENU_BUTTONS = {TODAY, TOMORROW, WEEK, NEXT, HW, ADD_HW, SETTINGS}


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=TODAY), KeyboardButton(text=TOMORROW)],
            [KeyboardButton(text=WEEK), KeyboardButton(text=NEXT)],
            [KeyboardButton(text=HW), KeyboardButton(text=ADD_HW)],
            [KeyboardButton(text=SETTINGS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


class Nav(CallbackData, prefix="n"):
    to: str


class DayCb(CallbackData, prefix="d"):
    action: str  # show | week | edit
    day: str  # дата в виде 2026-11-12


class HwCb(CallbackData, prefix="h"):
    action: str
    id: int


class SubjCb(CallbackData, prefix="s"):
    action: str
    id: int


class LessonCb(CallbackData, prefix="l"):
    action: str
    id: int
    day: str = ""


class Pick(CallbackData, prefix="p"):
    """Выбор варианта на шаге диалога."""

    kind: str
    value: str


def cancel_row(b: InlineKeyboardBuilder) -> None:
    b.button(text="✖️ Отмена", callback_data=Nav(to="cancel"))


def single(text: str, cb: CallbackData) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=text, callback_data=cb)
    return b.as_markup()
