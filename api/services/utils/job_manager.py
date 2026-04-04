import json
from datetime import datetime, timezone
from threading import Lock
from pathlib import Path
from typing import Any
from uuid import uuid4

from api.models import JobState, JobStatus


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_JOB_STORE_DIR = Path(__file__).resolve().parents[3] / "tmp" / "job_store"

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
        self._pause_flags: dict[str, bool] = {}
        self._lock = Lock()
        self._initialized = True
        self._load_persisted_jobs()

    def _job_file_path(self, job_id: str) -> Path:
        return _JOB_STORE_DIR / f"{job_id}.json"

    def _persist_job(self, job: JobState) -> None:
        _JOB_STORE_DIR.mkdir(parents=True, exist_ok=True)
        self._job_file_path(job.job_id).write_text(
            json.dumps(job.to_dict(), ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    def _load_persisted_jobs(self) -> None:
        if not _JOB_STORE_DIR.exists():
            return

        for job_file in _JOB_STORE_DIR.glob("*.json"):
            try:
                payload = json.loads(job_file.read_text(encoding="utf-8"))
                job = JobState(
                    job_id=payload["job_id"],
                    status=payload["status"],
                    payload=payload.get("payload", {}),
                    result=payload.get("result"),
                    error=payload.get("error"),
                    created_at=payload.get("created_at", ""),
                    started_at=payload.get("started_at"),
                    paused_at=payload.get("paused_at"),
                    completed_at=payload.get("completed_at"),
                    current_row=int(payload.get("current_row", 1)),
                    total_rows=int(payload.get("total_rows", 0)),
                )
                self._jobs[job.job_id] = job
            except Exception:
                continue

    def create_job(self, payload: dict[str, Any]) -> JobState:
        # Jobs start in queued state and are promoted by the async runner.
        with self._lock:
            job_id = str(uuid4())
            job = JobState(
                job_id=job_id,
                status="queued",
                payload=payload,
                created_at=_utc_now_iso(),
                current_row=1,
                total_rows=0,
            )
            self._jobs[job_id] = job
            self._persist_job(job)
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
                # Preserve partial stats when resuming from a paused checkpoint.
                if job.current_row <= 1:
                    job.result = None
                job.error = None
                job.paused_at = None
            elif status == "queued":
                job.paused_at = None
            elif status == "completed":
                job.result = result
                job.error = None
                job.completed_at = _utc_now_iso()
                job.paused_at = None
            elif status == "failed":
                job.error = error
                job.completed_at = _utc_now_iso()
                job.paused_at = None
            elif status == "paused":
                job.result = result if result is not None else job.result
                job.error = error
                job.paused_at = _utc_now_iso()
                job.completed_at = None
            self._persist_job(job)
            return job

    def update_progress(
        self,
        job_id: str,
        *,
        current_row: int | None = None,
        total_rows: int | None = None,
        result: dict[str, Any] | None = None,
    ) -> JobState | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None

            if current_row is not None:
                job.current_row = max(1, current_row)
            if total_rows is not None:
                job.total_rows = max(0, total_rows)
            if result is not None:
                job.result = result
            self._persist_job(job)
            return job

    def mark_job_cancelled(self, job_id: str) -> None:
        """Signal that a job should be cancelled (used during shutdown)."""
        with self._lock:
            self._cancel_flags[job_id] = True

    def request_job_pause(self, job_id: str) -> None:
        """Signal that a job should pause at the next checkpoint."""
        with self._lock:
            self._pause_flags[job_id] = True

    def is_job_cancelled(self, job_id: str) -> bool:
        """Check if a job has been signalled for cancellation."""
        with self._lock:
            return self._cancel_flags.get(job_id, False)

    def is_job_pause_requested(self, job_id: str) -> bool:
        """Check if a job should pause at the next checkpoint."""
        with self._lock:
            return self._pause_flags.get(job_id, False)

    def cleanup_cancel_flag(self, job_id: str) -> None:
        """Remove cancellation flag when job completes (normal or cancelled)."""
        with self._lock:
            self._cancel_flags.pop(job_id, None)

    def cleanup_pause_flag(self, job_id: str) -> None:
        """Remove pause flag when a job resumes or reaches a terminal state."""
        with self._lock:
            self._pause_flags.pop(job_id, None)

    def delete_persisted_job(self, job_id: str) -> None:
        with self._lock:
            try:
                self._job_file_path(job_id).unlink(missing_ok=True)
            except Exception:
                pass


job_store = JobStore()
