"""Тексты сообщений. Не зависят от мессенджера: простая разметка <b>, <i>."""

import html
import re
from datetime import date, datetime, timedelta
from html import escape

from bot.core.schedule import Occurrence, Schedule
from bot.core.timeutil import (
    WEEKDAYS,
    fmt_date,
    fmt_dt,
    fmt_time,
    parity_of,
    week_number,
)
from bot.models import Homework

DISCLAIMER = (
    "Бот неофициальный и не связан ни с одним вузом. "
    "Расписание и задания — те, что вы внесли сами."
)

# Длинные сообщения мессенджеры не принимают (в Telegram — больше 4096 знаков).
# Сводку держим заметно короче этого предела.
DIGEST_MAX_LEN = 3500

_TAG = re.compile(r"<[^>]+>")


def visible_len(text: str) -> int:
    """Длина текста, которую видит человек: без разметки и с раскрытыми &lt; &amp; и т. п."""
    return len(html.unescape(_TAG.sub("", text)))


def week_header(d: date, week1: date) -> str:
    n = week_number(d, week1)
    par = "нечётная" if parity_of(d, week1) == 1 else "чётная"
    if n < 1:
        return f"Неделя {par}"
    return f"{n}-я неделя, {par}"


def occurrence_line(o: Occurrence, details: bool = True) -> str:
    line = f"<b>{fmt_time(o.start)}–{fmt_time(o.end)}</b>  {escape(o.subject)}"
    extra = [x for x in (o.kind, o.room, o.teacher) if x]
    if extra and details:
        line += "\n      " + escape(" · ".join(extra))
    if o.one_off:
        line += "  <i>(разовая)</i>"
    return line


def day_text(day: date, occurrences: list[Occurrence], week1: date, title: str | None = None) -> str:
    head = title or WEEKDAYS[day.weekday()]
    lines = [f"<b>{head}, {fmt_date(day)}</b>  <i>({week_header(day, week1)})</i>"]
    if occurrences:
        lines += [occurrence_line(o) for o in occurrences]
    else:
        lines.append("Пар нет 🎉")
    return "\n".join(lines)


def _week_text(sched: Schedule, any_day: date, details: bool) -> str:
    days = sched.week(any_day)
    monday = days[0][0]
    parts = [f"<b>Неделя {fmt_date(monday)} – {fmt_date(days[-1][0])}</b>\n<i>{week_header(monday, sched.week1)}</i>"]
    for d, occ in days:
        if not occ and d.weekday() >= 5:
            continue  # пустые выходные не показываем
        block = [f"\n<b>{WEEKDAYS[d.weekday()]}, {fmt_date(d)}</b>"]
        block += [occurrence_line(o, details) for o in occ] if occ else ["—"]
        parts.append("\n".join(block))
    return "\n".join(parts)


def week_text(sched: Schedule, any_day: date, max_len: int | None = None) -> str:
    """Расписание недели. Если оно длиннее max_len — без аудиторий и преподавателей."""
    text = _week_text(sched, any_day, details=True)
    if max_len is not None and visible_len(text) > max_len:
        text = _week_text(sched, any_day, details=False)
        text += "\n\n<i>Аудитории и преподаватели не поместились — они есть в расписании на день.</i>"
    return text


def next_lesson_text(found: tuple[Occurrence, bool] | None, now_local: datetime) -> str:
    if found is None:
        return "В ближайшие три недели пар нет."
    o, ongoing = found
    if ongoing:
        head = "Сейчас идёт пара:"
    elif o.day == now_local.date():
        head = "Ближайшая пара — сегодня:"
    elif (o.day - now_local.date()).days == 1:
        head = "Ближайшая пара — завтра:"
    else:
        head = f"Ближайшая пара — {WEEKDAYS[o.day.weekday()].lower()}, {fmt_date(o.day)}:"
    return f"{head}\n{occurrence_line(o)}"


def due_text(hw: Homework) -> str:
    if hw.due_date is None:
        return "без срока"
    s = fmt_date(hw.due_date, with_weekday=True)
    if hw.due_time:
        s += f", {fmt_time(hw.due_time)}"
    return s


def is_overdue(hw: Homework, now_local: datetime) -> bool:
    if hw.done or hw.due_date is None:
        return False
    if hw.due_date < now_local.date():
        return True
    return hw.due_date == now_local.date() and hw.due_time is not None and hw.due_time <= now_local.time()


def due_marker(hw: Homework, now_local: datetime) -> str:
    if hw.done:
        return "✅"
    if hw.due_date is None:
        return "▫️"
    if is_overdue(hw, now_local):
        return "❗"
    if hw.due_date <= now_local.date() + timedelta(days=1):
        return "🔥"
    return "▫️"


def short(text: str, n: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def hw_button_label(hw: Homework, subject: str, now_local: datetime) -> str:
    due = fmt_date(hw.due_date) if hw.due_date else "без срока"
    body = short(hw.text, 30) if hw.text else "(вложение)"
    return f"{due_marker(hw, now_local)} {due} · {short(subject, 18)}: {body}"


def hw_card(
    hw: Homework,
    subject: str,
    reminder_at: datetime | None,
    n_files: int,
    reminders_enabled: bool = True,
) -> str:
    lines = [f"<b>{escape(subject)}</b>"]
    lines.append(escape(hw.text) if hw.text else "<i>(без текста)</i>")
    lines.append("")
    lines.append(f"📅 Срок: {due_text(hw)}")
    if reminder_at:
        line = f"⏰ Напоминание: {fmt_dt(reminder_at)}"
        if hw.done:
            line += " <i>(не придёт, пока задание отмечено выполненным)</i>"
        elif not reminders_enabled:
            line += " <i>(не придёт: напоминания выключены в ⚙️ Настройках)</i>"
        lines.append(line)
    if n_files:
        lines.append(f"📎 Вложений: {n_files}")
    if hw.done:
        lines.append("✅ Выполнено")
    return "\n".join(lines)


def hw_list_text(items: list[tuple[Homework, str]], now_local: datetime) -> str:
    """Список заданий, сгруппированный по срокам."""
    today = now_local.date()
    groups: dict[str, list[str]] = {"Просрочено": [], "Сегодня": [], "Завтра": [], "Позже": [], "Без срока": []}
    for hw, subject in items:
        line = f"• <b>{escape(subject)}</b>: {escape(short(hw.text, 80)) or '<i>(вложение)</i>'}"
        if hw.due_date is None:
            groups["Без срока"].append(line)
            continue
        delta = (hw.due_date - today).days
        if is_overdue(hw, now_local):
            groups["Просрочено"].append(line + f" <i>(до {due_text(hw)})</i>")
        elif delta == 0:
            groups["Сегодня"].append(line + (f" <i>(до {fmt_time(hw.due_time)})</i>" if hw.due_time else ""))
        elif delta == 1:
            groups["Завтра"].append(line + (f" <i>(до {fmt_time(hw.due_time)})</i>" if hw.due_time else ""))
        else:
            groups["Позже"].append(line + f" <i>(до {due_text(hw)})</i>")
    out = []
    for title, lines in groups.items():
        if lines:
            out.append(f"<b>{title}</b>\n" + "\n".join(lines))
    return "\n\n".join(out)


def digest_text(items: list[tuple[Homework, str]], now_local: datetime, max_len: int = DIGEST_MAX_LEN) -> str:
    """Сводка невыполненного. Если заданий слишком много, показываем самые срочные
    (задания приходят отсортированными по сроку) и пишем, сколько не поместилось."""
    head = f"📋 <b>Невыполненные задания ({len(items)})</b>\n\n"
    k = len(items)
    while True:
        text = head + hw_list_text(items[:k], now_local)
        if k < len(items):
            text += f"\n\n<i>…и ещё {len(items) - k}. Все задания — в разделе «📚 Задания».</i>"
        if visible_len(text) <= max_len or k <= 1:
            return text
        k -= 1


def reminder_text(hw: Homework, subject: str) -> str:
    return f"⏰ <b>Напоминание</b>\n\n<b>{escape(subject)}</b>: {escape(hw.text) or '<i>(см. вложения)</i>'}\n📅 Срок: {due_text(hw)}"
