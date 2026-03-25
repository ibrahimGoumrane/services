"""Centralized logging configuration for the application."""
import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from api.services.utils.ws_manager import ws_manager

_FORMATTER = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_BATCH_RE = re.compile(
    r"Batch:\s*(\d+)\s+inserted,\s*(\d+)\s+updated\s*\|\s*Progress:\s*(\d+)\s*/\s*(\d+)",
    re.IGNORECASE,
)


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
            # Always emit the log line first
            self._send({
                "type": "logs",
                "message": self.format(record),
                "level": record.levelname,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Emit progress only for batch-completion log lines.
            message = record.getMessage()

            batch_match = _BATCH_RE.search(message)
            if batch_match:
                self._progress["inserted"] += int(batch_match.group(1))
                self._progress["updated"] += int(batch_match.group(2))
                self._progress["processed"] = max(self._progress["processed"], int(batch_match.group(3)))
                self._send({
                    "type": "progress",
                    "payload": self._progress.copy(),
                })

            if record.levelno >= logging.ERROR:
                self._progress["errors"] += 1

        except Exception:
            print("Failed to send log record to WebSocket subscribers", flush=True)


def setup_logging(
    log_dir: str = "logs",
    module_name: str = "__main__",
    job_id: str | None = None,
) -> logging.Logger:
    """Set up and return a configured logger with file output."""
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(module_name)
    logger.setLevel(logging.DEBUG)

    if any(getattr(h, "_tag", "") == "file" for h in logger.handlers):
        return logger

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{module_name}_{job_id}" if job_id else module_name
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f"{prefix}_{timestamp}.log"), encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_FORMATTER)
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