"""Расписание: повторяющиеся пары с учётом чётности недели, разовые пары, отмены на конкретный день."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.timeutil import parity_of
from bot.models import Lesson, LessonCancel, Subject


@dataclass(frozen=True)
class Occurrence:
    """Конкретная пара в конкретный день."""

    lesson_id: int
    subject_id: int
    subject: str
    day: date
    start: time
    end: time
    room: str | None
    teacher: str | None
    kind: str | None
    one_off: bool


class ScheduleError(ValueError):
    pass


class Schedule:
    """Всё расписание одного студента в памяти: пар у студента немного, так проще и быстрее."""

    def __init__(self, lessons: list[Lesson], subjects: dict[int, str], cancels: set[tuple[int, date]], week1: date):
        self.lessons = lessons
        self.subjects = subjects
        self.cancels = cancels
        self.week1 = week1

    def _occurs(self, lesson: Lesson, day: date) -> bool:
        if lesson.on_date is not None:
            return lesson.on_date == day
        if lesson.weekday != day.weekday():
            return False
        if lesson.valid_from and day < lesson.valid_from:
            return False
        if lesson.valid_to and day > lesson.valid_to:
            return False
        if lesson.parity and parity_of(day, self.week1) != lesson.parity:
            return False
        return (lesson.id, day) not in self.cancels

    def day(self, day: date) -> list[Occurrence]:
        result = [
            Occurrence(
                lesson_id=l.id,
                subject_id=l.subject_id,
                subject=self.subjects.get(l.subject_id, "?"),
                day=day,
                start=l.start_time,
                end=l.end_time,
                room=l.room,
                teacher=l.teacher,
                kind=l.kind,
                one_off=l.on_date is not None,
            )
            for l in self.lessons
            if self._occurs(l, day)
        ]
        return sorted(result, key=lambda o: (o.start, o.end, o.subject))

    def week(self, any_day: date) -> list[tuple[date, list[Occurrence]]]:
        monday = any_day - timedelta(days=any_day.weekday())
        return [(monday + timedelta(days=i), self.day(monday + timedelta(days=i))) for i in range(7)]

    def next_lesson(self, now_local: datetime, horizon_days: int = 21) -> tuple[Occurrence, bool] | None:
        """Ближайшая пара. Второе значение — True, если пара идёт прямо сейчас."""
        for i in range(horizon_days + 1):
            d = now_local.date() + timedelta(days=i)
            for occ in self.day(d):
                start = datetime.combine(d, occ.start)
                end = datetime.combine(d, occ.end)
                if start <= now_local < end:
                    return occ, True
                if start > now_local:
                    return occ, False
        return None

    def next_for_subject(self, subject_id: int, after_local: datetime, horizon_days: int = 60) -> Occurrence | None:
        """Следующая пара по предмету (для срока «к следующей паре»)."""
        for i in range(horizon_days + 1):
            d = after_local.date() + timedelta(days=i)
            for occ in self.day(d):
                if occ.subject_id == subject_id and datetime.combine(d, occ.start) > after_local:
                    return occ
        return None

    def recurring(self) -> list[Lesson]:
        return sorted(
            (l for l in self.lessons if l.on_date is None),
            key=lambda l: (l.weekday, l.start_time, l.parity),
        )

    def time_slots(self) -> list[tuple[time, time]]:
        """Время пар, которое студент уже вводил, — чтобы предлагать его кнопками."""
        return sorted({(l.start_time, l.end_time) for l in self.lessons})


async def load(session: AsyncSession, user_id: int, week1: date) -> Schedule:
    lessons = list(await session.scalars(select(Lesson).where(Lesson.user_id == user_id)))
    subjects = {
        s.id: s.name for s in await session.scalars(select(Subject).where(Subject.user_id == user_id))
    }
    ids = [l.id for l in lessons]
    cancels: set[tuple[int, date]] = set()
    if ids:
        rows = await session.execute(
            select(LessonCancel.lesson_id, LessonCancel.day).where(LessonCancel.lesson_id.in_(ids))
        )
        cancels = {(lid, d) for lid, d in rows.all()}
    return Schedule(lessons, subjects, cancels, week1)


async def get_lesson(session: AsyncSession, user_id: int, lesson_id: int) -> Lesson | None:
    return await session.scalar(select(Lesson).where(Lesson.id == lesson_id, Lesson.user_id == user_id))


async def add_lesson(
    session: AsyncSession,
    user_id: int,
    subject_id: int,
    start: time,
    end: time,
    *,
    weekday: int | None = None,
    parity: int = 0,
    on_date: date | None = None,
    room: str | None = None,
    teacher: str | None = None,
    kind: str | None = None,
) -> Lesson:
    if (weekday is None) == (on_date is None):
        raise ScheduleError("Нужен либо день недели, либо дата")
    if end <= start:
        raise ScheduleError("Конец пары должен быть позже начала.")
    lesson = Lesson(
        user_id=user_id,
        subject_id=subject_id,
        weekday=weekday,
        parity=parity if on_date is None else 0,
        on_date=on_date,
        start_time=start,
        end_time=end,
        room=room,
        teacher=teacher,
        kind=kind,
    )
    session.add(lesson)
    await session.flush()
    return lesson


async def delete_lesson(session: AsyncSession, lesson: Lesson) -> None:
    await session.delete(lesson)
    await session.flush()


async def cancel_on(session: AsyncSession, lesson: Lesson, day: date) -> None:
    """Отменить пару в один день. Разовую пару просто удаляем."""
    if lesson.on_date is not None:
        await delete_lesson(session, lesson)
        return
    exists = await session.scalar(
        select(LessonCancel.id).where(LessonCancel.lesson_id == lesson.id, LessonCancel.day == day)
    )
    if not exists:
        session.add(LessonCancel(lesson_id=lesson.id, day=day))
        await session.flush()


async def restore_on(session: AsyncSession, lesson: Lesson, day: date) -> None:
    await session.execute(
        delete(LessonCancel).where(LessonCancel.lesson_id == lesson.id, LessonCancel.day == day)
    )


async def cancelled_on(session: AsyncSession, user_id: int, day: date) -> list[Lesson]:
    """Повторяющиеся пары, отменённые в этот день (чтобы их можно было вернуть)."""
    rows = await session.scalars(
        select(Lesson)
        .join(LessonCancel, LessonCancel.lesson_id == Lesson.id)
        .where(Lesson.user_id == user_id, LessonCancel.day == day)
    )
    return list(rows)
