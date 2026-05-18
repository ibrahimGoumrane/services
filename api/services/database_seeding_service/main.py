"""
Database Seeding - Entry Point

Provides high-level functions to seed the database from a CSV file
or a single URL using the processing pipeline.
"""

from typing import Any

from api.services.database_seeding_service.src.models import ProcessingConfig
from api.services.database_seeding_service.src.scraper import (
    process_database_seeding,
    process_single_url_seeding,
)


def seed_database(config: ProcessingConfig, job_id: str | None = None) -> dict[str, Any]:
    """
    Run the CSV-based seeding pipeline.

    Args:
        config: Processing configuration (CSV path, mapping, options)
        job_id: Optional job identifier for async logging

    Returns:
        Processing statistics (processed, inserted, updated, errors, etc.)
    """
    return process_database_seeding(config, job_id=job_id)


def seed_single_url(
    urls: list[str],
    enable_web_scraping: bool = True,
    skip_google_search: bool = False,
    sourcefile: str | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    """
    Process and persist data from multiple URLs sequentially.
    """
    return process_single_url_seeding(
        urls=urls,
        enable_web_scraping=enable_web_scraping,
        skip_google_search=skip_google_search,
        sourcefile=sourcefile,
        job_id=job_id,
    )