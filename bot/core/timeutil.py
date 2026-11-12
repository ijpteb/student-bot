"""Время, часовые пояса, чётность недель и разбор дат, которые пишет студент."""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

# Часовые пояса России: (название для студента, код пояса).
RU_TIMEZONES = [
    ("Калининград (UTC+2)", "Europe/Kaliningrad"),
    ("Москва (UTC+3)", "Europe/Moscow"),
    ("Самара (UTC+4)", "Europe/Samara"),
    ("Екатеринбург (UTC+5)", "Asia/Yekaterinburg"),
    ("Омск (UTC+6)", "Asia/Omsk"),
    ("Новосибирск, Красноярск (UTC+7)", "Asia/Krasnoyarsk"),
    ("Иркутск (UTC+8)", "Asia/Irkutsk"),
    ("Якутск (UTC+9)", "Asia/Yakutsk"),
    ("Владивосток (UTC+10)", "Asia/Vladivostok"),
    ("Магадан (UTC+11)", "Asia/Magadan"),
    ("Камчатка (UTC+12)", "Asia/Kamchatka"),
]


def tz_label(tz_name: str) -> str:
    for label, name in RU_TIMEZONES:
        if name == tz_name:
            return label
    return tz_name


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def local_now(tz_name: str, now: datetime | None = None) -> datetime:
    """Текущее местное время студента (без указания пояса — «как на его часах»)."""
    now = now or now_utc()
    return now.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)


def local_to_utc(local: datetime, tz_name: str) -> datetime:
    return local.replace(tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def default_week1(today: date) -> date:
    """Первая учебная неделя по умолчанию — неделя, в которую попадает 1 сентября."""
    year = today.year if today.month >= 9 else today.year - 1
    return monday_of(date(year, 9, 1))


def week_number(d: date, week1: date) -> int:
    """Номер недели: неделя с week1 — первая. До неё — 0, -1, … (чётность сохраняется)."""
    return (monday_of(d) - monday_of(week1)).days // 7 + 1


def parity_of(d: date, week1: date) -> int:
    """1 — нечётная неделя, 2 — чётная."""
    return 1 if week_number(d, week1) % 2 == 1 else 2


PARITY_NAMES = {0: "каждую неделю", 1: "по нечётным неделям", 2: "по чётным неделям"}


def fmt_date(d: date, with_weekday: bool = False) -> str:
    s = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    if with_weekday:
        s = f"{WEEKDAYS_SHORT[d.weekday()]}, {s}"
    return s


def fmt_time(t: time) -> str:
    return f"{t.hour}:{t.minute:02d}"


def fmt_dt(dt: datetime) -> str:
    return f"{fmt_date(dt.date())}, {fmt_time(dt.time())}"


class ParseError(ValueError):
    pass


_TIME_RE = re.compile(r"^(\d{1,2})[:.\-](\d{2})$")


def parse_time(text: str) -> time:
    text = text.strip()
    m = _TIME_RE.match(text) or re.match(r"^(\d{1,2})()$", text)
    if not m:
        raise ParseError("Не понял время. Напишите, например, 18:30.")
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    if h > 23 or mi > 59:
        raise ParseError("Такого времени не бывает. Напишите, например, 18:30.")
    return time(h, mi)


def parse_time_range(text: str) -> tuple[time, time]:
    # Понимает «9:00-10:30», «9.00 – 10.30», «с 9:00 до 10:30», «9-10».
    parts = re.findall(r"\d{1,2}[:.]\d{2}|\d{1,2}", text)
    if len(parts) != 2:
        raise ParseError("Напишите начало и конец пары, например: 9:00-10:30.")
    start, end = parse_time(parts[0]), parse_time(parts[1])
    if end <= start:
        raise ParseError("Конец пары должен быть позже начала.")
    return start, end


def parse_date(text: str, today: date) -> date:
    """«15.11», «15.11.2026», «15.11.26», «сегодня», «завтра», «послезавтра».
    Если год не указан и дата уже прошла — значит, следующий год."""
    t = text.strip().lower()
    words = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
    if t in words:
        return today + timedelta(days=words[t])
    m = re.match(r"^(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?$", t)
    if not m:
        raise ParseError("Не понял дату. Напишите, например, 15.11 или 15.11.2026.")
    day, month = int(m.group(1)), int(m.group(2))
    year_s = m.group(3)
    try:
        if year_s:
            year = int(year_s)
            if year < 100:
                year += 2000
            return date(year, month, day)
        d = date(today.year, month, day)
        if d < today - timedelta(days=1):
            d = date(today.year + 1, month, day)
        return d
    except ValueError:
        raise ParseError("Такой даты не бывает. Напишите, например, 15.11.") from None


def parse_date_time(text: str, today: date) -> tuple[date, time | None]:
    """«15.11», «15.11 18:00», «завтра 9:00». Время необязательно."""
    parts = text.strip().split()
    if len(parts) == 1:
        return parse_date(parts[0], today), None
    if len(parts) == 2:
        return parse_date(parts[0], today), parse_time(parts[1])
    raise ParseError("Напишите дату и, если нужно, время: например, 15.11 18:00.")


def parse_moment(text: str, now_local: datetime) -> datetime:
    """Момент для напоминания: «18:30» (сегодня, а если прошло — завтра), «15.11 18:30», «завтра 9:00»."""
    parts = text.strip().split()
    if len(parts) == 1 and not re.search(r"[./]", parts[0]) and parts[0].lower() not in (
        "сегодня", "завтра", "послезавтра",
    ):
        t = parse_time(parts[0])
        moment = datetime.combine(now_local.date(), t)
        if moment <= now_local:
            moment += timedelta(days=1)
        return moment
    d, t = parse_date_time(text, now_local.date())
    if t is None:
        raise ParseError("Укажите и время, например: 15.11 18:30.")
    return datetime.combine(d, t)
