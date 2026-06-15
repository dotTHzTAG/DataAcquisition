from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

MAX_APPLICATION_LOG_BYTES = 100 * 1024 * 1024


class QtLogSignals(QObject):
    message_emitted = pyqtSignal(str)


class QtLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.signals = QtLogSignals()
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s", "%H:%M:%S"))

    @property
    def message_emitted(self):
        return self.signals.message_emitted

    def emit(self, record: logging.LogRecord) -> None:
        self.signals.message_emitted.emit(self.format(record))


def configure_application_logger(log_path: Path) -> tuple[logging.Logger, QtLogHandler]:
    for legacy_name in ("catx_application.log", "catx.log"):
        legacy_log_path = log_path.with_name(legacy_name)
        if legacy_log_path.exists() and not log_path.exists():
            try:
                legacy_log_path.replace(log_path)
            except OSError:
                pass

    logger = logging.getLogger("catx")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    qt_handler = QtLogHandler()
    logger.addHandler(file_handler)
    logger.addHandler(qt_handler)
    return logger, qt_handler


def clear_application_log(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        if not isinstance(handler, logging.FileHandler):
            continue
        handler.acquire()
        try:
            handler.flush()
            handler.stream.seek(0)
            handler.stream.truncate()
            handler.stream.flush()
        finally:
            handler.release()


def application_log_exceeds_limit(
    log_path: Path, max_bytes: int = MAX_APPLICATION_LOG_BYTES
) -> bool:
    try:
        return log_path.stat().st_size >= max_bytes
    except OSError:
        return False
