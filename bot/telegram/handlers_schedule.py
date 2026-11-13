"""Просмотр и редактирование расписания, предметы."""

from datetime import date, time, timedelta
from html import escape

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core import render
from bot.core import schedule as sch
from bot.core import subjects as subj
from bot.core.timeutil import (
    PARITY_NAMES,
    WEEKDAYS,
    WEEKDAYS_ACC,
    WEEKDAYS_SHORT,
    ParseError,
    fmt_date,
    fmt_time,
    parity_of,
    parse_date,
    parse_time,
    parse_time_range,
    parse_weekday,
)
from bot.models import User
from bot.telegram import kb
from bot.telegram.ui import MAX_LEN, answer_cb, now_l, show, today, week1

router = Router(name="schedule")

PARITY_SHORT = {0: "", 1: " (неч.)", 2: " (чёт.)"}
NOT_MENU = ~F.text.in_(kb.MENU_BUTTONS) & ~F.text.startswith("/")
EMPTY_HINT = "\n\n<i>Расписание пока не внесено.</i>"


async def _load(session: AsyncSession, user: User) -> sch.Schedule:
    return await sch.load(session, user.id, week1(user))


# ---------------- Просмотр ----------------

def _day_markup(day: date, empty_schedule: bool):
    b = InlineKeyboardBuilder()
    b.button(text="◀️", callback_data=kb.DayCb(action="show", day=(day - timedelta(days=1)).isoformat()))
    b.button(text="▶️", callback_data=kb.DayCb(action="show", day=(day + timedelta(days=1)).isoformat()))
    if empty_schedule:
        b.button(text="✏️ Внести расписание", callback_data=kb.Nav(to="sched"))
    # Разовую пару (консультация, экзамен) можно добавить и в пустое расписание.
    b.button(text="✏️ Изменения на этот день", callback_data=kb.DayCb(action="edit", day=day.isoformat()))
    b.adjust(2, 1)
    return b.as_markup()


async def _show_day(event, session: AsyncSession, user: User, day: date, title: str | None = None) -> None:
    s = await _load(session, user)
    text = render.day_text(day, s.day(day), s.week1, title)
    if not s.lessons:
        text += EMPTY_HINT
    await show(event, text, _day_markup(day, not s.lessons))


@router.message(F.text == kb.TODAY, StateFilter("*"))
@router.message(Command("today"), StateFilter("*"))
async def today_cmd(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await _show_day(message, session, user, today(user), "Сегодня")


@router.message(F.text == kb.TOMORROW, StateFilter("*"))
@router.message(Command("tomorrow"), StateFilter("*"))
async def tomorrow_cmd(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await _show_day(message, session, user, today(user) + timedelta(days=1), "Завтра")


@router.callback_query(kb.DayCb.filter(F.action == "show"))
async def day_cb(cb: CallbackQuery, callback_data: kb.DayCb, session: AsyncSession, user: User) -> None:
    day = date.fromisoformat(callback_data.day)
    t = today(user)
    title = {t: "Сегодня", t + timedelta(days=1): "Завтра"}.get(day)
    await _show_day(cb, session, user, day, title)


def _week_markup(day: date, empty_schedule: bool = False):
    b = InlineKeyboardBuilder()
    b.button(text="◀️ Пред. неделя", callback_data=kb.DayCb(action="week", day=(day - timedelta(days=7)).isoformat()))
    b.button(text="След. неделя ▶️", callback_data=kb.DayCb(action="week", day=(day + timedelta(days=7)).isoformat()))
    if empty_schedule:
        b.button(text="✏️ Внести расписание", callback_data=kb.Nav(to="sched"))
    b.adjust(2, 1)
    return b.as_markup()


async def _show_week(event, session: AsyncSession, user: User, day: date) -> None:
    s = await _load(session, user)
    text = render.week_text(s, day, max_len=MAX_LEN - 100)
    if not s.lessons:
        text += EMPTY_HINT
    await show(event, text, _week_markup(day, not s.lessons))


@router.message(F.text == kb.WEEK, StateFilter("*"))
@router.message(Command("week"), StateFilter("*"))
async def week_cmd(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await _show_week(message, session, user, today(user))


@router.callback_query(kb.DayCb.filter(F.action == "week"))
async def week_cb(cb: CallbackQuery, callback_data: kb.DayCb, session: AsyncSession, user: User) -> None:
    await _show_week(cb, session, user, date.fromisoformat(callback_data.day))


@router.message(F.text == kb.NEXT, StateFilter("*"))
@router.message(Command("next"), StateFilter("*"))
async def next_cmd(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    s = await _load(session, user)
    if not s.lessons:
        b = InlineKeyboardBuilder()
        b.button(text="✏️ Внести расписание", callback_data=kb.Nav(to="sched"))
        await message.answer("Расписание пока не внесено — внесите его, и я буду показывать ближайшую пару.", reply_markup=b.as_markup())
        return
    now = now_l(user)
    await message.answer(render.next_lesson_text(s.next_lesson(now), now))


# ---------------- Изменения на конкретный день ----------------

async def _show_day_edit(event, session: AsyncSession, user: User, day: date) -> None:
    s = await _load(session, user)
    occ = s.day(day)
    cancelled = await sch.cancelled_on(session, user.id, day)
    b = InlineKeyboardBuilder()
    for o in occ:
        b.button(
            text=f"❌ Отменить {fmt_time(o.start)} {o.subject}",
            callback_data=kb.LessonCb(action="cancel", id=o.lesson_id, day=day.isoformat()),
        )
    for l in cancelled:
        b.button(
            text=f"↩️ Вернуть {fmt_time(l.start_time)} {s.subjects.get(l.subject_id, '?')}",
            callback_data=kb.LessonCb(action="restore", id=l.id, day=day.isoformat()),
        )
    b.button(text="➕ Разовая пара в этот день", callback_data=kb.DayCb(action="addone", day=day.isoformat()))
    b.button(text="⬅️ Назад", callback_data=kb.DayCb(action="show", day=day.isoformat()))
    b.adjust(1)
    text = (
        f"<b>Изменения на {WEEKDAYS_ACC[day.weekday()]}, {fmt_date(day)}</b>\n\n"
        "Отмена действует только в этот день, остальные недели не меняются. "
        "Перенос пары = отмена + разовая пара в другой день."
    )
    await show(event, text, b.as_markup())


@router.callback_query(kb.DayCb.filter(F.action == "edit"))
async def day_edit(cb: CallbackQuery, callback_data: kb.DayCb, session: AsyncSession, user: User) -> None:
    await _show_day_edit(cb, session, user, date.fromisoformat(callback_data.day))


@router.callback_query(kb.LessonCb.filter(F.action.in_({"cancel", "restore"})))
async def lesson_cancel(cb: CallbackQuery, callback_data: kb.LessonCb, session: AsyncSession, user: User) -> None:
    lesson = await sch.get_lesson(session, user.id, callback_data.id)
    day = date.fromisoformat(callback_data.day)
    if lesson is None:
        await answer_cb(cb, "Пара не найдена — возможно, её удалили.", show_alert=True)
        return
    if callback_data.action == "cancel":
        await sch.cancel_on(session, lesson, day)
    else:
        await sch.restore_on(session, lesson, day)
    await _show_day_edit(cb, session, user, day)


# ---------------- Редактор расписания ----------------

async def _show_editor(event, session: AsyncSession, user: User, toast: str | None = None) -> None:
    s = await _load(session, user)
    lessons = s.recurring()
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить пару", callback_data=kb.Nav(to="add_lesson"))
    for l in lessons:
        b.button(
            text=f"{WEEKDAYS_SHORT[l.weekday]} {fmt_time(l.start_time)} {s.subjects.get(l.subject_id, '?')}{PARITY_SHORT[l.parity]}",
            callback_data=kb.LessonCb(action="open", id=l.id),
        )
    b.button(text="📖 Предметы", callback_data=kb.Nav(to="subjects"))
    b.adjust(1)
    one_off_hint = (
        "Разовые пары (консультации, экзамены) и отмены — через 📅 Сегодня/Завтра → "
        "«✏️ Изменения на этот день»."
    )
    if lessons:
        text = (
            "<b>Расписание по дням недели</b>\n"
            "Нажмите на пару, чтобы удалить её или ограничить сроки.\n" + one_off_hint
        )
    elif s.lessons:
        text = "<b>Еженедельных пар пока нет</b> — есть только разовые.\n" + one_off_hint
    else:
        text = (
            "<b>Расписание пока пустое.</b>\n"
            "Добавляйте пары по одной: предмет, день недели, время, чётность недели. "
            "Это делается один раз на семестр.\n" + one_off_hint
        )
    await show(event, text, b.as_markup(), toast=toast)


@router.callback_query(kb.Nav.filter(F.to == "sched"), StateFilter("*"))
async def editor_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await _show_editor(cb, session, user)


@router.callback_query(kb.LessonCb.filter(F.action == "open"))
async def lesson_open(cb: CallbackQuery, callback_data: kb.LessonCb, session: AsyncSession, user: User) -> None:
    lesson = await sch.get_lesson(session, user.id, callback_data.id)
    if lesson is None:
        await answer_cb(cb, "Пара не найдена — возможно, её удалили.", show_alert=True)
        return
    s = await _load(session, user)
    lines = [
        f"<b>{escape(s.subjects.get(lesson.subject_id, '?'))}</b>",
        f"{WEEKDAYS[lesson.weekday]}, {fmt_time(lesson.start_time)}–{fmt_time(lesson.end_time)}, {PARITY_NAMES[lesson.parity]}",
    ]
    extra = [x for x in (lesson.kind, lesson.room, lesson.teacher) if x]
    if extra:
        lines.append(escape(" · ".join(extra)))
    if lesson.valid_from or lesson.valid_to:
        p = "Действует"
        if lesson.valid_from:
            p += f" с {fmt_date(lesson.valid_from, year=True)}"
        if lesson.valid_to:
            p += f" по {fmt_date(lesson.valid_to, year=True)}"
        lines.append(p)
    b = InlineKeyboardBuilder()
    b.button(text="📆 Сроки действия", callback_data=kb.LessonCb(action="period", id=lesson.id))
    b.button(text="🗑 Удалить", callback_data=kb.LessonCb(action="del", id=lesson.id))
    b.button(text="⬅️ К расписанию", callback_data=kb.Nav(to="sched"))
    b.adjust(2, 1)
    await show(cb, "\n".join(lines), b.as_markup())


@router.callback_query(kb.LessonCb.filter(F.action == "del"))
async def lesson_del(cb: CallbackQuery, callback_data: kb.LessonCb, session: AsyncSession, user: User) -> None:
    lesson = await sch.get_lesson(session, user.id, callback_data.id)
    if lesson is not None:
        await sch.delete_lesson(session, lesson)
    await _show_editor(cb, session, user, toast="Пара удалена")


class LessonPeriod(StatesGroup):
    text = State()


PERIOD_HELP = (
    "С какого по какое число идёт эта пара? Напишите, например:\n"
    "<code>01.09-25.12</code> — с 1 сентября по 25 декабря\n"
    "<code>-25.12</code> — до 25 декабря\n"
    "<code>01.10-</code> — с 1 октября\n"
    "<code>-</code> — без ограничений\n"
    "<i>Год можно не писать. Если нужно, пишите полностью: 09.02.2027-30.06.2027.</i>"
)


@router.callback_query(kb.LessonCb.filter(F.action == "period"), StateFilter("*"))
async def lesson_period(cb: CallbackQuery, callback_data: kb.LessonCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    lesson = await sch.get_lesson(session, user.id, callback_data.id)
    if lesson is None:
        await answer_cb(cb, "Пара не найдена — возможно, её удалили.", show_alert=True)
        return
    await state.clear()
    await state.set_state(LessonPeriod.text)
    await state.update_data(lesson_id=lesson.id)
    b = InlineKeyboardBuilder()
    kb.cancel_row(b)
    await show(cb, PERIOD_HELP, b.as_markup())


def _parse_period(raw: str, today_: date) -> tuple[date | None, date | None]:
    """«01.09-25.12», «-25.12», «01.10-», «-». Год можно не писать: начало — ближайшая к сегодняшнему
    дню такая дата (обычно уже прошедшая), конец — первая такая дата после начала."""
    left, sep, right = raw.strip().partition("-")
    if not sep:
        raise ParseError("Не понял. Пример: 01.09-25.12")
    vfrom = parse_date(left, today_, mode="nearest") if left.strip() else None
    if right.strip():
        vto = parse_date(right, vfrom, mode="after") if vfrom else parse_date(right, today_, mode="nearest")
    else:
        vto = None
    if vfrom and vto and vto < vfrom:
        raise ParseError("Конец раньше начала. Пример: 01.09.2026-25.12.2026")
    return vfrom, vto


@router.message(LessonPeriod.text, F.text, NOT_MENU)
async def lesson_period_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    lesson = await sch.get_lesson(session, user.id, data["lesson_id"])
    if lesson is None:
        await state.clear()
        await message.answer("Пара не найдена — возможно, её удалили.")
        return
    try:
        vfrom, vto = _parse_period(message.text, today(user))
    except ParseError as e:
        await message.answer(str(e))
        return
    lesson.valid_from, lesson.valid_to = vfrom, vto
    await state.clear()
    if vfrom or vto:
        period = "Сроки сохранены: пара идёт"
        if vfrom:
            period += f" с {fmt_date(vfrom, year=True)}"
        if vto:
            period += f" по {fmt_date(vto, year=True)}"
        await message.answer(period + ".")
    else:
        await message.answer("Сроки сняты: пара идёт без ограничений.")
    await _show_editor(message, session, user)


# ---------------- Добавление пары ----------------

class AddLesson(StatesGroup):
    subject = State()
    new_subject = State()
    weekday = State()
    time = State()
    parity = State()
    details = State()


async def _ask_subject(event, state: FSMContext, session: AsyncSession, user: User) -> None:
    subjects = await subj.list_subjects(session, user.id)
    if not subjects:
        await state.set_state(AddLesson.new_subject)
        b = InlineKeyboardBuilder()
        kb.cancel_row(b)
        await show(event, "Как называется предмет? Напишите название.", b.as_markup())
        return
    await state.set_state(AddLesson.subject)
    b = InlineKeyboardBuilder()
    for s in subjects:
        b.button(text=s.name, callback_data=kb.Pick(kind="subj", value=str(s.id)))
    b.button(text="➕ Новый предмет", callback_data=kb.Pick(kind="subj", value="new"))
    kb.cancel_row(b)
    b.adjust(1)
    await show(event, "Какой предмет? Выберите кнопкой или напишите название.", b.as_markup())


@router.callback_query(kb.Nav.filter(F.to == "add_lesson"), StateFilter("*"))
async def add_lesson_start(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await _ask_subject(cb, state, session, user)


@router.callback_query(kb.DayCb.filter(F.action == "addone"), StateFilter("*"))
async def add_one_off_start(cb: CallbackQuery, callback_data: kb.DayCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await state.update_data(on_date=callback_data.day)
    await _ask_subject(cb, state, session, user)


@router.callback_query(AddLesson.subject, kb.Pick.filter(F.kind == "subj"))
async def add_lesson_subject(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, session: AsyncSession, user: User) -> None:
    if callback_data.value == "new":
        await state.set_state(AddLesson.new_subject)
        b = InlineKeyboardBuilder()
        kb.cancel_row(b)
        await show(cb, "Как называется предмет? Напишите название.", b.as_markup())
        return
    s = await subj.get_subject(session, user.id, int(callback_data.value))
    if s is None:
        await answer_cb(cb, "Предмет не найден — возможно, его удалили.", show_alert=True)
        return
    await state.update_data(subject_id=s.id)
    await _after_subject(cb, state, session, user)


@router.message(AddLesson.new_subject, F.text, NOT_MENU)
@router.message(AddLesson.subject, F.text, NOT_MENU)  # название можно и написать, а не выбрать кнопкой
async def add_lesson_new_subject(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        s = await subj.find_or_create(session, user.id, message.text)
    except subj.SubjectError as e:
        await message.answer(str(e))
        return
    await state.update_data(subject_id=s.id)
    await _after_subject(message, state, session, user)


async def _after_subject(event, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    if data.get("on_date"):
        await _ask_time(event, state, session, user)
        return
    await state.set_state(AddLesson.weekday)
    b = InlineKeyboardBuilder()
    for i, name in enumerate(WEEKDAYS):
        b.button(text=name, callback_data=kb.Pick(kind="wd", value=str(i)))
    kb.cancel_row(b)
    b.adjust(2, 2, 2, 1, 1)
    await show(event, "В какой день недели?", b.as_markup())


@router.callback_query(AddLesson.weekday, kb.Pick.filter(F.kind == "wd"))
async def add_lesson_weekday(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.update_data(weekday=int(callback_data.value))
    await _ask_time(cb, state, session, user)


@router.message(AddLesson.weekday, F.text, NOT_MENU)
async def add_lesson_weekday_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    wd = parse_weekday(message.text)
    if wd is None:
        await message.answer("Не понял день недели. Нажмите кнопку или напишите, например: «вторник» или «Вт».")
        return
    await state.update_data(weekday=wd)
    await _ask_time(message, state, session, user)


async def _ask_time(event, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.set_state(AddLesson.time)
    s = await _load(session, user)
    b = InlineKeyboardBuilder()
    for start, end in s.time_slots():
        b.button(
            text=f"{fmt_time(start)}–{fmt_time(end)}",
            callback_data=kb.Pick(kind="slot", value=f"{start.strftime('%H%M')}{end.strftime('%H%M')}"),
        )
    b.adjust(3)
    # «Отмена» — всегда отдельной строкой, чтобы её не приняли за ещё одно время.
    b.row(InlineKeyboardButton(text="✖️ Отмена", callback_data=kb.Nav(to="cancel").pack()))
    hint = "Выберите время из уже введённых или напишите новое." if s.time_slots() else "Напишите время пары."
    await show(event, f"{hint}\nНапример: <code>9:00-10:30</code>", b.as_markup())


@router.callback_query(AddLesson.time, kb.Pick.filter(F.kind == "slot"))
async def add_lesson_slot(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext, user: User) -> None:
    v = callback_data.value
    start, end = parse_time(f"{v[0:2]}:{v[2:4]}"), parse_time(f"{v[4:6]}:{v[6:8]}")
    await _after_time(cb, state, user, start, end)


@router.message(AddLesson.time, F.text, NOT_MENU)
async def add_lesson_time(message: Message, state: FSMContext, user: User) -> None:
    try:
        start, end = parse_time_range(message.text)
    except ParseError as e:
        await message.answer(str(e))
        return
    await _after_time(message, state, user, start, end)


async def _after_time(event, state: FSMContext, user: User, start, end) -> None:
    await state.update_data(start=start.isoformat(), end=end.isoformat())
    data = await state.get_data()
    if data.get("on_date"):
        await _ask_details(event, state)
        return
    await state.set_state(AddLesson.parity)
    p = parity_of(today(user), week1(user))
    cur, other = ("нечётная", "чётная") if p == 1 else ("чётная", "нечётная")
    b = InlineKeyboardBuilder()
    b.button(text="Каждую неделю", callback_data=kb.Pick(kind="par", value="0"))
    b.button(text="Нечётные недели", callback_data=kb.Pick(kind="par", value="1"))
    b.button(text="Чётные недели", callback_data=kb.Pick(kind="par", value="2"))
    kb.cancel_row(b)
    b.adjust(1)
    await show(
        event,
        f"Как часто бывает эта пара?\n<i>По моему счёту сейчас {cur} неделя. Если в вашем вузе сейчас "
        f"{other} — поправьте первую неделю семестра: ⚙️ Настройки → 🔢 Первая неделя.</i>",
        b.as_markup(),
    )


@router.callback_query(AddLesson.parity, kb.Pick.filter(F.kind == "par"))
async def add_lesson_parity(cb: CallbackQuery, callback_data: kb.Pick, state: FSMContext) -> None:
    await state.update_data(parity=int(callback_data.value))
    await _ask_details(cb, state)


async def _ask_details(event, state: FSMContext) -> None:
    await state.set_state(AddLesson.details)
    b = InlineKeyboardBuilder()
    b.button(text="Пропустить", callback_data=kb.Pick(kind="det", value="skip"))
    kb.cancel_row(b)
    await show(
        event,
        "Аудитория, преподаватель и тип занятия — через запятую, в таком порядке. Например:\n"
        "<code>305, Иванова А.П., лекция</code>\n"
        "Можно написать только аудиторию или нажать «Пропустить».",
        b.as_markup(),
    )


@router.callback_query(AddLesson.details, kb.Pick.filter(F.kind == "det"))
async def add_lesson_skip_details(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await _save_lesson(cb, state, session, user, [])


@router.message(AddLesson.details, F.text, NOT_MENU)
async def add_lesson_details(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    parts = [p.strip()[:100] or None for p in message.text.split(",")][:3]
    await _save_lesson(message, state, session, user, parts)


async def _save_lesson(event, state: FSMContext, session: AsyncSession, user: User, parts: list) -> None:
    data = await state.get_data()
    parts = parts + [None] * (3 - len(parts))
    on_date = date.fromisoformat(data["on_date"]) if data.get("on_date") else None
    await state.clear()
    try:
        await sch.add_lesson(
            session,
            user.id,
            data["subject_id"],
            time.fromisoformat(data["start"]),
            time.fromisoformat(data["end"]),
            weekday=None if on_date else data["weekday"],
            parity=data.get("parity", 0),
            on_date=on_date,
            room=parts[0],
            teacher=parts[1],
            kind=parts[2],
        )
    except sch.ScheduleError as e:  # например, предмет удалили, пока шёл диалог
        await show(event, str(e))
        return
    b = InlineKeyboardBuilder()
    if on_date:
        b.button(text="⬅️ К этому дню", callback_data=kb.DayCb(action="show", day=on_date.isoformat()))
        text = f"✅ Разовая пара добавлена на {fmt_date(on_date)}."
    else:
        b.button(text="➕ Ещё пара", callback_data=kb.Nav(to="add_lesson"))
        b.button(text="🗓 Готово", callback_data=kb.Nav(to="sched"))
        text = "✅ Пара добавлена."
    await show(event, text, b.as_markup())


# ---------------- Предметы ----------------

class SubjectEdit(StatesGroup):
    new = State()
    rename = State()


async def _show_subjects(event, session: AsyncSession, user: User) -> None:
    subjects = await subj.list_subjects(session, user.id)
    b = InlineKeyboardBuilder()
    b.button(text="➕ Новый предмет", callback_data=kb.Nav(to="subj_new"))
    for s in subjects:
        b.button(text=s.name, callback_data=kb.SubjCb(action="open", id=s.id))
    b.button(text="⬅️ К расписанию", callback_data=kb.Nav(to="sched"))
    b.adjust(1)
    await show(event, "<b>Предметы</b>" if subjects else "Предметов пока нет.", b.as_markup())


@router.callback_query(kb.Nav.filter(F.to == "subjects"), StateFilter("*"))
async def subjects_cb(cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User) -> None:
    await state.clear()
    await _show_subjects(cb, session, user)


@router.callback_query(kb.Nav.filter(F.to == "subj_new"), StateFilter("*"))
async def subject_new(cb: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(SubjectEdit.new)
    b = InlineKeyboardBuilder()
    kb.cancel_row(b)
    await show(cb, "Напишите название предмета.", b.as_markup())


@router.message(SubjectEdit.new, F.text, NOT_MENU)
async def subject_new_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    try:
        await subj.find_or_create(session, user.id, message.text)
    except subj.SubjectError as e:
        await message.answer(str(e))
        return
    await state.clear()
    await _show_subjects(message, session, user)


@router.callback_query(kb.SubjCb.filter(F.action == "open"))
async def subject_open(cb: CallbackQuery, callback_data: kb.SubjCb, session: AsyncSession, user: User) -> None:
    s = await subj.get_subject(session, user.id, callback_data.id)
    if s is None:
        await answer_cb(cb, "Предмет не найден — возможно, его удалили.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text="✏️ Переименовать", callback_data=kb.SubjCb(action="rename", id=s.id))
    b.button(text="🗑 Удалить", callback_data=kb.SubjCb(action="del", id=s.id))
    b.button(text="⬅️ К предметам", callback_data=kb.Nav(to="subjects"))
    b.adjust(2, 1)
    await show(cb, f"<b>{escape(s.name)}</b>", b.as_markup())


@router.callback_query(kb.SubjCb.filter(F.action == "rename"), StateFilter("*"))
async def subject_rename(cb: CallbackQuery, callback_data: kb.SubjCb, state: FSMContext, session: AsyncSession, user: User) -> None:
    s = await subj.get_subject(session, user.id, callback_data.id)
    if s is None:
        await answer_cb(cb, "Предмет не найден — возможно, его удалили.", show_alert=True)
        return
    await state.clear()  # не смешиваем с данными другого начатого диалога
    await state.set_state(SubjectEdit.rename)
    await state.update_data(subject_id=s.id)
    b = InlineKeyboardBuilder()
    kb.cancel_row(b)
    await show(cb, "Напишите новое название.", b.as_markup())


@router.message(SubjectEdit.rename, F.text, NOT_MENU)
async def subject_rename_text(message: Message, state: FSMContext, session: AsyncSession, user: User) -> None:
    data = await state.get_data()
    s = await subj.get_subject(session, user.id, data["subject_id"])
    if s is None:
        await state.clear()
        await message.answer("Предмет не найден — возможно, его удалили.")
        return
    try:
        await subj.rename(session, s, message.text)
    except subj.SubjectError as e:
        await message.answer(str(e))
        return
    await state.clear()
    await _show_subjects(message, session, user)


@router.callback_query(kb.SubjCb.filter(F.action == "del"))
async def subject_del(cb: CallbackQuery, callback_data: kb.SubjCb, session: AsyncSession, user: User) -> None:
    s = await subj.get_subject(session, user.id, callback_data.id)
    if s is None:
        await answer_cb(cb, "Предмет не найден — возможно, его удалили.", show_alert=True)
        return
    n_lessons, n_hw = await subj.usage(session, s)
    b = InlineKeyboardBuilder()
    b.button(text="Да, удалить", callback_data=kb.SubjCb(action="delok", id=s.id))
    b.button(text="Нет", callback_data=kb.SubjCb(action="open", id=s.id))
    await show(
        cb,
        f"Удалить «{escape(s.name)}»? Вместе с ним удалятся пары ({n_lessons}) и задания ({n_hw}).",
        b.as_markup(),
    )


@router.callback_query(kb.SubjCb.filter(F.action == "delok"))
async def subject_delok(cb: CallbackQuery, callback_data: kb.SubjCb, session: AsyncSession, user: User) -> None:
    s = await subj.get_subject(session, user.id, callback_data.id)
    if s is not None:
        await subj.delete_subject(session, s)
    await _show_subjects(cb, session, user)
