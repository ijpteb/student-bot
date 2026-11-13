"""Домашние задания: добавить, посмотреть, отметить, изменить, удалить."""

from datetime import date, datetime, time, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core import homework as hw_core
from bot.core import reminders, render
from bot.core import schedule as sch
from bot.core import subjects as subj
from bot.core.timeutil import ParseError, fmt_date, fmt_dt, fmt_time, parse_date_time, parse_moment, tz_label
from bot.models import Homework, User
from bot.telegram import kb
from bot.telegram.notifier import TelegramNotifier
from bot.telegram.ui import answer_cb, now_l, show, today, week1

router = Router(name="homework")
NOT_MENU = ~F.text.in_(kb.MENU_BUTTONS) & ~F.text.startswith("/")
FILES = F.photo | F.document
# Время в данных кнопки — без двоеточий: двоеточие служит разделителем.
REM_FMT = "%Y%m%d%H%M"
# Длинный текст Telegram сам режет на несколько сообщений, они приходят подряд.
# Части, пришедшие в течение этих секунд после отклонённой, — продолжение той же вставки.
PASTE_WINDOW_S = 5
NOT_FOUND = "Задание не найдено — возможно, оно удалено."


class AddHw(StatesGroup):
    subject = State()
    new_subject = State()
    text = State()
    due = State()
    reminder = State()


class EditHw(StatesGroup):
    text = State()
    due = State()
    reminder = State()


def _file_of(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return ("photo", message.photo[-1].file_id)
    if message.document:
        return ("document", message.document.file_id)
    return None


def _text_of(message: Message) -> str:
    """Текст или подпись + адреса скрытых ссылок (Telegram передаёт их отдельно от текста)."""
    text = message.text or message.caption or ""
    entities = (message.entities if message.text is not None else message.caption_entities) or []
    links = list(dict.fromkeys(e.url for e in entities if e.type == "text_link" and e.url and e.url not in text))
    return "\n".join([text, *links]) if links else text


async def _checked_text(message: Message, state: FSMContext) -> str | None:
    """Текст задания с проверкой длины. None — текст отклонён (или это хвост отклонённой вставки)."""
    sent = message.date.timestamp()
    too_long_at = (await state.get_data()).get("too_long_at")
    if message.text and too_long_at is not None and sent - too_long_at <= PASTE_WINDOW_S:
        await state.update_data(too_long_at=sent)  # продолжение длинной вставки — не сохраняем
        return None
    try:
        text = hw_core.clean_text(_text_of(message))
    except hw_core.HomeworkError as e:
        await state.update_data(too_long_at=sent)
        await message.answer(f"{e} Пришлите сокращённый текст одним сообщением.")
        return None
    await state.update_data(too_long_at=None)
    return text


async def _subject_names(session: AsyncSession, user: User) -> dict[int, str]:
    return {s.id: s.name for s in await subj.list_subjects(session, user.id)}


# ---------------- Добавление ----------------

async def _start_add(event, state: FSMContext, session: AsyncSession, user: User, subject_id: int | None = None) -> None:
    await state.clear()
    if subject_id is not None:
        await state.update_data(subject_id=subject_id)
        await _ask_text(event, state)
        return
    subjects = await subj.list_subjects(session, user.id)
    b = InlineKeyboardBuilder()
    if not subjects:
        await state.set_state(AddHw.new_subject)
        kb.cancel_row(b)
        await show(event, "По какому предмету задание? Напишите название предмета.", b.as_markup())
        return
    await state.set_state(AddHw.subject)
    for s in subjects:
        b.button(text=s.name, callback_data=kb.Pick(kind="hwsubj", value=str(s.id)))
    b.button(text="➕ Новый предмет", callback_data=kb.Pick(kind="hwsubj", value="new"))
    kb.cancel_row(b)
    b.adjust(1)
    await show(event, "По какому предмету задание? Выберите кнопкой или напишите название.", b.as_markup())


@router.message(F.text == kb.ADD_HW, StateFilter("*"))
@router.message(Command("add"), StateFilter("*"))
async def add_cmd(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await _start_add(message, state, session, user)


@router.callback_query(kb.Nav.filter(F.to == "add_hw"), StateFilter("*"))
async def add_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await _start_add(cb, state, session, user)


@router.callback_query(kb.SubjCb.filter(F.action == "addhw"), StateFilter("*"))
async def add_for_subject(cb: CallbackQuery, callback_data: kb.SubjCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    s = await subj.get_subject(session, user.id, callback_data.id)
    await _start_add(cb, state, session, user, s.id if s else None)


@router.callback_query(AddHw.subject, kb.Pick.filter(F.kind == "hwsubj"))
async def add_subject(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, session: AsyncSession, user: User) -> None:
    if callback_data.value == "new":
        await state.set_state(AddHw.new_subject)
        b = InlineKeyboardBuilder()
        kb.cancel_row(b)
        await show(cb, "Напишите название предмета.", b.as_markup())
        return
    s = await subj.get_subject(session, user.id, int(callback_data.value))
    if s is None:
        await answer_cb(cb, "Предмет не найден — возможно, его удалили.", show_alert=True)
        return
    await state.update_data(subject_id=s.id)
    await _ask_text(cb, state)


@router.message(AddHw.new_subject, F.text, NOT_MENU)
@router.message(AddHw.subject, F.text, NOT_MENU)  # название можно и написать, а не выбрать кнопкой
async def add_new_subject(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        s = await subj.find_or_create(session, user.id, message.text)
    except subj.SubjectError as e:
        await message.answer(str(e))
        return
    await state.update_data(subject_id=s.id)
    await _ask_text(message, state)


async def _ask_text(event, state: FSMContext) -> None:
    await state.set_state(AddHw.text)
    b = InlineKeyboardBuilder()
    kb.cancel_row(b)
    await show(event, "Напишите задание. Можно прислать фото или файл — с подписью или без.", b.as_markup())


@router.message(AddHw.text, F.text, NOT_MENU)
@router.message(AddHw.text, FILES)
async def add_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    text = await _checked_text(message, state)
    if text is None:
        return
    f = _file_of(message)
    await state.update_data(text=text, files=[list(f)] if f else [])
    await _ask_due(message, state, session, user)


async def _due_markup(session: AsyncSession, user: User, subject_id: int):
    b = InlineKeyboardBuilder()
    s = await sch.load(session, user.id, week1(user))
    nxt = s.next_for_subject(subject_id, now_l(user))
    if nxt:
        b.button(
            text=f"К следующей паре ({fmt_date(nxt.day, True)}, {fmt_time(nxt.start)})",
            callback_data=kb.Pick(kind="due", value=f"{nxt.day.isoformat()}T{nxt.start.strftime('%H%M')}"),
        )
    t = today(user)
    b.button(text="Завтра", callback_data=kb.Pick(kind="due", value=(t + timedelta(days=1)).isoformat()))
    b.button(text="Через неделю", callback_data=kb.Pick(kind="due", value=(t + timedelta(days=7)).isoformat()))
    b.button(text="Без срока", callback_data=kb.Pick(kind="due", value="none"))
    kb.cancel_row(b)
    b.adjust(1, 2, 1, 1) if nxt else b.adjust(2, 1, 1)
    return b.as_markup()


DUE_HINT = "Можно написать дату и время: <code>15.11</code> или <code>15.11 18:00</code>."


async def _ask_due(event, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.set_state(AddHw.due)
    data = await state.get_data()
    n = len(data.get("files", []))
    files_note = f"📎 Вложений: {n}. " if n else ""
    await show(
        event,
        f"{files_note}Чтобы приложить ещё фото или файл — просто пришлите его сейчас.\n\n"
        f"<b>К какому сроку сдать?</b>\n{DUE_HINT}",
        await _due_markup(session, user, data["subject_id"]),
    )


@router.message(AddHw.due, FILES)
@router.message(AddHw.reminder, FILES)
async def add_more_files(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    files = data.get("files", [])
    files.append(list(_file_of(message)))
    update = {"files": files}
    if not data.get("text") and message.caption:  # подпись могла оказаться не на первом фото альбома
        update["text"] = _text_of(message)[: hw_core.MAX_TEXT]
    await state.update_data(**update)
    step = "срок" if await state.get_state() == AddHw.due.state else "время напоминания"
    await message.answer(f"📎 Добавлено. Вложений: {len(files)}. Теперь выберите {step} кнопкой выше или напишите.")


def _parse_due_value(value: str) -> tuple[date | None, time | None]:
    if value == "none":
        return None, None
    if "T" in value:
        d, t = value.split("T")
        return date.fromisoformat(d), time(int(t[:2]), int(t[2:]))
    return date.fromisoformat(value), None


@router.callback_query(AddHw.due, kb.Pick.filter(F.kind == "due"))
async def add_due_pick(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, user: User) -> None:
    d, t = _parse_due_value(callback_data.value)
    await _after_due(cb, state, user, d, t)


@router.message(AddHw.due, F.text, NOT_MENU)
async def add_due_text(message: Message, state: FSMContext, user: User) -> None:
    try:
        d, t = parse_date_time(message.text, today(user))
    except ParseError as e:
        await message.answer(str(e))
        return
    await _after_due(message, state, user, d, t)


async def _after_due(event, state: FSMContext, user: User, d: date | None, t: time | None) -> None:
    await state.update_data(due_date=d.isoformat() if d else None, due_time=t.isoformat() if t else None)
    await state.set_state(AddHw.reminder)
    await show(event, *_reminder_prompt(user, d, t))


def _reminder_prompt(user: User, due: date | None, due_time: time | None, note: str = ""):
    b = InlineKeyboardBuilder()
    for label, dt in reminders.reminder_presets(due, due_time, now_l(user)):
        b.button(text=f"{label} ({fmt_date(dt.date())})", callback_data=kb.Pick(kind="rem", value=dt.strftime(REM_FMT)))
    b.button(text="Без напоминания", callback_data=kb.Pick(kind="rem", value="none"))
    kb.cancel_row(b)
    b.adjust(1)
    text = note
    if not user.reminders_enabled:
        text += "⚠️ <b>Напоминания выключены</b> в ⚙️ Настройках — пока они выключены, напоминания не приходят.\n\n"
    text += (
        "<b>Когда напомнить?</b>\n"
        "Напишите дату и время (<code>14.11 19:30</code>, <code>завтра 9:00</code>) "
        "или только время (<code>19:30</code> — сегодня, а если уже прошло — завтра).\n"
        f"<i>Время — {tz_label(user.timezone)}. Сменить: ⚙️ Настройки → 🌍 Часовой пояс.</i>"
    )
    return text, b.as_markup()


def _parse_reminder(text: str, user: User) -> datetime:
    now = now_l(user)
    moment = parse_moment(text, now)
    if moment <= now:
        raise ParseError("Это время уже прошло. Укажите время в будущем.")
    return moment


def _reminder_note(user: User, moment: datetime | None) -> str:
    if moment is None:
        return ""
    note = f"⏰ Напомню {fmt_dt(moment)} (часовой пояс — {tz_label(user.timezone)})."
    if not user.reminders_enabled:
        note += " ⚠️ Но напоминания сейчас выключены в ⚙️ Настройках — включите их, иначе оно не придёт."
    return note


async def _picked_moment(cb: CallbackQuery, callback_data: kb.Pick, user: User, due: date | None, due_time: time | None):
    """Время из кнопки. Кнопку могли нажать позже, когда это время уже прошло. False — не годится."""
    if callback_data.value == "none":
        return None
    moment = datetime.strptime(callback_data.value, REM_FMT)
    if moment <= now_l(user):
        await show(cb, *_reminder_prompt(user, due, due_time, note="Это время уже прошло — выберите другое.\n\n"))
        return False
    return moment


@router.callback_query(AddHw.reminder, kb.Pick.filter(F.kind == "rem"))
async def add_rem_pick(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    due = date.fromisoformat(data["due_date"]) if data.get("due_date") else None
    due_time = time.fromisoformat(data["due_time"]) if data.get("due_time") else None
    moment = await _picked_moment(cb, callback_data, user, due, due_time)
    if moment is not False:
        await _save_new(cb, state, session, user, moment)


@router.message(AddHw.reminder, F.text, NOT_MENU)
async def add_rem_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        moment = _parse_reminder(message.text, user)
    except ParseError as e:
        await message.answer(str(e))
        return
    await _save_new(message, state, session, user, moment)


async def _save_new(event, state: FSMContext, session: AsyncSession, user: User, moment: datetime | None) -> None:
    data = await state.get_data()
    await state.clear()
    try:
        hw = await hw_core.add(
            session,
            user.id,
            data["subject_id"],
            data.get("text", ""),
            date.fromisoformat(data["due_date"]) if data.get("due_date") else None,
            time.fromisoformat(data["due_time"]) if data.get("due_time") else None,
            attachments=[tuple(f) for f in data.get("files", [])],
        )
    except hw_core.HomeworkError:  # предмет удалили, пока шёл диалог
        await show(event, "Предмет не найден — возможно, его удалили. Начните заново: «➕ Задание».")
        return
    await reminders.set_reminder(session, user, hw, moment)
    note = _reminder_note(user, moment)
    await _show_card(event, session, user, hw, prefix="✅ Задание сохранено. " + (note + "\n\n" if note else "\n\n"))


# ---------------- Карточка задания ----------------

async def _show_card(event, session: AsyncSession, user: User, hw: Homework, prefix: str = "", toast: str | None = None) -> None:
    names = await _subject_names(session, user)
    rem = await reminders.get_reminder(session, hw.id)
    files = await hw_core.attachments(session, hw)
    b = InlineKeyboardBuilder()
    if hw.done:
        b.button(text="↩️ Не выполнено", callback_data=kb.HwCb(action="undone", id=hw.id))
    else:
        b.button(text="✅ Выполнено", callback_data=kb.HwCb(action="done", id=hw.id))
    b.button(text="✏️ Текст", callback_data=kb.HwCb(action="etext", id=hw.id))
    b.button(text="📅 Срок", callback_data=kb.HwCb(action="edue", id=hw.id))
    if not hw.done:  # о выполненном не напоминаем
        b.button(text="⏰ Напоминание", callback_data=kb.HwCb(action="erem", id=hw.id))
    b.button(text="📚 Предмет", callback_data=kb.HwCb(action="esubj", id=hw.id))
    if files:
        b.button(text=f"📎 Показать вложения ({len(files)})", callback_data=kb.HwCb(action="files", id=hw.id))
    b.button(text="🗑 Удалить", callback_data=kb.HwCb(action="del", id=hw.id))
    b.button(text="⬅️ К списку", callback_data=kb.Nav(to="hw_dates"))
    b.adjust(1, 2, 2, 1, 1, 1, 1)
    text = prefix + render.hw_card(
        hw, names.get(hw.subject_id, "?"), rem.local_at if rem else None, len(files), user.reminders_enabled
    )
    await show(event, text, b.as_markup(), toast=toast)


async def _get_hw(cb: CallbackQuery, session: AsyncSession, user: User, hw_id: int) -> Homework | None:
    hw = await hw_core.get(session, user.id, hw_id)
    if hw is None:
        await answer_cb(cb, NOT_FOUND, show_alert=True)
    return hw


@router.callback_query(kb.HwCb.filter(F.action == "open"), StateFilter("*"))
async def hw_open(cb: CallbackQuery, callback_data: kb.HwCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    if hw := await _get_hw(cb, session, user, callback_data.id):
        await _show_card(cb, session, user, hw)


@router.callback_query(kb.HwCb.filter(F.action.in_({"done", "undone"})))
async def hw_done(cb: CallbackQuery, callback_data: kb.HwCb, session: AsyncSession, user: User) -> None:
    if hw := await _get_hw(cb, session, user, callback_data.id):
        await hw_core.set_done(hw, callback_data.action == "done")
        await _show_card(cb, session, user, hw)


@router.callback_query(kb.HwCb.filter(F.action == "files"))
async def hw_files(cb: CallbackQuery, callback_data: kb.HwCb, session: AsyncSession, user: User, notifier: TelegramNotifier) -> None:
    if hw := await _get_hw(cb, session, user, callback_data.id):
        await answer_cb(cb)
        await notifier.send_attachments(user, await hw_core.attachments(session, hw))


@router.callback_query(kb.HwCb.filter(F.action == "del"))
async def hw_del(cb: CallbackQuery, callback_data: kb.HwCb, session: AsyncSession, user: User) -> None:
    if hw := await _get_hw(cb, session, user, callback_data.id):
        b = InlineKeyboardBuilder()
        b.button(text="Да, удалить", callback_data=kb.HwCb(action="delok", id=hw.id))
        b.button(text="Нет", callback_data=kb.HwCb(action="open", id=hw.id))
        await show(cb, "Удалить задание?", b.as_markup())


@router.callback_query(kb.HwCb.filter(F.action == "delok"))
async def hw_delok(cb: CallbackQuery, callback_data: kb.HwCb, session: AsyncSession, user: User) -> None:
    if hw := await _get_hw(cb, session, user, callback_data.id):
        await hw_core.delete(session, hw)
        await _show_by_dates(cb, session, user, toast="Удалено")


# ---------------- Изменение ----------------

@router.callback_query(kb.HwCb.filter(F.action == "etext"), StateFilter("*"))
async def edit_text(cb: CallbackQuery, callback_data: kb.HwCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    if hw := await _get_hw(cb, session, user, callback_data.id):
        await state.clear()
        await state.set_state(EditHw.text)
        await state.update_data(hw_id=hw.id)
        b = InlineKeyboardBuilder()
        kb.cancel_row(b)
        await show(cb, "Напишите новый текст задания.", b.as_markup())


@router.message(EditHw.text, F.text, NOT_MENU)
async def edit_text_msg(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    text = await _checked_text(message, state)
    if text is None:
        return  # остаёмся на этом шаге — можно прислать текст ещё раз
    hw = await hw_core.get(session, user.id, (await state.get_data())["hw_id"])
    await state.clear()
    if hw is None:
        await message.answer(NOT_FOUND)
        return
    hw.text = text
    await _show_card(message, session, user, hw, prefix="Сохранено.\n\n")


@router.callback_query(kb.HwCb.filter(F.action == "edue"), StateFilter("*"))
async def edit_due(cb: CallbackQuery, callback_data: kb.HwCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    if hw := await _get_hw(cb, session, user, callback_data.id):
        await state.clear()
        await state.set_state(EditHw.due)
        await state.update_data(hw_id=hw.id)
        await show(cb, f"<b>Новый срок?</b>\n{DUE_HINT}", await _due_markup(session, user, hw.subject_id))


async def _apply_due(event, state: FSMContext, session: AsyncSession, user: User, d, t) -> None:
    hw = await hw_core.get(session, user.id, (await state.get_data())["hw_id"])
    await state.clear()
    if hw is None:
        await show(event, NOT_FOUND)
        return
    hw.due_date, hw.due_time = d, t
    note = "Срок изменён."
    if await reminders.get_reminder(session, hw.id):
        note += " Напоминание осталось прежним — при необходимости поменяйте его."
    await _show_card(event, session, user, hw, prefix=note + "\n\n")


@router.callback_query(EditHw.due, kb.Pick.filter(F.kind == "due"))
async def edit_due_pick(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, session: AsyncSession, user: User) -> None:
    d, t = _parse_due_value(callback_data.value)
    await _apply_due(cb, state, session, user, d, t)


@router.message(EditHw.due, F.text, NOT_MENU)
async def edit_due_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        d, t = parse_date_time(message.text, today(user))
    except ParseError as e:
        await message.answer(str(e))
        return
    await _apply_due(message, state, session, user, d, t)


DONE_NO_REMINDER = (
    "Задание отмечено выполненным — о нём я не напоминаю. "
    "Нажмите «↩️ Не выполнено», если напоминание всё-таки нужно."
)


@router.callback_query(kb.HwCb.filter(F.action == "erem"), StateFilter("*"))
async def edit_rem(cb: CallbackQuery, callback_data: kb.HwCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    if hw := await _get_hw(cb, session, user, callback_data.id):
        await state.clear()
        if hw.done:
            await _show_card(cb, session, user, hw, prefix=DONE_NO_REMINDER + "\n\n")
            return
        await state.set_state(EditHw.reminder)
        await state.update_data(hw_id=hw.id)
        await show(cb, *_reminder_prompt(user, hw.due_date, hw.due_time))


async def _apply_rem(event, state: FSMContext, session: AsyncSession, user: User, moment) -> None:
    hw = await hw_core.get(session, user.id, (await state.get_data())["hw_id"])
    await state.clear()
    if hw is None:
        await show(event, NOT_FOUND)
        return
    if moment is not None and hw.done:
        await _show_card(event, session, user, hw, prefix=DONE_NO_REMINDER + "\n\n")
        return
    await reminders.set_reminder(session, user, hw, moment)
    note = _reminder_note(user, moment) or "Напоминание убрано."
    await _show_card(event, session, user, hw, prefix=note + "\n\n")


@router.callback_query(EditHw.reminder, kb.Pick.filter(F.kind == "rem"))
async def edit_rem_pick(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, session: AsyncSession, user: User) -> None:
    hw = await hw_core.get(session, user.id, (await state.get_data())["hw_id"])
    moment = await _picked_moment(cb, callback_data, user, hw.due_date if hw else None, hw.due_time if hw else None)
    if moment is not False:
        await _apply_rem(cb, state, session, user, moment)


@router.message(EditHw.reminder, F.text, NOT_MENU)
async def edit_rem_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        moment = _parse_reminder(message.text, user)
    except ParseError as e:
        await message.answer(str(e))
        return
    await _apply_rem(message, state, session, user, moment)


@router.callback_query(kb.HwCb.filter(F.action == "esubj"))
async def edit_subj(cb: CallbackQuery, callback_data: kb.HwCb, session: AsyncSession, user: User) -> None:
    if hw := await _get_hw(cb, session, user, callback_data.id):
        others = [s for s in await subj.list_subjects(session, user.id) if s.id != hw.subject_id]
        b = InlineKeyboardBuilder()
        for s in others:
            b.button(text=s.name, callback_data=kb.HwCb(action=f"mv{s.id}", id=hw.id))
        b.button(text="⬅️ Назад", callback_data=kb.HwCb(action="open", id=hw.id))
        b.adjust(1)
        if others:
            text = "Перенести задание в предмет:"
        else:
            text = (
                "Других предметов пока нет. Добавьте предмет: ⚙️ Настройки → 📖 Предметы → "
                "➕ Новый предмет — и сможете перенести задание."
            )
        await show(cb, text, b.as_markup())


@router.callback_query(kb.HwCb.filter(F.action.startswith("mv")))
async def edit_subj_apply(cb: CallbackQuery, callback_data: kb.HwCb, session: AsyncSession, user: User) -> None:
    hw = await _get_hw(cb, session, user, callback_data.id)
    if hw is None:
        return
    s = await subj.get_subject(session, user.id, int(callback_data.action[2:]))
    if s is None:
        await answer_cb(cb, "Предмет не найден — возможно, его удалили.", show_alert=True)
        return
    hw.subject_id = s.id
    await _show_card(cb, session, user, hw)


# ---------------- Списки ----------------

def _list_nav(b: InlineKeyboardBuilder, current: str) -> None:
    for to, label in (("hw_dates", "📅 По срокам"), ("hw_subjects", "📚 По предметам"), ("hw_done", "✅ Выполненные")):
        if to != current:
            b.button(text=label, callback_data=kb.Nav(to=to))
    b.button(text="➕ Добавить", callback_data=kb.Nav(to="add_hw"))


async def _show_by_dates(event, session: AsyncSession, user: User, toast: str | None = None) -> None:
    items = await hw_core.list_open(session, user.id)
    names = await _subject_names(session, user)
    now = now_l(user)
    b = InlineKeyboardBuilder()
    for hw in items[:40]:
        b.button(text=render.hw_button_label(hw, names.get(hw.subject_id, "?"), now), callback_data=kb.HwCb(action="open", id=hw.id))
    _list_nav(b, "hw_dates")
    b.adjust(*([1] * min(len(items), 40)), 2, 1)
    if items:
        text = "<b>Невыполненные задания</b>\n\n" + render.hw_list_text([(h, names.get(h.subject_id, "?")) for h in items], now)
        text += "\n\n<i>Нажмите на задание, чтобы открыть его.</i>"
    else:
        text = "Невыполненных заданий нет 🎉"
    await show(event, text, b.as_markup(), toast=toast)


@router.message(F.text == kb.HW, StateFilter("*"))
@router.message(Command("homework"), StateFilter("*"))
async def hw_menu(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await _show_by_dates(message, session, user)


@router.callback_query(kb.Nav.filter(F.to == "hw_dates"), StateFilter("*"))
async def hw_dates_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await _show_by_dates(cb, session, user)


@router.callback_query(kb.Nav.filter(F.to == "hw_subjects"))
async def hw_subjects_cb(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    counts = await hw_core.open_counts_by_subject(session, user.id)
    b = InlineKeyboardBuilder()
    subjects = (await subj.list_subjects(session, user.id))[:90]  # не больше 100 кнопок в сообщении
    for s in subjects:
        b.button(text=f"{s.name} ({counts.get(s.id, 0)})", callback_data=kb.SubjCb(action="hw", id=s.id))
    _list_nav(b, "hw_subjects")
    b.adjust(*([1] * len(subjects)), 2, 1)
    await show(cb, "<b>Задания по предметам</b>\nВ скобках — сколько невыполненных.", b.as_markup())


@router.callback_query(kb.SubjCb.filter(F.action == "hw"))
async def hw_of_subject(cb: CallbackQuery, callback_data: kb.SubjCb, session: AsyncSession, user: User) -> None:
    s = await subj.get_subject(session, user.id, callback_data.id)
    if s is None:
        await answer_cb(cb, "Предмет не найден — возможно, его удалили.", show_alert=True)
        return
    items = await hw_core.list_open(session, user.id, s.id)
    now = now_l(user)
    b = InlineKeyboardBuilder()
    for hw in items[:40]:
        b.button(text=render.hw_button_label(hw, s.name, now), callback_data=kb.HwCb(action="open", id=hw.id))
    b.button(text="➕ Задание по этому предмету", callback_data=kb.SubjCb(action="addhw", id=s.id))
    b.button(text="⬅️ К предметам", callback_data=kb.Nav(to="hw_subjects"))
    b.adjust(1)
    text = f"<b>{escape(s.name)}</b>\n" + ("Невыполненные задания:" if items else "Невыполненных заданий нет.")
    await show(cb, text, b.as_markup())


@router.callback_query(kb.Nav.filter(F.to == "hw_done"))
async def hw_done_list(cb: CallbackQuery, session: AsyncSession, user: User) -> None:
    items = await hw_core.list_done(session, user.id)
    names = await _subject_names(session, user)
    now = now_l(user)
    b = InlineKeyboardBuilder()
    for hw in items:
        b.button(text=render.hw_button_label(hw, names.get(hw.subject_id, "?"), now), callback_data=kb.HwCb(action="open", id=hw.id))
    _list_nav(b, "hw_done")
    b.adjust(*([1] * len(items)), 2, 1)
    await show(cb, "<b>Последние выполненные задания</b>" if items else "Выполненных заданий пока нет.", b.as_markup())
