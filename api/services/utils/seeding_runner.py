import asyncio

from api.services.database_seeding_service import seed_database, seed_single_url
from api.services.database_seeding_service.src.models import ProcessingConfig
from api.services.utils.job_manager import job_store
from api.services.utils.log_socket import websocket_log_stream
from api.services.utils.ws_manager import ws_manager


async def run_seed_job(job_id: str) -> None:
    job = job_store.get_job(job_id)
    if job is None:
        return

    # If an explicit pause was requested before the runner started, respect it.
    if job_store.is_job_pause_requested(job_id):
        await ws_manager.send_event(job_id, "paused", job.to_dict())
        return

    loop = asyncio.get_running_loop()

    running_job = job_store.update(job_id, "status", "running")
    if running_job is not None:
        await ws_manager.send_event(job_id, "started", running_job.to_dict())

    with websocket_log_stream(job_id, loop) as seeding_logger:
        try:
            if str(job.payload.get("job_type", "")).lower() == "single_url":
                result = await asyncio.to_thread(
                    seed_single_url,
                    url=str(job.payload.get("target_url", "")),
                    enable_web_scraping=bool(job.payload.get("enable_web_scraping", True)),
                    skip_google_search=bool(job.payload.get("skip_google_search", False)),
                    sourcefile=job.payload.get("sourcefile"),
                    job_id=job_id,
                )
            else:
                config = ProcessingConfig(**job.payload)
                result = await asyncio.to_thread(seed_database, config, job_id)
            current_job = job_store.get_job(job_id)
            if current_job is not None and job_store.is_job_pause_requested(job_id):
                await ws_manager.send_event(job_id, "paused", current_job.to_dict())
                return

            job_store.update(job_id, "progress", {"result": result})
            completed_job = job_store.update(job_id, "status", "paused")
            if completed_job is not None:
                await ws_manager.send_event(job_id, "completed", completed_job.to_dict())

        except asyncio.CancelledError:
            # Ctrl+C hit — signal pause to the background thread and mark job as paused
            # to allow for potential resumption later.
            job_store.request_job_pause(job_id)
            seeding_logger.warning(f"Job {job_id} was stopped by shutdown.")
            await asyncio.sleep(0.5)  # Give thread time to notice and cleanup gracefully
            job_store.update(job_id, "progress", {"error": "Cancelled by server shutdown"})
            paused_job = job_store.update(job_id, "status", "paused")
            if paused_job is not None:
                await ws_manager.send_event(job_id, "paused", paused_job.to_dict())
            raise  # must re-raise so asyncio knows the task is done

        except Exception as exc:
            seeding_logger.error(f"Job {job_id} failed with error: {exc}")
            job_store.update(job_id, "progress", {"error": str(exc)})
            paused_job = job_store.update(job_id, "status", "paused")
            if paused_job is not None:
                await ws_manager.send_event(job_id, "failed", paused_job.to_dict())

    job_store.cleanup_pause_flag(job_id)
