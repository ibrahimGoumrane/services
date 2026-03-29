"""Main CSV processing and database seeding orchestrator."""

import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import pandas as pd

from api.services.utils.job_manager import job_store

from .models import ProcessingConfig
from .utils import contact_repository, data_transformers, email_classifiers, mx_resolver
from .utils.logging_config import get_logger, setup_logging
from .utils.tld_country_mapper import get_country_from_email_domain
from .utils.website_validator import WebsiteEmailValidator


logger = get_logger(__name__)

GENERIC_EMAIL_PROVIDER_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.fr",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
}


def process_database_seeding(
    config: ProcessingConfig,
    job_id: str | None = None,
) -> Dict[str, Any]:
    """
    Read CSV, enrich contacts, validate MX, classify emails, and batch write to DB.

    Returns processing statistics.
    """
    global logger
    logger = setup_logging(module_name="dbSeeder", job_id=job_id)

    logger.info(
        "SEED_START "
        f"job_id={job_id or 'none'} "
        f"csv='{config.csv_file_path}' "
        f"separator='{config.csv_separator}' "
        f"batch_size={config.batch_size} "
        f"web_scraping={'on' if config.enable_web_scraping else 'off'} "
        f"google_search={'on' if (config.enable_web_scraping and not config.skip_google_search) else 'off'}"
    )

    stats: Dict[str, Any] = {
        "total_rows": 0,
        "processed": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "emails_found": 0,
        "websites_found": 0,
        "mx_failed": 0,
        "rows_skipped_no_required_field": 0,
        "rows_skipped_invalid_mx": 0,
        "rows_skipped_no_email_found": 0,
        "errors": [],
        "google_search_attempts": 0,
        "google_search_successes": 0,
        "domain_derivation_attempts": 0,
        "domain_derivation_successes": 0,
        "website_scraping_attempts": 0,
        "website_scraping_successes": 0,
        "contact_form_discoveries": 0,
    }

    logger.debug(f"Loading CSV from {config.csv_file_path}...")
    try:
        contacts_df = pd.read_csv(
            config.csv_file_path,
            sep=config.csv_separator,
            dtype=str,
            encoding="utf-8",
        )
        stats["total_rows"] = len(contacts_df)
        logger.debug(f"Loaded {stats['total_rows']} contacts")
    except Exception as exc:
        logger.error(f"Failed to load CSV: {exc}")
        stats["errors"].append(f"CSV loading failed: {exc}")
        return stats

    logger.debug("Loading reference data from database...")
    try:
        generic_domains = set(contact_repository.get_all_generic_domains())
        generic_users = set(contact_repository.get_all_generic_users())
        generic_mx = set(contact_repository.get_all_mxrecords())
        site_builder_domains = set(contact_repository.get_all_site_builder_domains())
        not_visiting_domains = set(contact_repository.get_all_not_visiting_domains())

        logger.debug(
            f"Loaded {len(generic_domains)} generic domains, "
            f"{len(generic_users)} generic users, "
            f"{len(generic_mx)} generic MX records, "
            f"{len(site_builder_domains)} site builder domains, "
            f"{len(not_visiting_domains)} not-visiting domains"
        )
    except Exception as exc:
        logger.error(f"Failed to load reference data: {exc}")
        stats["errors"].append(f"Reference data loading failed: {exc}")
        return stats

    validator: Optional[WebsiteEmailValidator] = None
    if config.enable_web_scraping:
        logger.debug("Setting up NoDriver browser for web enrichment...")
        try:
            validator = WebsiteEmailValidator(skip_website_search=config.skip_google_search)
            validator.setup_driver()
            validator.setup_email_filters()
            logger.debug("NoDriver browser ready")
        except Exception as exc:
            logger.error(f"Failed to setup NoDriver: {exc}")
            logger.debug("Continuing WITHOUT web scraping")
            validator = None

    contact_batch: List[Tuple] = []
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    new_mx_records: List[Tuple[str, str, str]] = []
    start_time = time.time()

    try:
        for _, row in contacts_df.iterrows():
            if job_id and job_store.is_job_cancelled(job_id):
                logger.debug(f"Job {job_id} received shutdown signal, stopping gracefully...")
                break

            stats["processed"] += 1

            try:
                contact_data, row_stats = _process_contact_row(
                    row=row,
                    generic_domains=generic_domains,
                    generic_users=generic_users,
                    generic_mx=generic_mx,
                    site_builder_domains=site_builder_domains,
                    not_visiting_domains=not_visiting_domains,
                    sourcefile=config.sourcefile or config.csv_file_path,
                    csv_mapping=config.csv_mapping,
                    default_values=config.default_values or {},
                    mx_cache=mx_cache,
                    new_mx_records=new_mx_records,
                    validator=validator,
                )
                
                # Update stats with row-level stats
                if row_stats:
                    if row_stats.get("google_search_attempt"):
                        stats["google_search_attempts"] += 1
                    if row_stats.get("google_search_success"):
                        stats["google_search_successes"] += 1
                    if row_stats.get("domain_derivation_attempt"):
                        stats["domain_derivation_attempts"] += 1
                    if row_stats.get("domain_derivation_success"):
                        stats["domain_derivation_successes"] += 1
                    if row_stats.get("website_scraping_attempt"):
                        stats["website_scraping_attempts"] += 1
                    if row_stats.get("website_scraping_success"):
                        stats["website_scraping_successes"] += 1
                    if row_stats.get("contact_form_found"):
                        stats["contact_form_discoveries"] += 1

                if contact_data is not None:
                    contact_batch.append(contact_data)

                    original_email = data_transformers.get_mapped_value(row, config.csv_mapping.get("email"))
                    original_website = data_transformers.get_mapped_value(row, config.csv_mapping.get("url"))

                    if not original_email and contact_data[0] and "@" in contact_data[0]:
                        stats["emails_found"] += 1
                    if not original_website and contact_data[3]:
                        stats["websites_found"] += 1
                else:
                    stats["mx_failed"] += 1
                    stats["skipped"] += 1

                    csv_name = (data_transformers.get_mapped_value(row, config.csv_mapping.get("name")) or "").strip()
                    csv_company = (data_transformers.get_mapped_value(row, config.csv_mapping.get("company")) or "").strip()
                    csv_email = (data_transformers.get_mapped_value(row, config.csv_mapping.get("email")) or "").strip()

                    has_required = bool(csv_name or csv_company or csv_email)
                    if not has_required:
                        stats["rows_skipped_no_required_field"] += 1
                    elif csv_email:
                        stats["rows_skipped_invalid_mx"] += 1
                    else:
                        stats["rows_skipped_no_email_found"] += 1

                if len(contact_batch) >= config.batch_size or stats["processed"] == stats["total_rows"]:
                    _insert_batch(
                        contact_batch=contact_batch,
                        new_mx_records=new_mx_records,
                        stats=stats,
                        start_time=start_time,
                        total_rows=stats["total_rows"],
                        processed=stats["processed"],
                    )
                    contact_batch.clear()
                    new_mx_records.clear()

            except Exception as exc:
                logger.debug(f"Error processing row {stats['processed']}: {exc}")
                stats["errors"].append(f"Row {stats['processed']}: {exc}")
                continue

    finally:
        if job_id:
            job_store.cleanup_cancel_flag(job_id)
        if validator:
            logger.debug("Closing NoDriver browser...")
            try:
                validator.quit()
            except Exception:
                pass

    elapsed = time.time() - start_time
    logger.info(
        "SEED_END "
        f"processed={stats['processed']} "
        f"total={stats['total_rows']} "
        f"inserted={stats['inserted']} "
        f"updated={stats['updated']} "
        f"skipped={stats['skipped']} "
        f"errors={len(stats['errors'])} "
        f"elapsed={data_transformers.format_eta(elapsed)}"
    )
    if stats["errors"]:
        logger.debug(f"Errors: {len(stats['errors'])}")

    return stats


def _is_not_visiting_domain(url: str, not_visiting_domains: Set[str]) -> bool:
    """
    Check if a URL's domain matches any entry in the not-visiting domains set.

    Comparison is done on the netloc stripped of ``www.`` prefix so that both
    ``www.example.com`` and ``example.com`` are caught.

    Args:
        url: Full URL (e.g. ``https://www.example.com/page``).
        not_visiting_domains: Pre-fetched set of blocked domain strings.

    Returns:
        ``True`` when the domain should be skipped.
    """
    if not url or not not_visiting_domains:
        return False
    try:
        parsed = urlparse(url)
        netloc = (parsed.netloc or "").lower().replace("www.", "")
        if not netloc:
            return False
        for blocked in not_visiting_domains:
            blocked_clean = blocked.lower().replace("www.", "").strip()
            if netloc == blocked_clean or netloc.endswith("." + blocked_clean):
                return True
        return False
    except Exception:
        return False


def _process_contact_row(
    row: Any,
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    not_visiting_domains: Set[str],
    sourcefile: Optional[str],
    csv_mapping: Dict[str, str],
    default_values: Dict[str, Any],
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]],
    new_mx_records: List[Tuple[str, str, str]],
    validator: Optional[WebsiteEmailValidator] = None,
) -> Tuple[Optional[Tuple], Dict[str, bool]]:
    """
    Process one row and return DB tuple, or None when row should be skipped.
    Also returns row-level statistics for tracking enrichment activities.

    Rules:
    - At least one of name/company/email must be present in input row.
    - If email missing, attempt website/google enrichment.
    - Row must end with a valid email and valid MX to be stored.
    - Country is auto-filled from email ccTLD when missing.
    
    Returns:
        Tuple of (contact_data or None, row_stats dict)
    """
    row_stats: Dict[str, bool] = {
        "google_search_attempt": False,
        "google_search_success": False,
        "domain_derivation_attempt": False,
        "domain_derivation_success": False,
        "website_scraping_attempt": False,
        "website_scraping_success": False,
        "contact_form_found": False,
    }
    
    csv_name = (data_transformers.get_mapped_value(row, csv_mapping.get("name")) or "").strip()
    csv_company = (data_transformers.get_mapped_value(row, csv_mapping.get("company")) or "").strip()
    csv_email = (data_transformers.get_mapped_value(row, csv_mapping.get("email")) or "").strip().lower()
    csv_email_domain = csv_email.split("@", 1)[1].strip().lower() if "@" in csv_email else ""

    if not (csv_name or csv_company or csv_email):
        logger.debug("Skipped: row has none of name/company/email")
        return None, row_stats

    row_input_website = (
        data_transformers.get_mapped_value(row, csv_mapping.get("url")) or ""
    ).strip()
    row_has_website_input = bool(row_input_website)

    enriched_email = csv_email
    enriched_website = row_input_website
    contact_form_url = None

    # Start with mapped values, then fill missing values from discovered website when allowed.
    phone = data_transformers.get_mapped_value(row, csv_mapping.get("phone"))
    mobile = data_transformers.get_mapped_value(row, csv_mapping.get("mobile"))
    fax = data_transformers.get_mapped_value(row, csv_mapping.get("fax"))

    if validator:
        try:
            location = (
                data_transformers.get_mapped_value(row, csv_mapping.get("city"))
                or data_transformers.get_mapped_value(row, csv_mapping.get("location"))
                or ""
            )
            # Keep full email (not just domain) when using email as Google search seed.
            search_seed = csv_company or csv_name or csv_email

            # Check CSV-provided website against not-visiting domains first
            if enriched_website and _is_not_visiting_domain(enriched_website, not_visiting_domains):
                logger.debug(f"   → Skipping not-visiting domain (CSV input): {enriched_website}")
                enriched_website = ""

            if enriched_website and not validator.validate_website(enriched_website):
                logger.debug(f"Provided website not valid/reachable: {enriched_website}")
                enriched_website = ""

            # Google search is ONLY for rows where client did not provide website input.
            if (not row_has_website_input) and (not enriched_website) and (not validator.skip_website_search) and search_seed:
                row_stats["google_search_attempt"] = True
                logger.debug(f"   → Attempting Google search with seed='{search_seed}', location='{location}'")
                google_result, _ = validator.google_search_business(search_seed, location=location)
                if google_result and _is_not_visiting_domain(google_result, not_visiting_domains):
                    logger.debug(f"   → Google result skipped (not-visiting domain): {google_result}")
                elif google_result and validator.validate_website(google_result):
                    enriched_website = google_result
                    row_stats["google_search_success"] = True
                    logger.debug(f"   → Google search SUCCESS: found website '{google_result}'")
                else:
                    logger.debug("   → Google search failed: no valid website found")

            # If Google did not help, try deriving URL from email domain and validate it.
            if not enriched_website and csv_email_domain and csv_email_domain not in GENERIC_EMAIL_PROVIDER_DOMAINS:
                row_stats["domain_derivation_attempt"] = True
                candidate_url = f"https://{csv_email_domain}"
                if _is_not_visiting_domain(candidate_url, not_visiting_domains):
                    logger.debug(f"   → Domain derivation skipped (not-visiting domain): {candidate_url}")
                else:
                    logger.debug(f"   → Attempting domain derivation: trying '{candidate_url}'")
                    if validator.validate_website(candidate_url):
                        enriched_website = candidate_url
                        row_stats["domain_derivation_success"] = True
                        logger.debug(f"   → Domain derivation SUCCESS: website '{candidate_url}' is valid")
                    else:
                        logger.debug(f"   → Domain derivation failed: website '{candidate_url}' is not valid")

            # Shared enrichment phase:
            # if web scraping is enabled and we have a website (provided or discovered),
            # use it to enrich missing fields.
            if enriched_website:
                logger.debug(f"   → Running website enrichment on: '{enriched_website}'")

                if not enriched_email:
                    row_stats["website_scraping_attempt"] = True
                    logger.debug("   → Looking for email on website...")
                    found_emails = validator.find_email_on_website(enriched_website) or []
                    filtered = validator.filter_emails(found_emails)
                    if filtered:
                        enriched_email = filtered[0].strip().lower()
                        row_stats["website_scraping_success"] = True
                        logger.debug(f"   → Website scraping SUCCESS: found email '{enriched_email}'")
                    else:
                        logger.debug("   → Website scraping failed: no valid email found on website")

                if not contact_form_url:
                    try:
                        contact_form = validator.find_contact_page(enriched_website)
                        if contact_form:
                            contact_form_url = contact_form
                            row_stats["contact_form_found"] = True
                            logger.debug(f"   → Contact form found: '{contact_form}'")
                    except Exception:
                        pass

        except Exception as exc:
            logger.debug(f"Web enrichment error: {exc}")

    if not enriched_email or "@" not in enriched_email:
        logger.debug("   → Skipped: no valid email after enrichment")
        return None, row_stats

    _, domain = enriched_email.split("@", 1)
    domain = domain.strip().lower()
    if not domain:
        logger.debug("   → Skipped: empty email domain")
        return None, row_stats

    try:
        mx_host, mx_root = mx_resolver.resolve_mx_record(domain, mx_cache, new_mx_records)
        if not mx_host:
            logger.debug(f"   → Skipped: no valid MX record for domain '{domain}'")
            return None, row_stats
    except Exception as exc:
        logger.debug(f"MX resolution error for {domain}: {exc}")
        return None, row_stats

    is_generic_email, is_user_generic = email_classifiers.classify_email(
        enriched_email,
        generic_domains,
        generic_users,
        generic_mx,
        site_builder_domains,
        mx_root,
    )

    fname = data_transformers.format_fname(data_transformers.get_mapped_value(row, csv_mapping.get("fname")))
    lname = data_transformers.format_lname(data_transformers.get_mapped_value(row, csv_mapping.get("lname")))
    company = data_transformers.get_mapped_value(row, csv_mapping.get("company"))

    country = data_transformers.get_mapped_value(row, csv_mapping.get("country")) or ""
    if not country:
        try:
            from_tld = get_country_from_email_domain(enriched_email)
            if from_tld:
                country = from_tld
        except Exception as exc:
            logger.debug(f"Country enrichment failed for {enriched_email}: {exc}")

    urlcontactform = contact_form_url or data_transformers.get_mapped_value(row, csv_mapping.get("urlcontactform"))
    row_sourcefile = data_transformers.get_mapped_value(row, csv_mapping.get("sourcefile")) or sourcefile

    logger.debug(f"   → Email classification: is_generic={is_generic_email}, is_user_generic={is_user_generic}")

    contact_tuple = (
        enriched_email,
        fname,
        lname,
        enriched_website or None,
        data_transformers.get_mapped_value(row, csv_mapping.get("position")),
        phone,
        mobile,
        fax,
        company,
        data_transformers.get_mapped_value(row, csv_mapping.get("address")),
        data_transformers.get_mapped_value(row, csv_mapping.get("city")),
        data_transformers.get_mapped_value(row, csv_mapping.get("zip")),
        country,
        urlcontactform,
        data_transformers.get_mapped_value(row, csv_mapping.get("linkedin")),
        data_transformers.get_mapped_value(row, csv_mapping.get("image")),
        mx_host,
        is_generic_email,
        is_user_generic,
        "valid",
        row_sourcefile,
    )
    
    return contact_tuple, row_stats


def _insert_batch(
    contact_batch: List[Tuple],
    new_mx_records: List[Tuple[str, str, str]],
    stats: Dict[str, Any],
    start_time: float,
    total_rows: int,
    processed: int,
) -> None:
    """Insert a batch of contacts and MX records into the database."""
    try:
        if new_mx_records:
            try:
                mx_inserted = contact_repository.batch_create_mxrecords(new_mx_records)
                logger.debug(f"Batch inserted {mx_inserted} MX records")
            except Exception as exc:
                logger.error(f"Failed to insert MX records: {exc}")

        if contact_batch:
            try:
                inserted, updated = contact_repository.batch_create_contacts(contact_batch)
                stats["inserted"] += inserted
                stats["updated"] += updated

                elapsed = time.time() - start_time
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = max(total_rows - processed, 0)
                eta_seconds = remaining / rate if rate > 0 else float("inf")
                percent = (processed / total_rows * 100) if total_rows else 100

                logger.info(
                    f"Batch: {inserted} inserted, {updated} updated | "
                    f"Progress: {processed}/{total_rows} ({percent:.1f}%) | "
                    f"ETA: {data_transformers.format_eta(eta_seconds)}"
                )
            except Exception as exc:
                logger.error(f"Failed to insert contacts batch: {exc}")

    except Exception as exc:
        logger.error(f"Batch insertion error: {exc}")
