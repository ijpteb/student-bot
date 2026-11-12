"""Старт, меню, помощь, отмена, служебные команды."""

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core import render
from bot.core.users import add_university_request, request_counts
from bot.models import Homework, Reminder, User
from bot.telegram import kb
from bot.telegram.ui import show

router = Router(name="common")

HELP = (
    "<b>Что умеет бот</b>\n\n"
    "📅 <b>Расписание</b> — на сегодня, завтра, неделю и ближайшая пара. "
    "Пары вносятся в ⚙️ Настройки → ✏️ Расписание; можно указать чётность недели, "
    "разовые пары и отмены на конкретный день.\n\n"
    "📚 <b>Задания</b> — по предметам и по срокам, с фото и файлами. "
    "Напоминание приходит в указанное вами время, а каждый вечер — сводка невыполненного "
    "(время меняется в настройках).\n\n"
    "Команды: /today /tomorrow /week /next /homework /add /settings /cancel\n\n"
    f"<i>{render.DISCLAIMER}</i>"
)


class Onboarding(StatesGroup):
    university = State()


@router.message(CommandStart(), StateFilter("*"))
async def start(message: Message, state: FSMContext, user: User) -> None:
    await state.clear()
    if user.onboarded:
        await message.answer("С возвращением! Меню — внизу экрана.", reply_markup=kb.main_menu())
        return
    await message.answer(
        "👋 Привет! Я помогаю студентам: показываю расписание, храню домашние задания "
        "и напоминаю о них.\n\n"
        f"<i>{render.DISCLAIMER}</i>\n\n"
        "Сейчас вузы работают в <b>ручном режиме</b>: предметы и расписание вы вносите сами, "
        "задания и напоминания работают полностью.",
        reply_markup=kb.main_menu(),
    )
    b = InlineKeyboardBuilder()
    b.button(text="Пропустить", callback_data=kb.Nav(to="onb_skip"))
    await message.answer(
        "В каком вузе вы учитесь? Напишите название — так я пойму, какие вузы подключать "
        "к боту в первую очередь.",
        reply_markup=b.as_markup(),
    )
    await state.set_state(Onboarding.university)


async def _finish_onboarding(message: Message, user: User) -> None:
    user.onboarded = True
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Внести расписание", callback_data=kb.Nav(to="sched"))
    b.button(text="➕ Добавить задание", callback_data=kb.Nav(to="add_hw"))
    b.adjust(1)
    await message.answer(
        "С чего начнём? Расписание можно внести сейчас или позже — в ⚙️ Настройках.",
        reply_markup=b.as_markup(),
    )


@router.message(Onboarding.university, F.text, ~F.text.in_(kb.MENU_BUTTONS), ~F.text.startswith("/"))
async def onboarding_university(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await add_university_request(session, user, message.text)
    await state.clear()
    await message.answer("Спасибо! Записал заявку на подключение вашего вуза.")
    await _finish_onboarding(message, user)


@router.callback_query(kb.Nav.filter(F.to == "onb_skip"))
async def onboarding_skip(cb: CallbackQuery, state: FSMContext, user: User) -> None:
    await state.clear()
    await cb.message.edit_reply_markup()
    await cb.answer()
    await _finish_onboarding(cb.message, user)


@router.message(Command("help"), StateFilter("*"))
async def help_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP, reply_markup=kb.main_menu())


@router.message(Command("cancel"), StateFilter("*"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=kb.main_menu())


@router.callback_query(kb.Nav.filter(F.to == "cancel"), StateFilter("*"))
async def cancel_cb(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await show(cb, "Отменено.")


@router.message(Command("id"), StateFilter("*"))
async def my_id(message: Message) -> None:
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.callback_query(kb.Nav.filter(F.to == "noop"))
async def noop(cb: CallbackQuery) -> None:
    await cb.answer()


# ---- Служебные команды (только для владельца бота) ----

def _is_admin(message: Message, admin_id: int | None) -> bool:
    return admin_id is not None and message.from_user.id == admin_id


@router.message(Command("stats"), StateFilter("*"))
async def stats(message: Message, session: AsyncSession, admin_id: int | None) -> None:
    if not _is_admin(message, admin_id):
        return  # для остальных команды как будто нет
    users = await session.scalar(select(func.count()).select_from(User))
    blocked = await session.scalar(select(func.count()).select_from(User).where(User.blocked.is_(True)))
    hw = await session.scalar(select(func.count()).select_from(Homework))
    hw_open = await session.scalar(select(func.count()).select_from(Homework).where(Homework.done.is_(False)))
    pending = await session.scalar(select(func.count()).select_from(Reminder).where(Reminder.sent_at.is_(None)))
    lines = [
        "<b>Статистика</b>",
        f"Пользователей: {users} (заблокировали бота: {blocked})",
        f"Заданий: {hw} (невыполненных: {hw_open})",
        f"Ждут отправки напоминаний: {pending}",
        "",
        "<b>Заявки на подключение вузов</b>",
    ]
    counts = await request_counts(session)
    lines += [f"{n} — {escape(name)}" for name, n in counts] or ["пока нет"]
    await message.answer("\n".join(lines))


fallback_router = Router(name="fallback")  # подключается последним


@fallback_router.message(StateFilter(None), F.text)
async def fallback(message: Message) -> None:
    await message.answer("Не понял 🙂 Выберите действие в меню внизу или нажмите /help.", reply_markup=kb.main_menu())
