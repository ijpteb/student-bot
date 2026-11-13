"""Настройки: сводка, напоминания, часовой пояс, первая неделя, вуз, удаление данных."""

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core import reminders
from bot.core import users as users_core
from bot.core.timeutil import (
    RU_TIMEZONES,
    ParseError,
    fmt_date,
    fmt_time,
    monday_of,
    now_utc,
    parse_date,
    parse_time,
    tz_label,
)
from bot.models import User
from bot.telegram import kb
from bot.telegram.ui import answer_cb, show, today, week1

router = Router(name="settings")
NOT_MENU = ~F.text.in_(kb.MENU_BUTTONS) & ~F.text.startswith("/")


class SettingsInput(StatesGroup):
    digest_time = State()
    week1 = State()
    university = State()


def _on(v: bool) -> str:
    return "вкл" if v else "выкл"


async def _show_settings(event, user: User) -> None:
    w1 = week1(user)
    text = (
        "<b>Настройки</b>\n\n"
        f"🕗 Ежедневная сводка заданий: {fmt_time(user.digest_time) + ', ' if user.digest_enabled else ''}{_on(user.digest_enabled)}\n"
        f"🔔 Напоминания о заданиях: {_on(user.reminders_enabled)}\n"
        f"🌍 Часовой пояс: {tz_label(user.timezone)}\n"
        f"🔢 Первая (нечётная) неделя семестра начинается {fmt_date(w1, year=True)}\n"
        f"🏫 Вуз: {escape(user.university_name) if user.university_name else 'не указан'} — ручной режим"
    )
    b = InlineKeyboardBuilder()
    b.button(text="🕗 Сводка", callback_data=kb.Nav(to="set_digest"))
    b.button(text=f"🔔 Напоминания: {_on(user.reminders_enabled)}", callback_data=kb.Nav(to="set_rem_toggle"))
    b.button(text="🌍 Часовой пояс", callback_data=kb.Nav(to="set_tz"))
    b.button(text="🔢 Первая неделя", callback_data=kb.Nav(to="set_week1"))
    b.button(text="✏️ Расписание", callback_data=kb.Nav(to="sched"))
    b.button(text="📖 Предметы", callback_data=kb.Nav(to="subjects"))
    b.button(text="🏫 Подключить мой вуз", callback_data=kb.Nav(to="set_univ"))
    b.button(text="🗑 Удалить все мои данные", callback_data=kb.Nav(to="delete_me"))
    b.adjust(2, 2, 2, 1, 1)
    await show(event, text, b.as_markup())


@router.message(F.text == kb.SETTINGS, StateFilter("*"))
@router.message(Command("settings"), StateFilter("*"))
async def settings_cmd(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    await _show_settings(message, user)


@router.callback_query(kb.Nav.filter(F.to == "settings"), StateFilter("*"))
async def settings_cb(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.clear()
    await _show_settings(cb, user)


def _back(b: InlineKeyboardBuilder) -> None:
    b.button(text="⬅️ Настройки", callback_data=kb.Nav(to="settings"))


# ---- сводка ----

@router.callback_query(kb.Nav.filter(F.to == "set_digest"))
async def digest_menu(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    b = InlineKeyboardBuilder()
    b.button(text="Выключить сводку" if user.digest_enabled else "Включить сводку", callback_data=kb.Pick(kind="dg", value="toggle"))
    for t in ("07:00", "08:00", "18:00", "20:00", "21:00", "22:00"):
        # В данных кнопки нельзя двоеточие — время передаём как 0700.
        b.button(text=t, callback_data=kb.Pick(kind="dg", value=t.replace(":", "")))
    _back(b)
    b.adjust(1, 3, 3, 1)
    await state.set_state(SettingsInput.digest_time)
    await show(
        cb,
        "Каждый день в выбранное время я присылаю список невыполненных заданий. "
        "Если заданий нет — сводка не приходит.\n\nВыберите время или напишите своё, например <code>19:30</code>.\n"
        f"<i>Время — {tz_label(user.timezone)}.</i>",
        b.as_markup(),
    )


async def _set_digest_time(event, state: FSMContext, user: User, value: str) -> None:
    t = parse_time(value)
    if t != user.digest_time:
        user.digest_time = t
        # Уже прошедшее новое время сегодня (или вчера) считаем отработанным: сводка придёт
        # в ближайшее наступление нового времени, а не сразу.
        reminders.mark_digest(user, now_utc())
    user.digest_enabled = True
    await state.clear()
    await _show_settings(event, user)


@router.callback_query(SettingsInput.digest_time, kb.Pick.filter(F.kind == "dg"))
async def digest_pick(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, user: User) -> None:
    if callback_data.value == "toggle":
        user.digest_enabled = not user.digest_enabled
        await state.clear()
        await _show_settings(cb, user)
        return
    v = callback_data.value  # «0700»
    await _set_digest_time(cb, state, user, f"{v[:2]}:{v[2:]}")


@router.message(SettingsInput.digest_time, F.text, NOT_MENU)
async def digest_text(message: Message, state: FSMContext, user: User) -> None:
    try:
        await _set_digest_time(message, state, user, message.text)
    except ParseError as e:
        await message.answer(str(e))


# ---- напоминания ----

@router.callback_query(kb.Nav.filter(F.to == "set_rem_toggle"))
async def rem_toggle(cb: CallbackQuery, user: User) -> None:
    user.reminders_enabled = not user.reminders_enabled
    await _show_settings(cb, user)


# ---- часовой пояс ----

@router.callback_query(kb.Nav.filter(F.to == "set_tz"))
async def tz_menu(cb: CallbackQuery, user: User) -> None:
    b = InlineKeyboardBuilder()
    for i, (label, name) in enumerate(RU_TIMEZONES):
        mark = "✅ " if name == user.timezone else ""
        b.button(text=mark + label, callback_data=kb.Pick(kind="tz", value=str(i)))
    _back(b)
    b.adjust(1)
    await show(cb, "Выберите часовой пояс. Напоминания будут приходить по местному времени.", b.as_markup())


@router.callback_query(kb.Pick.filter(F.kind == "tz"))
async def tz_pick(cb: CallbackQuery, callback_data: kb.Pick, session: AsyncSession, user: User) -> None:
    _, name = RU_TIMEZONES[int(callback_data.value)]
    await users_core.set_timezone(session, user, name)
    await _show_settings(cb, user)


# ---- первая неделя ----

@router.callback_query(kb.Nav.filter(F.to == "set_week1"))
async def week1_menu(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.set_state(SettingsInput.week1)
    b = InlineKeyboardBuilder()
    b.button(text="По умолчанию (неделя с 1 сентября)", callback_data=kb.Pick(kind="w1", value="default"))
    _back(b)
    b.adjust(1)
    await show(
        cb,
        "От этой недели считается чётность: она первая (нечётная), следующая — чётная и так далее.\n\n"
        "Напишите любую дату первой учебной недели, например <code>01.09.2026</code> "
        "или <code>09.02.2027</code> для весеннего семестра.",
        b.as_markup(),
    )


@router.callback_query(SettingsInput.week1, kb.Pick.filter(F.kind == "w1"))
async def week1_default(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    user.week1_start = None
    await state.clear()
    await _show_settings(cb, user)


@router.message(SettingsInput.week1, F.text, NOT_MENU)
async def week1_text(message: Message, state: FSMContext, user: User) -> None:
    try:
        # Начало семестра обычно уже в прошлом: «01.09» в ноябре — это 1 сентября этого года.
        d = parse_date(message.text, today(user), mode="nearest")
    except ParseError as e:
        await message.answer(str(e))
        return
    user.week1_start = monday_of(d)
    await state.clear()
    await _show_settings(message, user)


# ---- вуз ----

@router.callback_query(kb.Nav.filter(F.to == "set_univ"))
async def univ_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(SettingsInput.university)
    b = InlineKeyboardBuilder()
    _back(b)
    await show(
        cb,
        "Автоматическое подключение к системам вузов пока в разработке. "
        "Напишите название вашего вуза — я запишу заявку, и вузы с наибольшим числом заявок подключим первыми.",
        b.as_markup(),
    )


@router.message(SettingsInput.university, F.text, NOT_MENU)
async def univ_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    added = await users_core.add_university_request(session, user, message.text)
    await state.clear()
    await message.answer("Заявка записана, спасибо!" if added else "Вы уже оставляли заявку на этот вуз — она учтена.")
    await _show_settings(message, user)


# ---- удаление данных ----

@router.message(Command("delete_me"), StateFilter("*"))
@router.callback_query(kb.Nav.filter(F.to == "delete_me"), StateFilter("*"))
async def delete_me(event: Message | CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    b = InlineKeyboardBuilder()
    b.button(text="Да, удалить всё", callback_data=kb.Nav(to="delete_me_ok"))
    b.button(text="Нет", callback_data=kb.Nav(to="settings"))
    await show(
        event,
        "Удалить <b>все</b> ваши данные: предметы, расписание, задания, напоминания и настройки? "
        "Восстановить их будет нельзя.",
        b.as_markup(),
    )


@router.callback_query(kb.Nav.filter(F.to == "delete_me_ok"))
async def delete_me_ok(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()  # вместе с базой забываем и недописанный ввод
    await users_core.delete_user_data(session, user)
    await session.commit()  # удаляем до ответа в Telegram: сбой ответа не должен вернуть данные
    if isinstance(cb.message, Message):
        await show(cb, "Все ваши данные удалены.")
    else:
        await answer_cb(cb, "Все ваши данные удалены.")
    if cb.message is not None:
        await cb.message.answer("Чтобы начать заново, нажмите /start.", reply_markup=ReplyKeyboardRemove())
