import json
import logging
import sys
from typing import Any


class _CorrelationIdFilter(logging.Filter):
    """Injects the current request correlation ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Import here to avoid a circular import at module load time.
        from app.core.correlation import get_correlation_id

        record.correlation_id = get_correlation_id()  # type: ignore[attr-defined]
        return True


class _JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        cid = getattr(record, "correlation_id", "")
        if cid:
            entry["cid"] = cid
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(_CorrelationIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]

    if not debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
