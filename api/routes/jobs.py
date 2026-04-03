import asyncio
import csv
import json
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import PlainTextResponse

from api.models import CreateJobResponse, JobStatusResponse, SeedDatabaseRequest
from api.services.utils.job_manager import job_store
from api.services.utils.seeding_runner import run_seed_job
from api.services.utils.ws_manager import ws_manager

router = APIRouter(tags=["jobs"])

UPLOADS_DIR = Path(__file__).resolve().parents[2] / "uploads"


def _parse_json_field(field_name: str, value: str | None, default: dict | None = None) -> dict | None:
    if value is None:
        return default
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must be valid JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field_name} must be a JSON object",
        )
    return parsed


async def _save_upload(file: UploadFile) -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename).suffix or ".csv"
    dest = UPLOADS_DIR / f"{uuid4()}{suffix}"
    dest.write_bytes(await file.read())
    await file.close()
    return dest


@router.post("/jobs", response_model=CreateJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    csv_file: Optional[UploadFile] = File(None),
    csv_text: Optional[str] = Form(None),
    csv_mapping: str = Form(...),
    csv_separator: str = Form(","),
    batch_size: int = Form(100),
    enable_web_scraping: bool = Form(True),
    skip_google_search: bool = Form(False),
    default_values: str | None = Form(None),
) -> CreateJobResponse:
    # Validate that exactly one input method is provided
    if not csv_file and not csv_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either csv_file or csv_text must be provided",
        )
    
    if csv_file and csv_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either csv_file or csv_text, not both",
        )

    # Handle file upload
    if csv_file:
        if not csv_file.filename:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="csv_file must include a filename",
            )
        saved_path = await _save_upload(csv_file)
        source_filename = csv_file.filename
    
    # Handle text input
    else:
        if not csv_text or not csv_text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="csv_text is empty",
            )
        # Save pasted text to a temporary file
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOADS_DIR / f"{uuid4()}.csv"
        dest.write_text(csv_text.strip(), encoding="utf-8")
        saved_path = dest
        source_filename = "pasted_data.csv"

    job_payload = SeedDatabaseRequest(
        csv_mapping=_parse_json_field("csv_mapping", csv_mapping) or {},
        csv_separator=csv_separator,
        batch_size=batch_size,
        enable_web_scraping=enable_web_scraping,
        skip_google_search=skip_google_search,
        default_values=_parse_json_field("default_values", default_values),
        sourcefile=source_filename,
    ).model_dump()
    job_payload["csv_file_path"] = str(saved_path)

    job = job_store.create_job(job_payload)
    await ws_manager.send_event(job.job_id, "queued", job.to_dict())
    asyncio.create_task(run_seed_job(job.job_id))

    return CreateJobResponse(job_id=job.job_id, status=job.status)


@router.post("/jobs/{job_id}/pause", response_model=JobStatusResponse)
async def pause_job(job_id: str) -> JobStatusResponse:
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    job_store.request_job_pause(job_id)
    paused_job = job_store.update_status(job_id, "paused")
    if paused_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    await ws_manager.send_event(job_id, "paused", paused_job.to_dict())
    return JobStatusResponse(**paused_job.to_dict())


@router.post("/jobs/{job_id}/resume", response_model=JobStatusResponse)
async def resume_job(job_id: str) -> JobStatusResponse:
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.status != "paused":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only paused jobs can be resumed")

    job_store.cleanup_pause_flag(job_id)
    queued_job = job_store.update_status(job_id, "queued")
    if queued_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    await ws_manager.send_event(job_id, "queued", queued_job.to_dict())
    asyncio.create_task(run_seed_job(job_id))
    return JobStatusResponse(**queued_job.to_dict())


@router.post("/jobs/{job_id}/stop", response_model=JobStatusResponse)
async def stop_job(job_id: str) -> JobStatusResponse:
    return await pause_job(job_id)


@router.get("/jobs", response_model=list[JobStatusResponse])
async def list_jobs() -> list[JobStatusResponse]:
    jobs = job_store.list_jobs()
    return [JobStatusResponse(**job.to_dict()) for job in jobs]


@router.post("/jobs/csv/headers")
async def preview_csv_headers(
    csv_file: Optional[UploadFile] = File(None),
    csv_text: Optional[str] = Form(None),
    csv_separator: str = Form(","),
) -> dict[str, list[str]]:
    # Validate that exactly one input method is provided
    if not csv_file and not csv_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either csv_file or csv_text must be provided",
        )
    
    if csv_file and csv_text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either csv_file or csv_text, not both",
        )

    # Process file input
    if csv_file:
        if not csv_file.filename:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="csv_file must include a filename",
            )

        raw_bytes = await csv_file.read()
        await csv_file.close()
        if not raw_bytes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="csv_file is empty",
            )

        try:
            content = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="csv_file must be UTF-8 encoded",
            ) from exc
    
    # Process text input
    else:
        if not csv_text or not csv_text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="csv_text is empty",
            )
        content = csv_text.strip()

    lines = content.splitlines()
    if not lines:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CSV content does not contain headers",
        )

    try:
        parsed_headers = next(csv.reader([lines[0]], delimiter=csv_separator))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to parse headers with provided csv_separator",
        ) from exc

    headers = [str(header).strip() for header in parsed_headers]
    if not any(headers):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CSV headers are empty",
        )

    return {"headers": headers}


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str) -> JobStatusResponse:
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobStatusResponse(**job.to_dict())


@router.get("/jobs/{job_id}/logs", response_class=PlainTextResponse)
async def get_job_logs(job_id: str) -> PlainTextResponse:
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    logs = job_store.get_job_logs(job_id)
    if not logs:
        return PlainTextResponse("No logs captured for this job yet.")

    return PlainTextResponse("\n".join(logs))