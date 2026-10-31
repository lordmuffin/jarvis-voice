"""Structured (JSON) logging; every record carries a ``session_id`` field."""

import json
import logging
from collections.abc import MutableMapping
from typing import Any

_STD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "session_id": getattr(record, "session_id", "-"),
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _STD and k not in out:
                out[k] = v
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class _SessionDefault(logging.Filter):
    """Guarantees the ``session_id`` attribute exists on records logged without a session."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "session_id"):
            record.session_id = "-"
        return True


class SessionLogger(logging.LoggerAdapter[logging.Logger]):
    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        extra = dict(self.extra or {})
        extra.update(kwargs.get("extra") or {})
        kwargs["extra"] = extra
        return msg, kwargs


def session_logger(name: str, session_id: object) -> SessionLogger:
    return SessionLogger(logging.getLogger(name), {"session_id": str(session_id)})


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_SessionDefault())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
