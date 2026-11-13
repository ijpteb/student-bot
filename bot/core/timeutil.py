"""Время, часовые пояса, чётность недель и разбор дат, которые пишет студент."""

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
# «на среду», «на пятницу» — винительный падеж для фраз «изменения на …»
WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
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


def academic_week1(year: int) -> date:
    """Понедельник первой учебной недели учебного года, начинающегося в сентябре year.
    Если 1 сентября — воскресенье, занятия начинаются 2 сентября."""
    sept1 = date(year, 9, 1)
    if sept1.weekday() == 6:
        return sept1 + timedelta(days=1)
    return monday_of(sept1)


def default_week1(day: date) -> date:
    """Первая учебная неделя по умолчанию — неделя, в которую попадает 1 сентября.
    Учебный год выбирается по этой неделе, а не по месяцу: 31 августа может уже быть 1-й неделей."""
    w1 = academic_week1(day.year)
    return w1 if day >= w1 else academic_week1(day.year - 1)


def week_number(d: date, week1: date) -> int:
    """Номер недели: неделя с week1 — первая. До неё — 0, -1, … (чётность сохраняется)."""
    return (monday_of(d) - monday_of(week1)).days // 7 + 1


def parity_of(d: date, week1: date) -> int:
    """1 — нечётная неделя, 2 — чётная."""
    return 1 if week_number(d, week1) % 2 == 1 else 2


PARITY_NAMES = {0: "каждую неделю", 1: "по нечётным неделям", 2: "по чётным неделям"}


def fmt_date(d: date, with_weekday: bool = False, year: bool | None = None) -> str:
    """«15 ноября». Год показываем, если дата дальше полугода от сегодняшней (или если year=True),
    чтобы ошибка в годе была видна сразу, а близкие даты (и даты на стыке лет) читались коротко."""
    s = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    if year or (year is None and abs((d - now_utc().date()).days) > 183):
        s += f" {d.year}"
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
    """«18:30», «18.30», «18-30», «7». «24:00» понимаем как конец дня (23:59)."""
    text = text.strip()
    m = _TIME_RE.match(text) or re.match(r"^(\d{1,2})()$", text)
    if not m:
        raise ParseError("Не понял время. Напишите, например, 18:30.")
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    if h == 24 and mi == 0:
        return time(23, 59)
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


# Насколько далеко в прошлом может быть дата без года, чтобы считаться этим годом,
# когда речь о сроке сдачи или напоминании. Более ранняя дата — значит, следующий год.
FUTURE_GRACE = timedelta(days=90)

_DATE_RE = re.compile(r"^(\d{1,2})[./](\d{1,2})(?:[./](\d{2}|\d{4}))?\.?$")
_DAY_WORDS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}


def parse_date(text: str, today: date, mode: str = "future") -> date:
    """«15.11», «15.11.2026», «15.11.26», «сегодня», «завтра», «послезавтра».

    Если год не указан, он выбирается по смыслу (mode):
    - "future" — срок сдачи, напоминание: этот год, а если дата прошла больше 90 дней назад — следующий;
    - "academic" — начало семестра, сроки пары: дата внутри текущего учебного года
      (с 1 августа по 31 июля: «01.09» в ноябре — этот сентябрь, «30.06» в сентябре — следующий июнь);
    - "nearest" — ближайшая к сегодняшнему дню такая дата (прошлый, этот или следующий год);
    - "after" — первая такая дата не раньше today (сюда передают, например, начало периода).
    """
    t = text.strip().lower()
    if t in _DAY_WORDS:
        return today + timedelta(days=_DAY_WORDS[t])
    m = _DATE_RE.match(t)
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
        date(2024, month, day)  # проверка, что такой день вообще бывает (29.02 — в високосный год)
    except ValueError:
        raise ParseError("Такой даты не бывает. Напишите, например, 15.11.") from None

    candidates = []
    for y in range(today.year - 1, today.year + 5):  # 29.02 бывает раз в 4 года
        try:
            candidates.append(date(y, month, day))
        except ValueError:  # 29.02 в невисокосный год
            pass
    if mode == "academic":
        start_year = today.year if today.month >= 8 else today.year - 1
        year = start_year if month >= 8 else start_year + 1
        for c in candidates:
            if c.year == year:
                return c
        return min(c for c in candidates if c.year >= year)  # 29.02 в невисокосный год — ближайший високосный
    if mode == "nearest":
        return min(candidates, key=lambda c: abs((c - today).days))
    if mode == "after":
        return min(c for c in candidates if c >= today)
    return min(c for c in candidates if c >= today - FUTURE_GRACE)


def parse_weekday(text: str) -> int | None:
    """«понедельник», «пн», «в среду», «Пятницу» → номер дня недели (0 — понедельник)."""
    t = text.strip().lower().replace("ё", "е")
    t = re.sub(r"^(в|во)\s+", "", t).rstrip(".")
    for i, (full, acc, short) in enumerate(zip(WEEKDAYS, WEEKDAYS_ACC, WEEKDAYS_SHORT)):
        if t in (full.lower(), acc, short.lower()):
            return i
    return None


_FILLER = {"в", "во", "к", "ко", "до", "на"}


def _is_time(word: str) -> bool:
    try:
        parse_time(word)
        return True
    except ParseError:
        return False


def _words(text: str) -> list[str]:
    """Делит ввод на слова, отбрасывая «в», «к», «до», запятые и точку в конце.
    Время перед датой («9:00 завтра», «9.30 завтра», «18.00 15.11») меняет местами с датой."""
    words = [w.rstrip(".") if not _TIME_RE.match(w) else w for w in text.replace(",", " ").split()]
    words = [w for w in words if w and w.lower() not in _FILLER]
    if len(words) == 2:
        first, second = words
        first_is_date = first.lower() in _DAY_WORDS or bool(_DATE_RE.match(first.lower()) and _valid_dm(first))
        second_is_date = second.lower() in _DAY_WORDS or bool(_DATE_RE.match(second.lower()) and _valid_dm(second))
        if _is_time(first) and not first_is_date and second_is_date:
            words.reverse()
    return words


def _valid_dm(word: str) -> bool:
    """Похоже ли слово на существующую дату «день.месяц» (без учёта года)."""
    m = _DATE_RE.match(word.lower())
    if not m:
        return False
    try:
        date(2024, int(m.group(2)), int(m.group(1)))
        return True
    except ValueError:
        return False


def parse_date_time(text: str, today: date) -> tuple[date, time | None]:
    """Срок сдачи: «15.11», «15.11 18:00», «завтра в 9:00», «к 18:00 15.11». Время необязательно."""
    parts = _words(text)
    if len(parts) == 1:
        return parse_date(parts[0], today), None
    if len(parts) == 2:
        return parse_date(parts[0], today), parse_time(parts[1])
    raise ParseError("Напишите дату и, если нужно, время: например, 15.11 18:00.")


def _is_date(word: str, today: date) -> bool:
    try:
        parse_date(word, today)
        return True
    except ParseError:
        return False


def parse_moment(text: str, now_local: datetime) -> datetime:
    """Момент для напоминания: «18:30» или «в 19.30» (сегодня, а если прошло — завтра),
    «15.11 18:30», «завтра в 9:00»."""
    parts = _words(text)
    if len(parts) == 1:
        word = parts[0]
        if word.lower() in _DAY_WORDS:
            raise ParseError("Укажите и время, например: завтра 9:00.")
        is_time = not re.search(r"[./]", word)
        if not is_time and re.fullmatch(r"\d{1,2}\.\d{2}", word):
            # «19.30» датой быть не может — значит, время. «14.05» может быть и тем и другим,
            # а «25.12» — только датой (25:12 не бывает): тогда просим добавить время.
            if not _is_date(word, now_local.date()):
                is_time = True
            elif _is_time(word):
                raise ParseError(
                    f"«{word}» — это дата или время? Время напишите через двоеточие (например, "
                    f"{word.replace('.', ':')}), а дату — вместе со временем (например, {word} 18:00)."
                )
        if is_time:
            t = parse_time(word)
            moment = datetime.combine(now_local.date(), t)
            if moment <= now_local:
                moment += timedelta(days=1)
            return moment
    d, t = parse_date_time(" ".join(parts), now_local.date())
    if t is None:
        raise ParseError(f"Укажите и время, например: {' '.join(parts)} 18:00.")
    return datetime.combine(d, t)
