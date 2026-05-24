from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

JobStatus = Literal["running", "paused", "completed"]


@dataclass
class JobState:
    job_id: str
    status: JobStatus
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = ""
    started_at: str | None = None
    paused_at: str | None = None
    completed_at: str | None = None
    current_row: int = 1
    total_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "paused_at": self.paused_at,
            "completed_at": self.completed_at,
            "current_row": self.current_row,
            "total_rows": self.total_rows,
        }


class CsvMapping(BaseModel):
    """Mapping from CSV column names to database field names."""

    fullname: str | None = None
    fname: str | None = None
    lname: str | None = None
    name: str | None = None
    email: str | None = None
    url: str | None = None
    phone: str | None = None
    mobile: str | None = None
    fax: str | None = None
    linkedin: str | None = None
    position: str | None = None
    address: str | None = None
    city: str | None = None
    zip: str | None = None
    country: str | None = None
    location: str | None = None
    urlcontactform: str | None = None
    image: str | None = None
    sourcefile: str | None = None
    ca: str | None = None
    activite: str | None = None
    secteur: str | None = None

    @property
    def has_mappings(self) -> bool:
        """Check if at least one mapping field is provided."""
        return any(getattr(self, f) for f in self.__class__.model_fields)

    def get(self, field: str, default: Any = None) -> Any:
        """Get the value of a mapping field, returning default if not set."""
        return getattr(self, field, default)

    @model_validator(mode="before")
    @classmethod
    def strip_values(cls, data: Any) -> Any:
        """Strip whitespace from all string values in the input data."""
        if isinstance(data, dict):
            cleaned: dict[str, Any] = {}
            for k, v in data.items():
                if isinstance(v, str):
                    v = v.strip()
                    if v == "":
                        v = None
                cleaned[k] = v
            return cleaned
        return data


class SeedDatabaseRequest(BaseModel):
    csv_mapping: CsvMapping
    csv_separator: str = ","
    batch_size: int = Field(default=5, ge=1)
    enable_web_scraping: bool = True
    skip_google_search: bool = False
    enable_person_search: bool = True
    enable_company_search: bool = True
    default_values: dict[str, Any] | None = None
    sourcefile: str | None = None


class UrlScrapeRequest(BaseModel):
    urls: list[str]
    enable_web_scraping: bool = True
    skip_google_search: bool = False
    sourcefile: str | None = None

    @field_validator("urls")
    @classmethod
    def validate_urls(cls, value: list[str]) -> list[str]:
        validated: list[str] = []
        for raw in value:
            raw = (raw or "").strip()
            if not raw:
                continue
            normalized = raw
            if "://" not in normalized:
                normalized = f"https://{normalized}"
            parsed = urlparse(normalized)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"url must be a valid http(s) URL: {raw}")
            validated.append(normalized)
        if not validated:
            raise ValueError("at least one valid URL is required")
        return validated


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
    paused_at: str | None = None
    completed_at: str | None = None
    current_row: int = 1
    total_rows: int = 0
