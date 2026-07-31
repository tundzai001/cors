"""Bounded queue logging so console or disk I/O cannot freeze asyncio."""

from __future__ import annotations

import logging
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
import queue
import sys
import threading
from typing import Iterable, TextIO


class DroppingQueueHandler(QueueHandler):
    """A QueueHandler that drops overflow without writing errors to stderr."""

    def __init__(self, log_queue: queue.Queue) -> None:
        super().__init__(log_queue)
        self._dropped = 0
        self._dropped_lock = threading.Lock()

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            with self._dropped_lock:
                self._dropped += 1

    @property
    def dropped_count(self) -> int:
        with self._dropped_lock:
            return self._dropped


class NonBlockingLoggingRuntime:
    def __init__(
        self,
        log_queue: queue.Queue,
        queue_handler: DroppingQueueHandler,
        listener: QueueListener,
    ) -> None:
        self.queue = log_queue
        self.queue_handler = queue_handler
        self.listener = listener

    def snapshot(self) -> dict[str, int]:
        return {
            "queue_depth": self.queue.qsize(),
            "queue_capacity": self.queue.maxsize,
            "dropped_records": self.queue_handler.dropped_count,
        }


def configure_nonblocking_logging(
    *,
    log_file: str,
    log_format: str,
    level: int = logging.INFO,
    queue_size: int = 20_000,
    stream: TextIO | None = None,
    isolated_logger_names: Iterable[str] = ("uvicorn", "uvicorn.error", "uvicorn.access"),
) -> NonBlockingLoggingRuntime:
    """Move all potentially blocking writes onto one daemon listener thread."""

    formatter = logging.Formatter(log_format)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(stream or sys.stderr)
    stream_handler.setFormatter(formatter)

    log_queue: queue.Queue = queue.Queue(maxsize=max(100, int(queue_size)))
    queue_handler = DroppingQueueHandler(log_queue)
    queue_handler.setLevel(level)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(queue_handler)

    for logger_name in isolated_logger_names:
        target = logging.getLogger(logger_name)
        target.handlers.clear()
        target.addHandler(queue_handler)
        target.propagate = False

    listener = QueueListener(
        log_queue,
        file_handler,
        stream_handler,
        respect_handler_level=True,
    )
    listener.start()
    return NonBlockingLoggingRuntime(log_queue, queue_handler, listener)
