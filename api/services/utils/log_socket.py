"""Logger and WebSocket handler lifecycle helpers."""

import asyncio
import logging
from contextlib import contextmanager
from typing import Generator

from api.services.utils.logging_config import (
    attach_websocket_log_handler,
    detach_websocket_log_handler,
    get_seeding_logger as _get_seeding_logger,
)


def get_seeding_logger() -> logging.Logger:
    """Return the dedicated dbSeeder logger with DebugFilter and propagation disabled."""
    seeding_logger = _get_seeding_logger("dbSeeder")
    seeding_logger.propagate = False
    return seeding_logger


@contextmanager
def websocket_log_stream(
    job_id: str,
    event_loop: asyncio.AbstractEventLoop,
    max_size: int = 5,
) -> Generator[logging.Logger, None, None]:
    """
    Context manager that attaches a WebSocket log handler to the dbSeeder logger
    for the duration of the block and detaches it on exit.

    Args:
        job_id: The job identifier used for WebSocket routing.
        event_loop: The asyncio event loop to schedule WS sends on.
        max_size: Number of log records to buffer before flushing to WebSocket.
    """
    logger = get_seeding_logger()
    ws_handler = attach_websocket_log_handler(
        logger, job_id=job_id, event_loop=event_loop, max_size=max_size
    )
    try:
        yield logger
    finally:
        if ws_handler is not None:
            ws_handler.flush()
        detach_websocket_log_handler(logger, ws_handler)
