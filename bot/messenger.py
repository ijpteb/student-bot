"""Как ядро отправляет сообщения, не зная, какой мессенджер под ним.

Для Telegram это реализовано в bot/telegram/notifier.py. Чтобы добавить другой мессенджер,
достаточно написать такой же класс для него.
"""

from typing import Protocol

from bot.models import Attachment, User


class RecipientUnavailable(Exception):
    """Студент заблокировал бота или удалил чат — писать ему больше нельзя."""


class TemporaryFailure(Exception):
    """Нет связи с мессенджером или он временно не отвечает — стоит повторить позже."""


class MessageRejected(Exception):
    """Мессенджер отказался принять это сообщение (например, слишком длинное) — повтор не поможет."""


class Notifier(Protocol):
    async def send_text(self, user: User, text: str, homework_id: int | None = None) -> None:
        """Отправить сообщение. homework_id — показать под ним кнопки задания."""

    async def send_attachments(self, user: User, files: list[Attachment]) -> None: ...

    async def notify_admin(self, text: str) -> None: ...
