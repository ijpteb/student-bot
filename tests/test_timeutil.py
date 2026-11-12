from datetime import date, datetime, time, timezone

import pytest

from bot.core.timeutil import (
    ParseError,
    default_week1,
    local_now,
    local_to_utc,
    parity_of,
    parse_date,
    parse_date_time,
    parse_moment,
    parse_time,
    parse_time_range,
    week_number,
)


def test_local_to_utc_moscow_and_vladivostok():
    local = datetime(2026, 11, 15, 9, 0)
    assert local_to_utc(local, "Europe/Moscow") == datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)
    assert local_to_utc(local, "Asia/Vladivostok") == datetime(2026, 11, 14, 23, 0, tzinfo=timezone.utc)


def test_local_now_crosses_midnight():
    now = datetime(2026, 11, 14, 22, 30, tzinfo=timezone.utc)
    assert local_now("Europe/Moscow", now) == datetime(2026, 11, 15, 1, 30)
    assert local_now("Europe/Kaliningrad", now) == datetime(2026, 11, 15, 0, 30)


def test_week_parity():
    w1 = default_week1(date(2026, 11, 12))
    assert w1 == date(2026, 8, 31)  # 1 сентября 2026 — вторник, неделя начинается 31 августа
    assert week_number(date(2026, 9, 1), w1) == 1
    assert parity_of(date(2026, 9, 6), w1) == 1  # воскресенье той же недели
    assert parity_of(date(2026, 9, 7), w1) == 2
    assert week_number(date(2026, 11, 12), w1) == 11
    assert parity_of(date(2026, 11, 12), w1) == 1
    # до начала семестра чётность продолжает чередоваться
    assert parity_of(date(2026, 8, 30), w1) == 2


def test_default_week1_spring():
    assert default_week1(date(2027, 3, 1)) == date(2026, 8, 31)


@pytest.mark.parametrize(
    "text,expected",
    [("9:00-10:30", (time(9), time(10, 30))), ("9.00 – 10.30", (time(9), time(10, 30))),
     ("с 13:40 до 15:10", (time(13, 40), time(15, 10))), ("9-10", (time(9), time(10)))],
)
def test_time_range(text, expected):
    assert parse_time_range(text) == expected


@pytest.mark.parametrize("bad", ["10:30-9:00", "9:00", "утром", "25:00-26:00"])
def test_time_range_bad(bad):
    with pytest.raises(ParseError):
        parse_time_range(bad)


def test_parse_date():
    today = date(2026, 11, 12)
    assert parse_date("15.11", today) == date(2026, 11, 15)
    assert parse_date("15.11.27", today) == date(2027, 11, 15)
    assert parse_date("10.01", today) == date(2027, 1, 10)  # прошедшая дата без года — следующий год
    assert parse_date("завтра", today) == date(2026, 11, 13)
    with pytest.raises(ParseError):
        parse_date("31.02", today)
    assert parse_date_time("15.11 18:00", today) == (date(2026, 11, 15), time(18))
    assert parse_time("7") == time(7)


def test_parse_moment():
    now = datetime(2026, 11, 12, 20, 0)
    assert parse_moment("21:30", now) == datetime(2026, 11, 12, 21, 30)
    assert parse_moment("19:30", now) == datetime(2026, 11, 13, 19, 30)  # уже прошло — завтра
    assert parse_moment("завтра 9:00", now) == datetime(2026, 11, 13, 9, 0)
    assert parse_moment("15.11 8:15", now) == datetime(2026, 11, 15, 8, 15)
    with pytest.raises(ParseError):
        parse_moment("15.11", now)  # без времени нельзя
