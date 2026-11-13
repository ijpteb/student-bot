"""Старт, меню, помощь, отмена, служебные команды."""

from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InaccessibleMessage, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core import render
from bot.core.timeutil import tz_label
from bot.core.users import add_university_request, request_counts
from bot.models import Homework, Reminder, User
from bot.telegram import kb
from bot.telegram.ui import answer_cb, show

router = Router(name="common")

# Слова, которыми студенты пытаются выйти из ввода вместо кнопки «✖️ Отмена».
CANCEL_WORDS = {"отмена", "отменить", "назад", "стоп", "выход", "выйти", "cancel"}
# Сообщения, которые пишет сам студент (а не служебные: закрепление, таймер удаления и т. п.).
USER_CONTENT = (
    F.text | F.photo | F.document | F.video | F.voice | F.audio | F.sticker | F.video_note
    | F.animation | F.location | F.contact | F.poll | F.dice | F.venue
)

HELP = (
    "<b>Что умеет бот</b>\n\n"
    "📅 <b>Расписание</b> — на сегодня, завтра, неделю и ближайшая пара. "
    "Пары вносятся в ⚙️ Настройки → ✏️ Расписание; можно указать чётность недели, "
    "разовые пары и отмены на конкретный день.\n\n"
    "📚 <b>Задания</b> — по предметам и по срокам, с фото и файлами. "
    "Напоминание приходит в указанное вами время, а каждый вечер — сводка невыполненного "
    "(время сводки меняется в настройках). Время — по вашему часовому поясу: "
    "⚙️ Настройки → 🌍 Часовой пояс.\n\n"
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


async def _finish_onboarding(message: Message | InaccessibleMessage, user: User) -> None:
    user.onboarded = True
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Внести расписание", callback_data=kb.Nav(to="sched"))
    b.button(text="➕ Добавить задание", callback_data=kb.Nav(to="add_hw"))
    b.button(text="🌍 Сменить часовой пояс", callback_data=kb.Nav(to="set_tz"))
    b.adjust(1)
    await message.answer(
        f"Время напоминаний и сводки считаю по часовому поясу: <b>{tz_label(user.timezone)}</b>. "
        "Если вы в другом поясе — смените его кнопкой ниже или потом в ⚙️ Настройках.\n\n"
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
    if isinstance(cb.message, Message):
        try:
            await cb.message.edit_reply_markup()
        except TelegramBadRequest:
            pass
    await answer_cb(cb)
    if cb.message is not None:
        await _finish_onboarding(cb.message, user)


@router.message(Command("help"), StateFilter("*"))
async def help_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(HELP, reply_markup=kb.main_menu())


@router.message(Command("cancel"), StateFilter("*"))
async def cancel_cmd(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено.", reply_markup=kb.main_menu())


@router.message(StateFilter("*"), F.text, F.text.func(lambda t: t.strip().lower().rstrip(".!") in CANCEL_WORDS))
async def cancel_word(message: Message, state: FSMContext) -> None:
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
    await answer_cb(cb)


# ---- Служебные команды (только для владельца бота) ----

def _is_admin(message: Message, admin_id: int | None) -> bool:
    return admin_id is not None and message.from_user.id == admin_id


# Проверка — в фильтре: для остальных команда ведёт себя как несуществующая («Не понял»).
@router.message(Command("stats"), StateFilter("*"), _is_admin)
async def stats(message: Message, session: AsyncSession) -> None:
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


# Альбом приходит отдельными сообщениями — на него отвечаем один раз, а не на каждое фото.
_answered_album: dict[int, str] = {}


def _first_of_album(message: Message) -> bool:
    group = message.media_group_id
    if not group:
        return True
    if _answered_album.get(message.chat.id) == group:
        return False
    _answered_album[message.chat.id] = group
    return True


@fallback_router.message(StateFilter(None), F.photo | F.document)
async def fallback_file(message: Message) -> None:
    if not _first_of_album(message):
        return
    await message.answer(
        "Чтобы сохранить фото или файл как задание, нажмите «➕ Задание» и пришлите его, "
        "когда я попрошу текст задания.",
        reply_markup=kb.main_menu(),
    )


@fallback_router.message(StateFilter(None), USER_CONTENT)
async def fallback_other(message: Message) -> None:
    if not _first_of_album(message):
        return
    await message.answer("Не понял 🙂 Выберите действие в меню внизу или нажмите /help.", reply_markup=kb.main_menu())


@fallback_router.message(USER_CONTENT)
async def fallback_in_dialog(message: Message) -> None:
    """Посреди диалога пришло то, чего этот шаг не ждёт."""
    if not _first_of_album(message):
        return
    if message.text and message.text.startswith("/"):
        await message.answer("Такой команды нет. Выйти из ввода — /cancel, список команд — /help.")
    elif message.text:
        await message.answer("Не понял 🙂 Ответьте на вопрос выше или нажмите «✖️ Отмена».")
    elif message.photo or message.document:
        await message.answer(
            "Это фото или файл не сохранено: сейчас я жду ответ на вопрос выше. "
            "Фото и файлы можно прислать, когда я попрошу текст задания (и на следующих шагах)."
        )
    else:
        await message.answer(
            "Видео, голосовые и стикеры сохранить не получится. "
            "Ответьте на вопрос выше текстом или кнопкой. Выйти из ввода — /cancel."
        )


@fallback_router.callback_query()
async def stale_button(cb: CallbackQuery) -> None:
    """Кнопка из старого сообщения или из уже пройденного шага."""
    await answer_cb(cb, "Эта кнопка уже не действует (действие выполнено или устарело).")
