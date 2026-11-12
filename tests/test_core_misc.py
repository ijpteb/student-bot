from datetime import date

from bot.core import homework as hw_core
from bot.core import schedule as sch
from bot.core import subjects as subj
from bot.core.users import add_university_request, get_or_create_user, request_counts
from bot.logging_setup import redact


async def test_isolation_between_students(session):
    a = await get_or_create_user(session, "telegram", "100")
    b = await get_or_create_user(session, "telegram", "200")
    sa = await subj.find_or_create(session, a.id, "Право")
    hw = await hw_core.add(session, a.id, sa.id, "секрет студента А")
    from datetime import time

    lesson = await sch.add_lesson(session, a.id, sa.id, time(9), time(10), weekday=0)

    assert await hw_core.get(session, b.id, hw.id) is None
    assert await hw_core.list_open(session, b.id) == []
    assert await subj.get_subject(session, b.id, sa.id) is None
    assert await subj.list_subjects(session, b.id) == []
    assert await sch.get_lesson(session, b.id, lesson.id) is None
    s = await sch.load(session, b.id, date(2026, 8, 31))
    assert s.lessons == []
    # Нельзя добавить задание в чужой предмет
    try:
        await hw_core.add(session, b.id, sa.id, "x")
        raise AssertionError("должна быть ошибка")
    except hw_core.HomeworkError:
        pass


async def test_homework_sorting(session):
    u = await get_or_create_user(session, "telegram", "1")
    s = await subj.find_or_create(session, u.id, "Экономика")
    await hw_core.add(session, u.id, s.id, "без срока")
    await hw_core.add(session, u.id, s.id, "позже", date(2026, 12, 1))
    await hw_core.add(session, u.id, s.id, "раньше", date(2026, 11, 20))
    assert [h.text for h in await hw_core.list_open(session, u.id)] == ["раньше", "позже", "без срока"]


async def test_subject_names_case_insensitive(session):
    u = await get_or_create_user(session, "telegram", "1")
    a = await subj.find_or_create(session, u.id, "  Мировая   экономика ")
    b = await subj.find_or_create(session, u.id, "мировая экономика")
    assert a.id == b.id and a.name == "Мировая экономика"


async def test_university_requests_counter(session):
    users = [await get_or_create_user(session, "telegram", str(i)) for i in range(3)]
    assert await add_university_request(session, users[0], "ИМЭС")
    assert await add_university_request(session, users[1], "«имэс»")
    assert not await add_university_request(session, users[1], "ИМЭС")  # повтор не считается
    assert await add_university_request(session, users[2], "МГУ")
    await session.flush()
    assert await request_counts(session) == [("ИМЭС", 2), ("МГУ", 1)]


def test_log_redaction():
    line = "POST https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getMe token=abc&password=Qwerty1!"
    out = redact(line)
    assert "AAHdq" not in out and "abc" not in out and "Qwerty1" not in out
