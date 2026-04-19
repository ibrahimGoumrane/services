"""Main CSV processing and database seeding orchestrator."""

import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import pandas as pd

from api.services.utils.job_manager import job_store

from .models import ProcessingConfig
from .utils import contact_repository, data_transformers, email_classifiers, mx_resolver
from .utils.logging_config import flush_buffered_log_handlers, get_logger, setup_logging
from .utils.tld_country_mapper import get_country_from_email_domain
from .utils.website_validator import WebsiteEmailValidator


logger = get_logger(__name__)

SITE_TIMEOUT_SECONDS = 30
PERIODIC_BROWSER_RESTART_BATCHES = 10


def _format_reference_preview(values: set[str], limit: int = 5) -> str:
    preview = sorted((value for value in values if value), key=str.lower)[:limit]
    if not preview:
        return "[]"
    suffix = "" if len(values) <= limit else f" ... (+{len(values) - limit} more)"
    return f"{preview}{suffix}"


def _slugify_for_email(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""

    cleaned = []
    previous_dot = False
    for ch in raw:
        if ch.isalnum():
            cleaned.append(ch)
            previous_dot = False
            continue
        if ch in {" ", ".", "-", "_"}:
            if not previous_dot and cleaned:
                cleaned.append(".")
                previous_dot = True

    token = "".join(cleaned).strip(".")
    return token


def _prefer_named_synthetic_email(
    domain: str,
    fname: str,
    lname: str,
    fullname: str,
) -> Optional[str]:
    if not domain or domain == "nodomaine.com":
        return None

    first_initial = (fname or "").strip().lower()[:1]
    last_token = _slugify_for_email(lname)
    if first_initial and last_token:
        return f"{first_initial}.{last_token}@{domain}"

    fullname_token = _slugify_for_email(fullname)
    if fullname_token:
        return f"{fullname_token}@{domain}"

    return None


def _is_domain_already_processed(domain: str) -> bool:
    if not domain:
        return False
    return contact_repository.get_contact_by_domain(domain) is not None


def _build_company_contact_tuple(
    company_name: str,
    company_url: str,
    sourcefile: Optional[str],
    csv_mapping: Dict[str, str],
    row: Any,
    default_values: Dict[str, Any],
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    found_emails: List[str],
    found_phones: List[str],
    found_contact_page: Optional[str],
    found_city: Optional[str],
    found_country: Optional[str],
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]],
    new_mx_records: List[Tuple[str, str, str]],
) -> Optional[Tuple]:
    domain = _extract_domain_from_website(company_url)
    if not domain:
        return None

    def _mapped_or_default(field: str, fallback: Any = "") -> Any:
        value = data_transformers.get_mapped_value(row, csv_mapping.get(field))
        if value is not None and str(value).strip() != "":
            return value

        default_value = default_values.get(field)
        if default_value is not None and str(default_value).strip() != "":
            return default_value

        return fallback

    selected_email = ""
    for candidate in found_emails:
        candidate_email = (candidate or "").strip().lower()
        if candidate_email and "@" in candidate_email:
            selected_email = candidate_email
            break

    if not selected_email:
        company_local = _slugify_for_email(company_name) or "company"
        selected_email = f"{company_local}@{domain}"

    _, email_domain = selected_email.split("@", 1)
    email_domain = (email_domain or "").strip().lower() or "nodomaine.com"

    is_generic_email, is_user_generic = email_classifiers.classify_email(
        selected_email,
        generic_domains,
        generic_users,
        generic_mx,
        site_builder_domains,
    )

    mx_host = None
    if not is_generic_email:
        try:
            mx_host, _ = mx_resolver.resolve_mx_record(email_domain, mx_cache, new_mx_records)
        except Exception:
            mx_host = None

    company_phone = (found_phones[0] if found_phones else None)

    return (
        selected_email,
        None,
        None,
        None,
        company_url,
        _mapped_or_default("position", None),
        company_phone,
        None,
        None,
        company_name,
        _mapped_or_default("address", None),
        (found_city or "").strip() or None,
        _mapped_or_default("zip", None),
        (found_country or "").strip() or None,
        found_contact_page or None,
        _mapped_or_default("linkedin", None),
        _mapped_or_default("image", None),
        mx_host,
        is_generic_email,
        is_user_generic,
        "valid",
        _mapped_or_default("sourcefile", None) or sourcefile,
        _mapped_or_default("ca", None),
        _mapped_or_default("activite", "")
        or _mapped_or_default("activité", "")
        or _mapped_or_default("secteur", ""),
    )


def process_database_seeding(
    config: ProcessingConfig,
    job_id: str | None = None,
) -> Dict[str, Any]:
    """
    Read CSV, enrich contacts, validate MX, classify emails, and batch write to DB.

    Returns processing statistics.
    """
    global logger
    logger = setup_logging(module_name="dbSeeder", job_id=job_id, buffer_size=config.batch_size)

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
        "website_scraping_attempts": 0,
        "website_scraping_successes": 0,
        "contact_form_discoveries": 0,
        "synthetic_emails_created": 0,
    }

    logger.info(f"Loading CSV from {config.csv_file_path}...")
    try:
        contacts_df = pd.read_csv(
            config.csv_file_path,
            sep=config.csv_separator,
            dtype=str,
            encoding="utf-8",
        )
        stats["total_rows"] = len(contacts_df)
        logger.info(f"Loaded {stats['total_rows']} contacts")
    except Exception as exc:
        logger.error(f"Failed to load CSV: {exc}")
        stats["errors"].append(f"CSV loading failed: {exc}")
        return stats

    logger.info("Loading reference data from database...")
    try:
        generic_domains, generic_users, generic_mx, site_builder_domains, not_visiting_domains = _load_reference_data()
    except Exception as exc:
        logger.error(f"Failed to load reference data: {exc}")
        stats["errors"].append(f"Reference data loading failed: {exc}")
        return stats

    start_row = 1
    if job_id:
        existing_job = job_store.get_job(job_id)
        if existing_job is not None and existing_job.current_row > 1:
            start_row = existing_job.current_row
            stats["processed"] = int(existing_job.result.get("processed", 0)) if existing_job.result else 0
            stats["inserted"] = int(existing_job.result.get("inserted", 0)) if existing_job.result else 0
            stats["updated"] = int(existing_job.result.get("updated", 0)) if existing_job.result else 0
            stats["skipped"] = int(existing_job.result.get("skipped", 0)) if existing_job.result else 0
            logger.info(f"Loaded job progress for {job_id}: resume from row {start_row}")

    validator: Optional[WebsiteEmailValidator] = None
    if config.enable_web_scraping:
        logger.info("Setting up NoDriver browser for web enrichment...")
        try:
            validator = WebsiteEmailValidator(
                skip_website_search=config.skip_google_search,
                site_timeout_seconds=SITE_TIMEOUT_SECONDS,
            )
            validator.setup_driver()
            validator.update_reference_filters(
                generic_domains=generic_domains,
                generic_users=generic_users,
                site_builder_domains=site_builder_domains,
                not_visiting_domains=not_visiting_domains,
            )
            logger.info("NoDriver browser ready")
        except Exception as exc:
            logger.error(f"Failed to setup NoDriver: {exc}")
            logger.warning("Continuing WITHOUT web scraping")
            validator = None

    contact_batch: List[Tuple] = []
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    new_mx_records: List[Tuple[str, str, str]] = []
    batches_processed = 0
    last_boundary_restart_epoch = 0
    if validator:
        last_boundary_restart_epoch = validator.restart_epoch()
    start_time = time.time()

    try:
        for row_number, (_, row) in enumerate(contacts_df.iterrows(), start=1):
            if row_number < start_row:
                continue

            if job_id and job_store.is_job_cancelled(job_id):
                logger.info(f"Job {job_id} received shutdown signal, stopping gracefully...")
                break

            if job_id and job_store.is_job_pause_requested(job_id):
                logger.info(f"Job {job_id} pause requested, stopping at checkpoint row {row_number}")
                job_store.update_progress(
                    job_id,
                    current_row=row_number,
                    total_rows=stats["total_rows"],
                    result=stats,
                )
                job_store.update_status(job_id, "paused", result=stats)
                flush_buffered_log_handlers(logger)
                return stats

            stats["processed"] += 1

            try:
                contact_data, row_stats, extra_contacts, _ = _process_contact_row(
                    row=row,
                    generic_domains=generic_domains,
                    generic_users=generic_users,
                    generic_mx=generic_mx,
                    site_builder_domains=site_builder_domains,
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
                    if row_stats.get("website_scraping_attempt"):
                        stats["website_scraping_attempts"] += 1
                    if row_stats.get("website_scraping_success"):
                        stats["website_scraping_successes"] += 1
                    if row_stats.get("contact_form_found"):
                        stats["contact_form_discoveries"] += 1
                    if row_stats.get("synthetic_email_used"):
                        stats["synthetic_emails_created"] += 1

                if extra_contacts:
                    contact_batch.extend(extra_contacts)

                if contact_data is not None:
                    contact_batch.append(contact_data)

                    original_email = data_transformers.get_mapped_value(row, config.csv_mapping.get("email"))
                    original_website = data_transformers.get_mapped_value(row, config.csv_mapping.get("url"))

                    if not original_email and contact_data[0] and "@" in contact_data[0]:
                        stats["emails_found"] += 1
                    if not original_website and contact_data[4]:
                        stats["websites_found"] += 1
                else:
                    stats["mx_failed"] += 1
                    stats["skipped"] += 1

                    csv_fullname = (data_transformers.get_mapped_value(row, config.csv_mapping.get("fullname")) or "").strip()
                    csv_fname = (data_transformers.get_mapped_value(row, config.csv_mapping.get("fname")) or "").strip()
                    csv_lname = (data_transformers.get_mapped_value(row, config.csv_mapping.get("lname")) or "").strip()
                    csv_company_name = (data_transformers.get_mapped_value(row, config.csv_mapping.get("name")) or "").strip()
                    csv_email = (data_transformers.get_mapped_value(row, config.csv_mapping.get("email")) or "").strip()

                    has_required = bool(csv_fullname or csv_fname or csv_lname or csv_company_name or csv_email)
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
                    if job_id:
                        next_checkpoint_row = min(row_number + 1, stats["total_rows"])
                        job_store.update_progress(
                            job_id,
                            current_row=next_checkpoint_row,
                            total_rows=stats["total_rows"],
                            result=stats,
                        )

                    batches_processed += 1
                    if validator:
                        try:
                            logger.debug(f"Batch reset starting for batch {batches_processed}")
                            validator.prepare_next_batch()
                            logger.debug(f"Batch tab cleanup completed for batch {batches_processed}")
                        except Exception as exc:
                            logger.debug(f"Batch tab cleanup failed: {exc}")

                        periodic_due = batches_processed % PERIODIC_BROWSER_RESTART_BATCHES == 0
                        if periodic_due:
                            if validator.had_health_restart_since(last_boundary_restart_epoch):
                                logger.debug(
                                    "Skipping periodic browser restart on this boundary "
                                    "because a health restart already occurred"
                                )
                            else:
                                try:
                                    validator.restart_browser(reason="periodic")
                                    logger.debug(
                                        f"Periodic browser restart completed at batch {batches_processed}"
                                    )
                                except Exception as exc:
                                    logger.debug(f"Periodic browser restart failed: {exc}")

                        last_boundary_restart_epoch = validator.restart_epoch()

                    try:
                        (
                            generic_domains,
                            generic_users,
                            generic_mx,
                            site_builder_domains,
                            not_visiting_domains,
                        ) = _load_reference_data(log_prefix="Batch reference refresh")

                        logger.debug(
                            "Batch reference refresh values: "
                            f"generic_domains={_format_reference_preview(generic_domains)}, "
                            f"generic_users={_format_reference_preview(generic_users)}, "
                            f"generic_mx={_format_reference_preview(generic_mx)}, "
                            f"site_builder_domains={_format_reference_preview(site_builder_domains)}, "
                            f"not_visiting_domains={_format_reference_preview(not_visiting_domains)}"
                        )

                        if validator:
                            validator.update_reference_filters(
                                generic_domains=generic_domains,
                                generic_users=generic_users,
                                site_builder_domains=site_builder_domains,
                                not_visiting_domains=not_visiting_domains,
                            )
                        logger.debug(
                            f"Batch reference refresh completed for batch {batches_processed}"
                        )
                    except Exception as exc:
                        logger.debug(
                            f"Failed to refresh reference data after batch {batches_processed}: {exc}"
                        )

                    flush_buffered_log_handlers(logger)
                    contact_batch.clear()
                    new_mx_records.clear()

            except Exception as exc:
                logger.warning(f"Error processing row {stats['processed']}: {exc}")
                stats["errors"].append(f"Row {stats['processed']}: {exc}")
                continue

    finally:
        if job_id:
            job_store.cleanup_cancel_flag(job_id)
        if validator:
            logger.info("Closing NoDriver browser...")
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
        logger.info(f"Errors: {len(stats['errors'])}")

    if job_id:
        if job_store.get_job(job_id) and job_store.get_job(job_id).status == "paused":
            job_store.update_progress(
                job_id,
                current_row=stats["processed"] + 1,
                total_rows=stats["total_rows"],
                result=stats,
            )
        elif stats["processed"] >= stats["total_rows"]:
            job_store.update_progress(
                job_id,
                current_row=stats["total_rows"],
                total_rows=stats["total_rows"],
                result=stats,
            )

    flush_buffered_log_handlers(logger)

    return stats


def process_single_url_seeding(
    url: str,
    enable_web_scraping: bool = True,
    skip_google_search: bool = False,
    sourcefile: str | None = None,
    job_id: str | None = None,
) -> Dict[str, Any]:
    """Scrape one URL, save one record, and return a compact scraping result payload."""
    global logger
    logger = setup_logging(module_name="dbSeeder", job_id=job_id, buffer_size=1)

    stats: Dict[str, Any] = {
        "total_rows": 1,
        "processed": 1,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "emails_found": 0,
        "websites_found": 1,
        "mx_failed": 0,
        "rows_skipped_no_required_field": 0,
        "rows_skipped_invalid_mx": 0,
        "rows_skipped_no_email_found": 0,
        "errors": [],
        "google_search_attempts": 0,
        "google_search_successes": 0,
        "website_scraping_attempts": 0,
        "website_scraping_successes": 0,
        "contact_form_discoveries": 0,
        "synthetic_emails_created": 0,
        "url_result": None,
    }

    logger.info(
        "SEED_SINGLE_URL_START "
        f"job_id={job_id or 'none'} "
        f"url='{url}' "
        f"web_scraping={'on' if enable_web_scraping else 'off'} "
        f"google_search={'on' if (enable_web_scraping and not skip_google_search) else 'off'}"
    )

    validator: Optional[WebsiteEmailValidator] = None
    start_time = time.time()

    try:
        generic_domains, generic_users, generic_mx, site_builder_domains, not_visiting_domains = _load_reference_data()

        if enable_web_scraping:
            validator = WebsiteEmailValidator(
                skip_website_search=skip_google_search,
                site_timeout_seconds=SITE_TIMEOUT_SECONDS,
            )
            validator.setup_driver()
            validator.update_reference_filters(
                generic_domains=generic_domains,
                generic_users=generic_users,
                site_builder_domains=site_builder_domains,
                not_visiting_domains=not_visiting_domains,
            )

        row = {
            "url": (url or "").strip(),
        }
        contact_data, row_stats, extra_contacts, _ = _process_contact_row(
            row=row,
            generic_domains=generic_domains,
            generic_users=generic_users,
            generic_mx=generic_mx,
            site_builder_domains=site_builder_domains,
            sourcefile=sourcefile or (url or "single-url"),
            csv_mapping={"url": "url"},
            default_values={},
            mx_cache={},
            new_mx_records=[],
            validator=validator,
        )

        if row_stats.get("google_search_attempt"):
            stats["google_search_attempts"] += 1
        if row_stats.get("google_search_success"):
            stats["google_search_successes"] += 1
        if row_stats.get("website_scraping_attempt"):
            stats["website_scraping_attempts"] += 1
        if row_stats.get("website_scraping_success"):
            stats["website_scraping_successes"] += 1
        if row_stats.get("contact_form_found"):
            stats["contact_form_discoveries"] += 1
        if row_stats.get("synthetic_email_used"):
            stats["synthetic_emails_created"] += 1

        if contact_data is None:
            stats["skipped"] = 1
            stats["mx_failed"] = 1
            stats["rows_skipped_no_email_found"] = 1
            return stats

        single_url_contacts = [*extra_contacts, contact_data]
        inserted, updated = contact_repository.batch_create_contacts(single_url_contacts)
        stats["inserted"] = inserted
        stats["updated"] = updated
        stats["emails_found"] = 1 if (contact_data[0] and "@" in str(contact_data[0])) else 0

        stats["url_result"] = {
            "email": contact_data[0],
            "website": contact_data[4],
            "phone": contact_data[6],
            "city": contact_data[11],
            "country": contact_data[13],
            "contact_form_url": contact_data[14],
            "status": "updated" if updated else "inserted",
        }
        return stats
    except Exception as exc:
        logger.error(f"Single URL processing failed: {exc}")
        stats["errors"].append(str(exc))
        stats["skipped"] = 1
        return stats
    finally:
        if validator:
            try:
                validator.quit()
            except Exception:
                pass

        elapsed = time.time() - start_time
        logger.info(
            "SEED_SINGLE_URL_END "
            f"inserted={stats['inserted']} "
            f"updated={stats['updated']} "
            f"errors={len(stats['errors'])} "
            f"elapsed={data_transformers.format_eta(elapsed)}"
        )
        flush_buffered_log_handlers(logger)


def _process_contact_row(
    row: Any,
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    sourcefile: Optional[str],
    csv_mapping: Dict[str, str],
    default_values: Dict[str, Any],
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]],
    new_mx_records: List[Tuple[str, str, str]],
    validator: Optional[WebsiteEmailValidator] = None,
) -> Tuple[Optional[Tuple], Dict[str, bool], List[Tuple], Set[str]]:
    """
    Process one row and return DB tuple, or None when row should be skipped.
    Also returns row-level statistics for tracking enrichment activities.

    Rules:
    - At least one of fullname/fname/lname/name/email must be present in input row.
    - If email missing, attempt website/google enrichment.
    - Row must end with a valid email and valid MX to be stored.
    - Country is auto-filled from email ccTLD when missing.
    
    Returns:
        Tuple of (contact_data or None, row_stats dict)
    """
    row_stats: Dict[str, bool] = {
        "google_search_attempt": False,
        "google_search_success": False,
        "website_scraping_attempt": False,
        "website_scraping_success": False,
        "contact_form_found": False,
        "synthetic_email_used": False,
        "company_prefetch_attempt": False,
        "company_prefetch_success": False,
        "person_search_attempt": False,
        "person_search_success": False,
        "synthetic_email_rewritten": False,
    }
    extra_contacts: List[Tuple] = []
    row_domains: Set[str] = set()

    def _mapped_or_default(field: str, fallback: Any = "") -> Any:
        value = data_transformers.get_mapped_value(row, csv_mapping.get(field))
        if value is not None and str(value).strip() != "":
            return value

        default_value = default_values.get(field)
        if default_value is not None and str(default_value).strip() != "":
            return default_value

        return fallback

    csv_fullname_raw = str(_mapped_or_default("fullname", "") or "").strip()
    csv_fname = data_transformers.format_fname(
        _mapped_or_default("fname", "")
    )
    csv_lname = data_transformers.format_lname(
        _mapped_or_default("lname", "")
    )
    csv_fullname = csv_fullname_raw or " ".join(
        part for part in [csv_lname, csv_fname] if part
    )

    csv_company_name = str(_mapped_or_default("name", "") or "").strip()

    csv_email = str(_mapped_or_default("email", "") or "").strip().lower()

    row_input_website = str(_mapped_or_default("url", "") or "").strip()
    row_has_website_input = bool(row_input_website)

    if not (csv_fullname or csv_fname or csv_lname or csv_company_name or csv_email or row_input_website):
        logger.info("Skipped: row has none of fullname/fname/lname/name/email/url")
        return None, row_stats, extra_contacts, row_domains

    enriched_email = csv_email
    enriched_website = row_input_website
    contact_form_url = None

    # Start with mapped values, then fill missing values from discovered website when allowed.
    phone = _mapped_or_default("phone", None)
    mobile = _mapped_or_default("mobile", None)
    fax = _mapped_or_default("fax", None)
    geo_location: Optional[str] = None
    geo_city: Optional[str] = None
    geo_country: Optional[str] = None

    if validator:
        try:
            location = (
                _mapped_or_default("location", "")
                or _mapped_or_default("city", "")
                or _mapped_or_default("country", "")
                or _mapped_or_default("position", "")
                or ""
            )
            # Keep full email (not just domain) when using email as Google search seed.
            search_seed = csv_company_name or csv_fullname or csv_email

            if enriched_website and not validator.validate_website(enriched_website):
                logger.info(f"Website rejected by validator: {enriched_website}")
                enriched_website = ""

            can_search = (not row_has_website_input) and (not validator.skip_website_search)

            if can_search and csv_company_name and location:
                row_stats["company_prefetch_attempt"] = True
                logger.info(
                    "Company prefetch search attempt: "
                    f"company='{csv_company_name}', location='{location}'"
                )
                company_result, _ = validator.google_search_business(csv_company_name, location=location)
                if company_result and validator.validate_website(company_result):
                    row_stats["company_prefetch_success"] = True
                    company_domain = _extract_domain_from_website(company_result)
                    if company_domain:
                        row_domains.add(company_domain)
                    if not enriched_website:
                        enriched_website = company_result

                    already_processed_company_domain = bool(
                        company_domain and _is_domain_already_processed(company_domain)
                    )
                    logger.debug(
                        f"Company prefetch domain check: domain='{company_domain or 'n/a'}' "
                        f"already_processed={already_processed_company_domain}"
                    )
                    if not already_processed_company_domain:
                        try:
                            (
                                company_found_emails,
                                company_found_phones,
                                company_contact_page,
                                _,
                                company_city,
                                company_country,
                            ) = validator.find_contact_info_on_website(company_result)

                            company_contact = _build_company_contact_tuple(
                                company_name=csv_company_name,
                                company_url=company_result,
                                sourcefile=sourcefile,
                                csv_mapping=csv_mapping,
                                row=row,
                                default_values=default_values,
                                generic_domains=generic_domains,
                                generic_users=generic_users,
                                generic_mx=generic_mx,
                                site_builder_domains=site_builder_domains,
                                found_emails=company_found_emails,
                                found_phones=company_found_phones,
                                found_contact_page=company_contact_page,
                                found_city=company_city,
                                found_country=company_country,
                                mx_cache=mx_cache,
                                new_mx_records=new_mx_records,
                            )
                            if company_contact is not None:
                                extra_contacts.append(company_contact)
                                logger.info(
                                    "Company prefetch SUCCESS: staged separate company record "
                                    f"for domain '{company_domain}'"
                                )
                        except Exception as exc:
                            logger.warning(f"Company prefetch scraping error: {exc}")

            needs_person_search = can_search and bool(csv_company_name) and bool(csv_fullname or (csv_fname and csv_lname))
            if needs_person_search and (not enriched_email) and (not enriched_website):
                person_seed = f"{csv_fullname or f'{csv_fname} {csv_lname}'.strip()} {csv_company_name}".strip()
                if person_seed:
                    row_stats["person_search_attempt"] = True
                    logger.info(f"Person search attempt: seed='{person_seed}'")
                    person_result, _ = validator.google_search_business(person_seed, location=None)
                    if person_result and validator.validate_website(person_result):
                        enriched_website = person_result
                        row_stats["person_search_success"] = True
                        person_domain = _extract_domain_from_website(person_result)
                        if person_domain:
                            row_domains.add(person_domain)
                        logger.info(f"Person search SUCCESS: found website '{person_result}'")

            # Google search is ONLY for rows where client did not provide website input.
            if (not row_has_website_input) and (not enriched_website) and (not validator.skip_website_search) and search_seed:
                row_stats["google_search_attempt"] = True
                logger.info(f"Google search attempt: seed='{search_seed}', location='{location}'")
                google_result, _ = validator.google_search_business(search_seed, location=location)
                if google_result and validator.validate_website(google_result):
                    enriched_website = google_result
                    row_stats["google_search_success"] = True
                    google_domain = _extract_domain_from_website(google_result)
                    if google_domain:
                        row_domains.add(google_domain)
                    logger.info(f"Google search SUCCESS: found website '{google_result}'")
                else:
                    logger.info("Google search failed: no valid website found")

            if enriched_website:
                logger.info(f"Website enrichment on: '{enriched_website}'")

                website_domain = _extract_domain_from_website(enriched_website)
                if website_domain:
                    row_domains.add(website_domain)
                logger.debug(f"Website reuse lookup domain: '{website_domain or 'n/a'}'")
                existing_contact = contact_repository.get_contact_by_domain(enriched_website)
                if existing_contact:
                    logger.info(
                        "Website domain already exists in DB; reusing stored contact fields before scraping"
                    )
                    if not enriched_email and existing_contact[0]:
                        enriched_email = str(existing_contact[0]).strip().lower()
                    if not phone and existing_contact[6]:
                        phone = existing_contact[6]
                    if not mobile and existing_contact[7]:
                        mobile = existing_contact[7]
                    if not fax and existing_contact[8]:
                        fax = existing_contact[8]
                    if not contact_form_url and existing_contact[14]:
                        contact_form_url = existing_contact[14]
                    if not geo_city and existing_contact[11]:
                        geo_city = str(existing_contact[11]).strip()
                    if not geo_country and existing_contact[13]:
                        geo_country = str(existing_contact[13]).strip()

                needs_site_lookup = (
                    not enriched_email
                    or not phone
                    or not contact_form_url
                    or not _mapped_or_default("city", "")
                    or not _mapped_or_default("country", "")
                )

                if needs_site_lookup:
                    row_stats["website_scraping_attempt"] = True
                    logger.info("Website scraping attempt: looking for email and phone")
                    (
                        found_emails,
                        found_phones,
                        found_contact_page,
                        found_location,
                        found_city,
                        found_country,
                    ) = validator.find_contact_info_on_website(enriched_website)

                    geo_location = found_location or geo_location
                    geo_city = found_city or geo_city
                    geo_country = found_country or geo_country

                    if not contact_form_url and found_contact_page:
                        contact_form_url = found_contact_page
                        row_stats["contact_form_found"] = True
                        logger.info(f"Contact form found: '{found_contact_page}'")

                    email_found = False
                    if not enriched_email:
                        filtered = validator.filter_emails(found_emails or [])
                        if filtered:
                            enriched_email = filtered[0].strip().lower()
                            email_found = True
                            logger.info(f"Website scraping SUCCESS: found email '{enriched_email}'")

                    phone_found = False
                    if not phone:
                        if found_phones:
                            phone = (found_phones[0] or "").strip() or None
                            if phone:
                                phone_found = True
                                logger.info(f"Website scraping SUCCESS: found phone '{phone}'")

                    if email_found or phone_found:
                        row_stats["website_scraping_success"] = True
                    else:
                        logger.info("Website scraping failed: no valid email or phone found on website")

        except Exception as exc:
            logger.warning(f"Web enrichment error: {exc}")

    if not enriched_email or "@" not in enriched_email:
        fallback_domain = _extract_domain_from_website(enriched_website)
        if not fallback_domain:
            fallback_domain = "nodomaine.com"
        should_rewrite = bool(
            fallback_domain
            and _is_domain_already_processed(fallback_domain)
            and (csv_fullname or (csv_fname and csv_lname))
        )
        rewritten = None
        if should_rewrite:
            rewritten = _prefer_named_synthetic_email(
                domain=fallback_domain,
                fname=csv_fname,
                lname=csv_lname,
                fullname=csv_fullname,
            )

        if rewritten:
            enriched_email = rewritten
            row_stats["synthetic_email_rewritten"] = True
            logger.info(f"Synthetic fallback email rewritten from postmaster pattern to '{enriched_email}'")
        else:
            synthetic_user_id = uuid.uuid4().hex
            enriched_email = f"postmaster+{synthetic_user_id}@{fallback_domain}"
        row_stats["synthetic_email_used"] = True
        logger.info(
            "Synthetic fallback email generated: "
            f"'{enriched_email}' (website_domain={'yes' if fallback_domain != 'nodomaine.com' else 'no'})"
        )

    if "@" in enriched_email:
        local_part, current_domain = enriched_email.split("@", 1)
        local_part = (local_part or "").strip().lower()
        current_domain = (current_domain or "").strip().lower()
        if local_part.startswith("postmaster+") and _is_domain_already_processed(current_domain):
            rewritten_existing = _prefer_named_synthetic_email(
                domain=current_domain,
                fname=csv_fname,
                lname=csv_lname,
                fullname=csv_fullname,
            )
            if rewritten_existing:
                enriched_email = rewritten_existing
                row_stats["synthetic_email_rewritten"] = True
                logger.info(
                    "Synthetic postmaster email rewritten using name fields: "
                    f"'{enriched_email}'"
                )

    _, domain = enriched_email.split("@", 1)
    domain = domain.strip().lower()
    if not domain:
        logger.info("Email domain was empty; normalizing to nodomaine.com to preserve record")
        local_part = enriched_email.split("@", 1)[0].strip() or f"postmaster+{uuid.uuid4().hex}"
        domain = "nodomaine.com"
        enriched_email = f"{local_part}@{domain}"
        row_stats["synthetic_email_used"] = True

    is_generic_email, is_user_generic = email_classifiers.classify_email(
        enriched_email,
        generic_domains,
        generic_users,
        generic_mx,
        site_builder_domains,
    )

    mx_host = None
    mx_root = None

    if not is_generic_email and not row_stats["synthetic_email_used"]:
        try:
            mx_host, mx_root = mx_resolver.resolve_mx_record(domain, mx_cache, new_mx_records)
            if not mx_host:
                logger.info(
                    f"No valid MX record for domain '{domain}'; preserving row with discovered email"
                )
        except Exception as exc:
            logger.warning(
                f"MX resolution error for {domain}: {exc}; preserving row with discovered email"
            )

        if mx_root:
            mx_root_email = f"mx@{mx_root}"
            mx_is_generic, _ = email_classifiers.classify_email(
                mx_root_email,
                generic_domains=set(),
                generic_users=set(),
                generic_mx=generic_mx,
                site_builder_domains=site_builder_domains,
            )
            is_generic_email = bool(is_generic_email or mx_is_generic)

    fullname = csv_fullname or None
    fname = csv_fname
    lname = csv_lname
    company_name = csv_company_name or None
    ca = _mapped_or_default("ca", None)
    activite = (
        _mapped_or_default("activite", "")
        or _mapped_or_default("activité", "")
        or _mapped_or_default("secteur", "")
    )

    city = str(_mapped_or_default("city", "") or "")
    if not city:
        city = (geo_city or geo_location or "").strip()

    country = str(_mapped_or_default("country", "") or "")
    if not country:
        country = (geo_country or "").strip()

    if not country:
        try:
            from_tld = get_country_from_email_domain(enriched_email)
            if from_tld:
                country = from_tld
        except Exception as exc:
            logger.debug(f"Country enrichment failed for {enriched_email}: {exc}")

    urlcontactform = contact_form_url or _mapped_or_default("urlcontactform", None)
    row_sourcefile = _mapped_or_default("sourcefile", None) or sourcefile

    logger.info(f"Email classification: is_generic={is_generic_email}, is_user_generic={is_user_generic}")

    contact_tuple = (
        enriched_email,
        fullname,
        fname,
        lname,
        enriched_website or None,
        _mapped_or_default("position", None),
        phone,
        mobile,
        fax,
        company_name,
        _mapped_or_default("address", None),
        city or None,
        _mapped_or_default("zip", None),
        country,
        urlcontactform,
        _mapped_or_default("linkedin", None),
        _mapped_or_default("image", None),
        mx_host,
        is_generic_email,
        is_user_generic,
        "valid",
        row_sourcefile,
        ca,
        activite,
    )

    final_domain = _extract_domain_from_website(enriched_website)
    if final_domain:
        row_domains.add(final_domain)

    return contact_tuple, row_stats, extra_contacts, row_domains


def _extract_domain_from_website(website: Optional[str]) -> str:
    """Extract normalized host domain from a website string."""
    raw_website = (website or "").strip().lower()
    if not raw_website:
        return ""

    candidate = raw_website if "://" in raw_website else f"https://{raw_website}"
    parsed = urlparse(candidate)

    host = (parsed.netloc or parsed.path or "").strip().lower()
    if not host:
        return ""

    host = host.split("/", 1)[0]
    host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]

    return host


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
                logger.info(f"Batch inserted {mx_inserted} MX records")
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

                logger.info(
                    f"Batch: {inserted} inserted, {updated} updated | "
                    f"Progress: {processed} / {total_rows} | "
                    f"Email status: Inserted={inserted}, Updated={updated} | "
                    f"Totals: Inserted={stats['inserted']}, Updated={stats['updated']} | "
                    f"ETA: {data_transformers.format_eta(eta_seconds)}"
                )
            except Exception as exc:
                logger.error(f"Failed to insert contacts batch: {exc}")

    except Exception as exc:
        logger.error(f"Batch insertion error: {exc}")


def _load_reference_data(log_prefix: str = "Loaded") -> tuple[
    set[str],
    set[str],
    set[str],
    set[str],
    set[str],
]:
    """Fetch all classifier reference sets from DB in one call site."""
    generic_domains = set(contact_repository.get_all_generic_domains())
    generic_users = set(contact_repository.get_all_generic_users())
    generic_mx = set(contact_repository.get_all_mxrecords())
    site_builder_domains = set(contact_repository.get_all_site_builder_domains())
    not_visiting_domains = set(contact_repository.get_all_not_visiting_domains())

    logger.debug(
        f"{log_prefix}: "
        f"{len(generic_domains)} generic domains, "
        f"{len(generic_users)} generic users, "
        f"{len(generic_mx)} generic MX records, "
        f"{len(site_builder_domains)} site builder domains, "
        f"{len(not_visiting_domains)} not-visiting domains"
    )
    logger.debug(
        f"{log_prefix} values: "
        f"generic_domains={_format_reference_preview(generic_domains)}, "
        f"generic_users={_format_reference_preview(generic_users)}, "
        f"generic_mx={_format_reference_preview(generic_mx)}, "
        f"site_builder_domains={_format_reference_preview(site_builder_domains)}, "
        f"not_visiting_domains={_format_reference_preview(not_visiting_domains)}"
    )

    return (
        generic_domains,
        generic_users,
        generic_mx,
        site_builder_domains,
        not_visiting_domains,
    )
