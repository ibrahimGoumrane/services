from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from api.models import JobState, JobStatus


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class JobStore:
    _instance: "JobStore | None" = None

    def __new__(cls) -> "JobStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        # Shared in-memory state for queued/running/completed jobs.
        self._jobs: dict[str, JobState] = {}
        self._cancel_flags: dict[str, bool] = {}  # Tracks which jobs should be cancelled
        self._lock = Lock()
        self._initialized = True

    def create_job(self, payload: dict[str, Any]) -> JobState:
        # Jobs start in queued state and are promoted by the async runner.
        with self._lock:
            job_id = str(uuid4())
            job = JobState(
                job_id=job_id,
                status="queued",
                payload=payload,
                created_at=_utc_now_iso(),
            )
            self._jobs[job_id] = job
            return job

    def get_job(self, job_id: str) -> JobState | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[JobState]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda job: job.created_at, reverse=True)
        return jobs

    def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> JobState | None:
        # Single transition utility used by the runner for all lifecycle updates.
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None

            job.status = status
            if status == "running":
                job.started_at = _utc_now_iso()
                job.completed_at = None
                job.result = None
                job.error = None
            elif status == "completed":
                job.result = result
                job.error = None
                job.completed_at = _utc_now_iso()
            elif status == "failed":
                job.error = error
                job.completed_at = _utc_now_iso()
            return job

    def mark_job_cancelled(self, job_id: str) -> None:
        """Signal that a job should be cancelled (used during shutdown)."""
        with self._lock:
            self._cancel_flags[job_id] = True

    def is_job_cancelled(self, job_id: str) -> bool:
        """Check if a job has been signalled for cancellation."""
        with self._lock:
            return self._cancel_flags.get(job_id, False)

    def cleanup_cancel_flag(self, job_id: str) -> None:
        """Remove cancellation flag when job completes (normal or cancelled)."""
        with self._lock:
            self._cancel_flags.pop(job_id, None)


job_store = JobStore()
