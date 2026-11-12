"""Таблицы базы данных.

Все даты-время событий хранятся в UTC. Даты сдачи и время пар хранятся «как у студента»
(местная дата/время), потому что так их понимает сам студент.
"""

from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Дата-время, которое всегда возвращается в UTC с указанием пояса (и в SQLite, и в PostgreSQL)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Нужно время с часовым поясом")
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("platform", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Мессенджер и номер чата в нём. Ядро не знает, что это Telegram.
    platform: Mapped[str] = mapped_column(String(16), default="telegram")
    external_id: Mapped[str] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow")
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    digest_time: Mapped[time] = mapped_column(Time, default=time(20, 0))
    last_digest_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Понедельник недели, которая считается первой (нечётной). Пусто — 1 сентября.
    week1_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    university_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # manual — ручной режим; позже появятся подключённые вузы.
    mode: Mapped[str] = mapped_column(String(32), default="manual")
    onboarded: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Subject(Base):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Lesson(Base):
    """Пара. Либо повторяется (weekday + чётность недели), либо разовая (date)."""

    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0 — понедельник
    parity: Mapped[int] = mapped_column(Integer, default=0)  # 0 — каждую неделю, 1 — нечётные, 2 — чётные
    on_date: Mapped[date | None] = mapped_column("date", Date, nullable=True)  # для разовой пары
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    room: Mapped[str | None] = mapped_column(String(100), nullable=True)
    teacher: Mapped[str | None] = mapped_column(String(150), nullable=True)
    kind: Mapped[str | None] = mapped_column(String(100), nullable=True)  # лекция, семинар…
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")


class LessonCancel(Base):
    """Отмена повторяющейся пары в конкретный день."""

    __tablename__ = "lesson_cancels"
    __table_args__ = (UniqueConstraint("lesson_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)


class Homework(Base):
    __tablename__ = "homework"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Attachment(Base):
    """Фото или файл к заданию. Сам файл хранится у мессенджера, у нас — только его номер."""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    homework_id: Mapped[int] = mapped_column(ForeignKey("homework.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(16), default="telegram")
    kind: Mapped[str] = mapped_column(String(16))  # photo | document
    file_id: Mapped[str] = mapped_column(String(255))


class Reminder(Base):
    """Напоминание о задании. local_at — как указал студент, fire_at — тот же момент в UTC."""

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True)
    homework_id: Mapped[int] = mapped_column(ForeignKey("homework.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    local_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    fire_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class UniversityRequest(Base):
    """Заявка «подключите мой вуз». После удаления данных студента заявка остаётся без привязки к нему."""

    __tablename__ = "university_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
