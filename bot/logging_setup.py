"""Журнал работы. Тексты сообщений студентов в журнал не пишутся, а токены вырезаются."""

import logging
import re

# Токен бота (123456:ABC…) и ключи вида token=… не должны попасть в журнал.
_SECRET_RES = [
    re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"(?i)(token|password|passwd|wstoken|privatetoken)=([^&\s]+)"),
]


def redact(text: str) -> str:
    text = _SECRET_RES[0].sub("***", text)
    return _SECRET_RES[1].sub(r"\1=***", text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
