"""Logging: JSON lines, to a private rotating file when ``log_dir`` is set, else to stderr.

Only the fields in ``FIELDS`` are copied from a record, so a stray ``extra=`` cannot put a
password or a cookie in the log. The file is 0600 inside a 0700 folder, rotated at 10 MiB, five
kept.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path

from ..adapters.files import PrivacyError, create_private_file, ensure_private_file
from ..bootstrap import SettingsError

FIELDS = (
    "ask_id",
    "error_id",
    "kind",
    "trace",
    "event",
    "request_id",
    "username",
    "address",
    "method",
    "route",
    "status",
    "duration_ms",
    "session_id",
    "target",
    "role",
    "actor",
)
LOG_NAME = "api.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                data[name] = value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Rotation opens a fresh file; make it 0600 too, not whatever the umask says."""

    def _open(self) -> TextIOWrapper:
        descriptor = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        return os.fdopen(descriptor, "a", encoding=self.encoding)


def configure_logging(log_dir: Path | None) -> None:
    """Send the service's own loggers (``research_api.*``) to the file or to stderr.

    ``SettingsError`` when the log file exists and is open to other accounts.
    """
    logger = logging.getLogger("research_api")
    for old in list(logger.handlers):
        logger.removeHandler(old)
        old.close()
    handler: logging.Handler
    if log_dir is None:
        handler = logging.StreamHandler(sys.stderr)
    else:
        path = log_dir / LOG_NAME
        create_private_file(path)
        try:
            ensure_private_file(path)  # a log kept from before is checked like the database
        except PrivacyError as e:
            raise SettingsError(str(e)) from e
        handler = PrivateRotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
