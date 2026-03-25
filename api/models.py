from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


JobStatus = Literal["queued", "running", "completed", "failed"]


@dataclass
class JobState:
    job_id: str
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class SeedDatabaseRequest(BaseModel):
    csv_mapping: dict[str, str]
    csv_separator: str = ","
    batch_size: int = Field(default=100, ge=1)
    enable_web_scraping: bool = True
    skip_google_search: bool = False
    default_values: dict[str, Any] | None = None
    sourcefile: str | None = None

    @field_validator("csv_mapping")
    @classmethod
    def validate_mapping(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("csv_mapping cannot be empty")
        return value


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
