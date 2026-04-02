"""Centralized logging configuration for the application."""
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import List

from api.services.utils.ws_manager import ws_manager

_FORMATTER = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_BATCH_RE = re.compile(
    r"Batch:\s*(\d+)\s+inserted,\s*(\d+)\s+updated\s*\|\s*Progress:\s*(\d+)\s*/\s*(\d+)",
    re.IGNORECASE,
)

_START_RE = re.compile(r"^SEED_START\b")
_END_RE = re.compile(r"^SEED_END\b")


class WebSocketLogHandler(logging.Handler):
    """Forward logs and progress updates to WebSocket subscribers for a specific job."""

    def __init__(self, job_id: str, event_loop: asyncio.AbstractEventLoop) -> None:
        super().__init__(level=logging.INFO)
        self.job_id = job_id
        self.event_loop = event_loop
        self._progress = {"processed": 0, "inserted": 0, "updated": 0, "errors": 0}
        self.setFormatter(_FORMATTER)

    def _send(self, data: dict) -> None:
        asyncio.run_coroutine_threadsafe(
            ws_manager.send_event(self.job_id, "stream", data),
            self.event_loop,
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            batch_match = _BATCH_RE.search(message)

            # Forward only high-level lifecycle and batch logs to keep websocket output concise.
            if _START_RE.search(message) or _END_RE.search(message) or batch_match:
                self._send({
                    "type": "logs",
                    "message": self.format(record),
                    "level": record.levelname,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            # Emit structured progress only for batch-completion log lines.
            if batch_match:
                self._progress["inserted"] += int(batch_match.group(1))
                self._progress["updated"] += int(batch_match.group(2))
                self._progress["processed"] = max(self._progress["processed"], int(batch_match.group(3)))
                self._send({
                    "type": "progress",
                    "payload": {
                        **self._progress,
                        "total": int(batch_match.group(4)),
                    },
                })

        except Exception:
            print("Failed to send log record to WebSocket subscribers", flush=True)


class BufferedFileHandler(logging.Handler):
    """Buffer formatted log lines and append them to disk in batches."""

    def __init__(self, file_path: str, buffer_size: int = 1) -> None:
        super().__init__(level=logging.DEBUG)
        self.file_path = file_path
        self._buffer: List[str] = []
        self.setFormatter(_FORMATTER)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        if not self._buffer:
            return

        try:
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(self.file_path, "a", encoding="utf-8") as file_handle:
                file_handle.write("\n".join(self._buffer))
                file_handle.write("\n")
            self._buffer.clear()
        except Exception:
            pass


def flush_buffered_log_handlers(logger: logging.Logger) -> None:
    """Flush buffered file handlers attached to a logger."""
    for handler in logger.handlers:
        if isinstance(handler, BufferedFileHandler):
            handler.flush()


def setup_logging(
    log_dir: str = "logs",
    module_name: str = "__main__",
    job_id: str | None = None,
    buffer_size: int = 1,
) -> logging.Logger:
    """Set up and return a configured logger with file output."""
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(module_name)
    logger.setLevel(logging.DEBUG)

    if any(getattr(h, "_tag", "") == "file" for h in logger.handlers):
        return logger

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{module_name}_{job_id}" if job_id else module_name
    file_handler = BufferedFileHandler(
        os.path.join(log_dir, f"{prefix}_{timestamp}.log"),
        buffer_size=buffer_size,
    )
    file_handler._tag = "file"
    logger.addHandler(file_handler)

    return logger


def attach_websocket_log_handler(
    logger: logging.Logger,
    job_id: str | None,
    event_loop: asyncio.AbstractEventLoop,
) -> WebSocketLogHandler | None:
    """Attach WebSocket log handler when a job ID is available."""
    if not job_id:
        return None
    handler = WebSocketLogHandler(job_id=job_id, event_loop=event_loop)
    logger.addHandler(handler)
    return handler


def detach_websocket_log_handler(
    logger: logging.Logger,
    handler: WebSocketLogHandler | None,
) -> None:
    """Detach WebSocket log handler if attached."""
    if handler:
        logger.removeHandler(handler)


def get_logger(module_name: str) -> logging.Logger:
    """Get or create a logger for a specific module."""
    return logging.getLogger(module_name)