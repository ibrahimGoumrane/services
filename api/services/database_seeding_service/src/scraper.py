"""Main CSV processing and database seeding orchestrator"""

import pandas as pd
import time
from typing import Dict, Tuple, Optional, Any, Set, List

from .utils import contact_repository, data_transformers, email_classifiers

from .models import ProcessingConfig
from .utils.logging_config import setup_logging, get_logger
from .utils import (
    mx_resolver
)
from .utils.website_validator import WebsiteEmailValidator
from api.services.utils.job_manager import job_store


logger = get_logger(__name__)


def process_database_seeding(
    config: ProcessingConfig,
    job_id: str | None = None,
) -> Dict[str, Any]:
    """
    Main function: reads CSV, enriches data from web, resolves MX records,
    classifies emails, and batch-inserts into database.
    
    Args:
        config: ProcessingConfig object with all settings
    
    Returns:
        Dictionary with processing statistics
    """
    # Setup logging and bind module logger to the seeding logger.
    global logger
    logger = setup_logging(module_name="dbSeeder", job_id=job_id)
    
    logger.info("=" * 80)
    logger.info("STARTING DATABASE SEEDING PROCESS")
    logger.info("=" * 80)
    logger.info(f"CSV File: {config.csv_file_path}")
    if job_id:
        logger.info(f"Job ID: {job_id}")
    logger.info(f"CSV Separator: '{config.csv_separator}'")
    logger.info(f"Batch Size: {config.batch_size}")
    logger.info(f"Web Scraping: {'Enabled' if config.enable_web_scraping else 'Disabled'}")
    if config.enable_web_scraping:
        logger.info(f"Google Search: {'Enabled' if not config.skip_google_search else 'Disabled'}")
    logger.info("=" * 80)
    
    # Initialize statistics
    stats = {
        'total_rows': 0,
        'processed': 0,
        'inserted': 0,
        'updated': 0,
        'skipped': 0,
        'emails_found': 0,
        'websites_found': 0,
        'mx_failed': 0,
        'errors': []
    }
    
    # Load CSV
    logger.info(f"Loading CSV from {config.csv_file_path}...")
    try:
        contacts_df = pd.read_csv(
            config.csv_file_path,
            sep=config.csv_separator,
            dtype=str,
            encoding='utf-8'
        )
        stats['total_rows'] = len(contacts_df)
        logger.info(f"Loaded {stats['total_rows']} contacts")
    except Exception as e:
        logger.error(f"Failed to load CSV: {e}")
        stats['errors'].append(f"CSV loading failed: {str(e)}")
        return stats
    
    # Load reference data
    logger.info("Loading reference data from database...")
    try:
        generic_domains = set(contact_repository.get_all_generic_domains())
        generic_users = set(contact_repository.get_all_generic_users())
        generic_mx = set(contact_repository.get_all_mxrecords())
        site_builder_domains = set(contact_repository.get_all_site_builder_domains())
        
        logger.info(f"Loaded {len(generic_domains)} generic domains, "
                   f"{len(generic_users)} generic users, "
                   f"{len(generic_mx)} generic MX records, "
                   f"{len(site_builder_domains)} site builder domains")
    except Exception as e:
        logger.error(f"Failed to load reference data: {e}")
        stats['errors'].append(f"Reference data loading failed: {str(e)}")
        return stats
    
    # Setup nodriver-based web enrichment if enabled
    validator = None
    if config.enable_web_scraping:
        logger.info("Setting up NoDriver browser for web enrichment...")
        try:
            validator = WebsiteEmailValidator(
                skip_website_search=config.skip_google_search
            )
            validator.setup_driver()
            validator.setup_email_filters()
            logger.info("✅ NoDriver browser ready")
        except Exception as e:
            logger.error(f"Failed to setup NoDriver: {e}")
            logger.warning("⚠️ Continuing WITHOUT web scraping")
            validator = None
    
    # Processing state
    contact_batch: List[Tuple] = []
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    new_mx_records: List[Tuple[str, str, str]] = []
    start_time = time.time()
    progress_interval = max(1, min(25, stats['total_rows'] // 20 if stats['total_rows'] else 1))
    
    try:
        # Process all rows
        for _, row in contacts_df.iterrows():
            # Check if job was cancelled (e.g., during server shutdown)
            if job_id and job_store.is_job_cancelled(job_id):
                logger.warning(f"Job {job_id} received shutdown signal, stopping gracefully...")
                break
            
            stats['processed'] += 1
            if (
                stats['processed'] == 1
                or stats['processed'] == stats['total_rows']
                or stats['processed'] % progress_interval == 0
            ):
                percent = (stats['processed'] / stats['total_rows'] * 100) if stats['total_rows'] else 100
                logger.info(
                    f"Progress: {stats['processed']}/{stats['total_rows']} ({percent:.1f}%)"
                )
            
            try:
                # Process single contact
                contact_data = _process_contact_row(
                    row,
                    generic_domains,
                    generic_users,
                    generic_mx,
                    site_builder_domains,
                    config.csv_file_path,
                    config.csv_mapping,
                    config.default_values or {},
                    mx_cache,
                    new_mx_records,
                    validator=validator
                )
                
                if contact_data:
                    contact_batch.append(contact_data)
                    
                    # Track enrichment stats
                    original_email = data_transformers.safe_get(
                        row,
                        config.csv_mapping.get("email")
                    )
                    original_website = data_transformers.safe_get(
                        row,
                        config.csv_mapping.get("url")
                    )
                    
                    if not original_email and contact_data[0] and "@" in contact_data[0]:
                        stats['emails_found'] += 1
                    if not original_website and contact_data[3]:
                        stats['websites_found'] += 1
                else:
                    stats['mx_failed'] += 1
                
                # Batch insert when reaching batch size or end of file
                if len(contact_batch) >= config.batch_size or stats['processed'] == stats['total_rows']:
                    _insert_batch(
                        contact_batch,
                        new_mx_records,
                        stats,
                        start_time,
                        stats['total_rows'],
                        stats['processed']
                    )
                    contact_batch.clear()
                    new_mx_records.clear()
            
            except Exception as e:
                logger.error(f"Error processing row {stats['processed']}: {str(e)}")
                stats['errors'].append(f"Row {stats['processed']}: {str(e)}")
                continue
    
    finally:
        # Cleanup cancellation flag and browser
        if job_id:
            job_store.cleanup_cancel_flag(job_id)
        if validator:
            logger.info("Closing NoDriver browser...")
            try:
                validator.quit()
            except Exception:
                pass
    
    # Summary
    elapsed = time.time() - start_time
    logger.info("=" * 80)
    logger.info("DATABASE SEEDING COMPLETE")
    logger.info(f"Total Rows: {stats['total_rows']}")
    logger.info(f"Processed: {stats['processed']}")
    logger.info(f"Inserted: {stats['inserted']}")
    logger.info(f"Updated: {stats['updated']}")
    logger.info(f"Skipped (No MX): {stats['mx_failed']}")
    logger.info(f"Emails Found (Web): {stats['emails_found']}")
    logger.info(f"Websites Found (Google): {stats['websites_found']}")
    logger.info(f"Time Elapsed: {data_transformers.format_eta(elapsed)}")
    if stats['errors']:
        logger.warning(f"Errors: {len(stats['errors'])}")
    logger.info("=" * 80)
    
    return stats


def _process_contact_row(
    row: Any,
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    file_name: str,
    csv_mapping: Dict[str, str],
    default_values: Dict[str, Any],
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]],
    new_mx_records: List[Tuple[str, str, str]],
    validator: Optional[WebsiteEmailValidator] = None
) -> Optional[Tuple]:
    """
    Process a single contact row: web enrichment, MX resolution, classification, preparation.
    
    Args:
        row: Pandas row
        generic_domains: Set of generic domain names
        generic_users: Set of generic user patterns
        generic_mx: Set of generic MX root domains
        site_builder_domains: Set of site builder domains
        file_name: Source file name
        csv_mapping: Column mapping
        default_values: Default values for null fields
        mx_cache: MX resolution cache
        new_mx_records: List to collect new MX records
        validator: WebsiteEmailValidator (optional)
    
    Returns:
        Contact tuple for insertion or None if invalid
    """
    # Web enrichment
    original_email = str(data_transformers.safe_get(
        row,
        csv_mapping.get("email")
    ) or "").lower().strip()
    original_website = data_transformers.safe_get(
        row,
        csv_mapping.get("url"),
        default_values.get("url")
    ) or ""
    
    enriched_email = original_email
    enriched_website = original_website
    contact_form_url = None
    
    if validator:
        try:
            company = data_transformers.safe_get(
                row,
                csv_mapping.get("company"),
                default_values.get("company")
            ) or ""
            location = data_transformers.safe_get(row, csv_mapping.get("location")) or ""
            
            # Try to find email/website via web scraping
            if enriched_website:
                # Validate existing website
                if validator.validate_website(enriched_website):
                    logger.debug(f"Website valid: {enriched_website}")
                    if not enriched_email:
                        try:
                            found_emails = validator.find_email_on_website(enriched_website)
                            if found_emails:
                                enriched_email = found_emails[0].lower().strip()
                                logger.info(f"Found email via web: {enriched_email}")
                        except Exception as e:
                            logger.warning(f"Web scraping error: {e}")
                    
                    try:
                        contact_form = validator.find_contact_page(enriched_website)
                        if contact_form:
                            contact_form_url = contact_form
                    except Exception as e:
                        logger.debug(f"Contact page search error: {e}")
                else:
                    enriched_website = None
            
            # Google search if no website and company name available
            if not enriched_website and not validator.skip_website_search and company:
                logger.info(f"Searching Google for: {company}")
                try:
                    google_result, is_valid = validator.google_search_business(
                        company,
                        location=location
                    )
                    if google_result:
                        enriched_website = google_result
                        logger.info(f"Found website via Google: {google_result}")
                        
                        if not enriched_email:
                            try:
                                found_emails = validator.find_email_on_website(google_result)
                                if found_emails:
                                    enriched_email = found_emails[0].lower().strip()
                                    logger.info(f"Found email on Google result: {enriched_email}")
                            except Exception as e:
                                logger.warning(f"Web scraping error: {e}")
                except Exception as e:
                    logger.warning(f"Google search error: {e}")
        
        except Exception as e:
            logger.warning(f"Web enrichment error: {e}")
    
    # Extract domain and resolve MX
    email = enriched_email
    url = enriched_website or None
    domain = None
    
    if "@" in email:
        _, domain = email.split("@", 1)
        if not url:
            url = domain
        logger.debug(f"Extracted domain: {domain}")
    else:
        if email:
            logger.warning(f"Invalid email format (no @): {email}")
        return None
    
    # Resolve MX record
    mx_host = None
    mx_root = None
    if domain:
        try:
            mx_host, mx_root = mx_resolver.resolve_mx_record(domain, mx_cache, new_mx_records)
            if not mx_host:
                logger.warning(f"No valid MX record for: {domain}")
                return None
            logger.info(f"MX Host: {mx_host} (Root: {mx_root}) for {domain}")
        except Exception as e:
            logger.warning(f"MX resolution error: {e}")
            return None
    
    # Classify email
    is_generic_email, is_user_generic = email_classifiers.classify_email(
        email,
        generic_domains,
        generic_users,
        generic_mx,
        site_builder_domains,
        mx_root
    )
    
    # Build contact tuple
    fname = data_transformers.format_fname(
        data_transformers.safe_get(row, csv_mapping.get("fname"))
    )
    lname = data_transformers.format_lname(
        data_transformers.safe_get(row, csv_mapping.get("lname"))
    )
    company = data_transformers.safe_get(
        row,
        csv_mapping.get("company"),
        default_values.get("company")
    )
    
    urlcontactform = contact_form_url or data_transformers.safe_get(
        row,
        csv_mapping.get("urlcontactform")
    )
    
    display_name = " ".join(part for part in [fname, lname] if part) or "(no name)"
    display_company = company or "(no company)"
    logger.info(
        f"Contact: {display_name} ({email}) - {display_company} - {url}"
    )
    
    # Return contact tuple (21 fields)
    return (
        email,  # PRIMARY KEY (0)
        fname,  # (1)
        lname,  # (2)
        url,  # (3)
        data_transformers.safe_get(row, csv_mapping.get("position")),  # (4)
        data_transformers.safe_get(row, csv_mapping.get("phone")),  # (5)
        data_transformers.safe_get(row, csv_mapping.get("mobile")),  # (6)
        data_transformers.safe_get(row, csv_mapping.get("fax")),  # (7)
        company,  # (8)
        data_transformers.safe_get(row, csv_mapping.get("address")),  # (9)
        data_transformers.safe_get(row, csv_mapping.get("city")),  # (10)
        data_transformers.safe_get(row, csv_mapping.get("zip")),  # (11)
        data_transformers.safe_get(
            row,
            csv_mapping.get("country"),
            default_values.get("country")
        ),  # (12)
        urlcontactform,  # (13)
        data_transformers.safe_get(row, csv_mapping.get("linkedin")),  # (14)
        data_transformers.safe_get(row, csv_mapping.get("image")),  # (15)
        mx_host,  # (16)
        is_generic_email,  # (17)
        is_user_generic,  # (18)
        "valid" if "@" in email else "invalid",  # (19)
        file_name  # (20)
    )


def _insert_batch(
    contact_batch: List[Tuple],
    new_mx_records: List[Tuple[str, str, str]],
    stats: Dict[str, Any],
    start_time: float,
    total_rows: int,
    processed: int
) -> None:
    """
    Insert a batch of contacts and MX records into the database.
    
    Args:
        contact_batch: List of contact tuples
        new_mx_records: List of new MX records
        stats: Statistics dictionary to update
        start_time: Processing start time
        total_rows: Total rows to process
        processed: Current number of processed rows
    """
    try:
        # Insert MX records first
        if new_mx_records:
            try:
                mx_inserted = contact_repository.batch_create_mxrecords(new_mx_records)
                logger.info(f"Batch inserted {mx_inserted} MX records")
            except Exception as e:
                logger.error(f"Failed to insert MX records: {e}")
        
        # Insert/update contacts
        if contact_batch:
            try:
                inserted, updated = contact_repository.batch_create_contacts(contact_batch)
                stats['inserted'] += inserted
                stats['updated'] += updated
                
                # Calculate ETA
                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = max(total_rows - processed, 0)
                eta_seconds = remaining / rate if rate > 0 else float('inf')
                percent = (processed / total_rows * 100) if total_rows else 100
                
                logger.info(
                    f"Batch: {inserted} inserted, {updated} updated | "
                    f"Progress: {processed}/{total_rows} ({percent:.1f}%) | "
                    f"ETA: {data_transformers.format_eta(eta_seconds)}"
                )
            except Exception as e:
                logger.error(f"Failed to insert contacts batch: {e}")
    
    except Exception as e:
        logger.error(f"Batch insertion error: {e}")