# Database Seeding Service

This project currently exposes that service through the root entry module: `main.py`.

## Role

The service orchestrates the full CSV-to-database pipeline:

- Load and validate CSV input
- Optionally enrich missing website/email data via web scraping
- Resolve MX records
- Classify emails (generic vs non-generic)
- Insert/update contacts in batches
- Return processing statistics

## Main Entry Point

Function: `seed_database(config: ProcessingConfig) -> Dict[str, Any]`

Location:

- `main.py`

Internal orchestrator called by the entrypoint:

- `src/scraper.py` (`process_database_seeding`)

## Input Contract

Input type: `ProcessingConfig` (defined in `src/models.py`)

Main fields:

- `csv_file_path: str`
- `csv_mapping: Dict[str, str]`
- `csv_separator: str = ","`
- `batch_size: int = 100`
- `enable_web_scraping: bool = True`
- `skip_google_search: bool = False`
- `default_values: Optional[Dict[str, Any]] = None`

## Expected Output

Return type: `Dict[str, Any]`

Typical result shape:

```python
{
    "total_rows": int,
    "processed": int,
    "inserted": int,
    "updated": int,
    "skipped": int,
    "emails_found": int,
    "websites_found": int,
    "mx_failed": int,
    "errors": list[str],
}
```

## Minimal Usage Example

```python
from main import seed_database
from src.models import ProcessingConfig

config = ProcessingConfig(
    csv_file_path="contacts.csv",
    csv_mapping={
        "email": "email",
        "company": "company",
        "url": "website",
    },
    csv_separator=",",
    batch_size=100,
    enable_web_scraping=True,
    skip_google_search=False,
)

result = seed_database(config)
print(result)
```

## Notes

- Keep `init_files/` and `init_scripts/` for initialization workflows.
- The API boundary for future FastAPI integration is `seed_database(config)`.
