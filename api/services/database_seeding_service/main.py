"""
Database Seeding Module - Main Entry Point

This module provides a single entry point for database seeding operations.
All configuration and processing logic is encapsulated in the ProcessingConfig
and internal modules to keep this file clean and focused.

Usage:
    from main import seed_database
    
    config = ProcessingConfig(
        csv_file_path="data.csv",
        csv_mapping={"email": "email", "name": "company"},
        csv_separator=",",
        batch_size=100,
        enable_web_scraping=True
    )
    
    results = seed_database(config)
"""

from typing import Any

from api.services.database_seeding_service.src.models import ProcessingConfig
from api.services.database_seeding_service.src.scraper import process_database_seeding


def seed_database(config: ProcessingConfig, job_id: str | None = None) -> dict[str, Any]:
    """
    Main entry point for database seeding.

    Processes a CSV file, enriches data with web scraping if enabled,
    resolves MX records, classifies emails, and inserts/updates contacts
    in the database.

    Args:
        config: ProcessingConfig object containing:
            - csv_file_path: Path to CSV file
            - csv_mapping: Dict mapping DB field names to CSV column names
            - csv_separator: CSV delimiter (default: ',')
            - batch_size: Insertion batch size (default: 100)
            - enable_web_scraping: Enable Selenium scraping (default: True)
            - skip_google_search: Skip Google search (default: False)
            - default_values: Default values for null fields (optional)
        job_id: Optional async job id used for websocket log streaming context.

    Returns:
        Dictionary with processing statistics:
            {
                'total_rows': int,
                'processed': int,
                'inserted': int,
                'updated': int,
                'skipped': int,
                'emails_found': int,
                'websites_found': int,
                'mx_failed': int,
                'errors': List[str]
            }

    Raises:
        ValueError: If config validation fails

    Example:
        >>> config = ProcessingConfig(
        ...     csv_file_path="contacts.csv",
        ...     csv_mapping={"email": "email", "company": "name"}
        ... )
        >>> results = seed_database(config)
        >>> print(f"Inserted: {results['inserted']}, Updated: {results['updated']}")
    """
    return process_database_seeding(config, job_id=job_id)