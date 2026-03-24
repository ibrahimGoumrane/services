import asyncio
import logging
from api.services.database_seeding_service import seed_database
from api.services.database_seeding_service.src.models import ProcessingConfig
from api.services.database_seeding_service.src.utils.logging_config import (
    attach_websocket_log_handler,
    detach_websocket_log_handler,
)
from api.services.utils.job_manager import job_store
from api.services.utils.ws_manager import ws_manager


async def run_seed_job(job_id: str) -> None:
    job = job_store.get_job(job_id)
    if job is None:
        return

    loop = asyncio.get_running_loop()
    seeding_logger = logging.getLogger("dbSeeder")
    seeding_logger.propagate = False
    ws_stream_handler = attach_websocket_log_handler(seeding_logger, job_id=job_id, event_loop=loop)

    running_job = job_store.update_status(job_id, "running")
    if running_job is not None:
        await ws_manager.send_event(job_id, "started", running_job.to_dict())

    try:
        config = ProcessingConfig(**job.payload)
        result = await asyncio.to_thread(seed_database, config, job_id)
        completed_job = job_store.update_status(job_id, "completed", result=result)
        if completed_job is not None:
            await ws_manager.send_event(job_id, "completed", completed_job.to_dict())

    except asyncio.CancelledError:
        # Ctrl+C hit — signal cancellation to the background thread and mark job as failed
        job_store.mark_job_cancelled(job_id)
        seeding_logger.warning(f"Job {job_id} was cancelled by shutdown.")
        await asyncio.sleep(0.5)  # Give thread time to notice and cleanup gracefully
        failed_job = job_store.update_status(job_id, "failed", error="Cancelled by server shutdown")
        if failed_job is not None:
            await ws_manager.send_event(job_id, "failed", failed_job.to_dict())
        raise  # must re-raise so asyncio knows the task is done

    except Exception as exc:
        seeding_logger.error(f"Job {job_id} failed with error: {exc}")
        failed_job = job_store.update_status(job_id, "failed", error=str(exc))
        if failed_job is not None:
            await ws_manager.send_event(job_id, "failed", failed_job.to_dict())

    finally:
        detach_websocket_log_handler(seeding_logger, ws_stream_handler)
        job_store.cleanup_cancel_flag(job_id)
