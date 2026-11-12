from datetime import date, datetime, time

from bot.core import schedule as sch
from bot.core import subjects as subj
from bot.core.users import get_or_create_user

W1 = date(2026, 8, 31)  # первая неделя семестра
THU = date(2026, 11, 12)  # четверг, 11-я (нечётная) неделя
THU_EVEN = date(2026, 11, 19)  # четверг, 12-я (чётная) неделя


async def _setup(session):
    user = await get_or_create_user(session, "telegram", "1")
    math = await subj.find_or_create(session, user.id, "Матанализ")
    hist = await subj.find_or_create(session, user.id, "История")
    return user, math, hist


async def test_parity_and_one_off_and_cancel(session):
    user, math, hist = await _setup(session)
    every = await sch.add_lesson(session, user.id, math.id, time(9), time(10, 30), weekday=3)
    await sch.add_lesson(session, user.id, hist.id, time(10, 40), time(12, 10), weekday=3, parity=1)
    await sch.add_lesson(session, user.id, hist.id, time(13, 0), time(14, 30), weekday=3, parity=2)
    s = await sch.load(session, user.id, W1)

    assert [(o.subject, o.start) for o in s.day(THU)] == [("Матанализ", time(9)), ("История", time(10, 40))]
    assert [(o.subject, o.start) for o in s.day(THU_EVEN)] == [("Матанализ", time(9)), ("История", time(13))]

    # Отмена только на один день
    await sch.cancel_on(session, every, THU)
    # Разовая пара
    await sch.add_lesson(session, user.id, math.id, time(15), time(16, 30), on_date=THU)
    s = await sch.load(session, user.id, W1)
    assert [(o.subject, o.start, o.one_off) for o in s.day(THU)] == [
        ("История", time(10, 40), False),
        ("Матанализ", time(15), True),
    ]
    assert [o.start for o in s.day(THU_EVEN)][0] == time(9)  # через неделю пара на месте

    await sch.restore_on(session, every, THU)
    s = await sch.load(session, user.id, W1)
    assert len(s.day(THU)) == 3


async def test_valid_period(session):
    user, math, _ = await _setup(session)
    lesson = await sch.add_lesson(session, user.id, math.id, time(9), time(10, 30), weekday=3)
    lesson.valid_to = date(2026, 11, 15)
    s = await sch.load(session, user.id, W1)
    assert s.day(THU)
    assert not s.day(THU_EVEN)


async def test_next_lesson(session):
    user, math, hist = await _setup(session)
    await sch.add_lesson(session, user.id, math.id, time(9), time(10, 30), weekday=3)
    await sch.add_lesson(session, user.id, hist.id, time(9), time(10, 30), weekday=0)
    s = await sch.load(session, user.id, W1)

    occ, ongoing = s.next_lesson(datetime(2026, 11, 12, 9, 45))
    assert occ.subject == "Матанализ" and ongoing
    occ, ongoing = s.next_lesson(datetime(2026, 11, 12, 11, 0))
    assert occ.subject == "История" and occ.day == date(2026, 11, 16) and not ongoing

    nxt = s.next_for_subject(math.id, datetime(2026, 11, 12, 11, 0))
    assert nxt.day == date(2026, 11, 19)


async def test_week_and_empty(session):
    user, math, _ = await _setup(session)
    s = await sch.load(session, user.id, W1)
    assert s.next_lesson(datetime(2026, 11, 12, 9)) is None
    await sch.add_lesson(session, user.id, math.id, time(9), time(10, 30), weekday=3)
    s = await sch.load(session, user.id, W1)
    week = s.week(THU)
    assert week[0][0] == date(2026, 11, 9) and len(week) == 7
    assert [len(o) for _, o in week] == [0, 0, 0, 1, 0, 0, 0]
