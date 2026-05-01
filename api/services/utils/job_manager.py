import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from api.models import JobState, JobStatus


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_JOB_STORE_DIR = Path(__file__).resolve().parents[3] / "tmp" / "job_store"


def _derive_job_id(payload: dict[str, Any]) -> str:
    """Derive a human-readable job ID from the payload."""
    sourcefile = payload.get("sourcefile")
    if sourcefile:
        return Path(sourcefile).stem
    return str(uuid4())


class JobStore:
    _instance: "JobStore | None" = None

    def __new__(cls) -> "JobStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        self._jobs: dict[str, JobState] = {}
        self._flags: dict[str, dict[str, bool]] = {}
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
                # Normalize legacy statuses to the two-state model.
                raw_status = payload.get("status", "paused")
                if raw_status not in ("running", "paused"):
                    raw_status = "paused"
                job = JobState(
                    job_id=payload["job_id"],
                    status=raw_status,
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
        with self._lock:
            base_id = _derive_job_id(payload)
            job_id = base_id
            counter = 1
            # Ensure uniqueness of job_id by appending a counter if needed.
            while job_id in self._jobs:
                job_id = f"{base_id}_{counter}"
                counter += 1

            job = JobState(
                job_id=job_id,
                status="paused",
                payload=payload,
                created_at=_utc_now_iso(),
                current_row=0,
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

    def update(self, job_id: str, key: str, value: Any) -> JobState | None:
        """Update a job by key. Supported keys: 'status', 'progress'."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None

            if key == "status":
                if value not in ("running", "paused"):
                    raise ValueError("status must be 'running' or 'paused'")
                job.status = value
                if value == "running":
                    job.started_at = _utc_now_iso()
                    job.paused_at = None
                    job.completed_at = None
                elif value == "paused":
                    job.paused_at = _utc_now_iso()
            elif key == "progress":
                if not isinstance(value, dict):
                    raise ValueError("progress must be a dict")
                if "current_row" in value:
                    job.current_row = max(1, value["current_row"])
                if "total_rows" in value:
                    job.total_rows = max(0, value["total_rows"])
                if "result" in value:
                    job.result = value["result"]
                if "error" in value:
                    job.error = value["error"]
                if "completed_at" in value:
                    job.completed_at = value["completed_at"]
            else:
                raise ValueError(f"Unsupported update key: {key}")

            self._persist_job(job)
            return job

    def request_job_pause(self, job_id: str) -> None:
        """Signal that a job should pause at the next checkpoint."""
        with self._lock:
            self._flags[job_id] = {"pause_requested": True}

    def is_job_pause_requested(self, job_id: str) -> bool:
        """Check if a job should pause at the next checkpoint."""
        with self._lock:
            return self._flags.get(job_id, {}).get("pause_requested", False)

    def cleanup_pause_flag(self, job_id: str) -> None:
        """Remove pause flag when a job resumes or reaches a terminal state."""
        with self._lock:
            self._flags.pop(job_id, None)

    def delete_persisted_job(self, job_id: str) -> None:
        with self._lock:
            try:
                self._job_file_path(job_id).unlink(missing_ok=True)
            except Exception:
                pass


job_store = JobStore()
