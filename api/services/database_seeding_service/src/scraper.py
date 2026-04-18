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
                contact_data, row_stats = _process_contact_row(
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
) -> Tuple[Optional[Tuple], Dict[str, bool]]:
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
    }

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
        return None, row_stats

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

            # Google search is ONLY for rows where client did not provide website input.
            if (not row_has_website_input) and (not enriched_website) and (not validator.skip_website_search) and search_seed:
                row_stats["google_search_attempt"] = True
                logger.info(f"Google search attempt: seed='{search_seed}', location='{location}'")
                google_result, _ = validator.google_search_business(search_seed, location=location)
                if google_result and validator.validate_website(google_result):
                    enriched_website = google_result
                    row_stats["google_search_success"] = True
                    logger.info(f"Google search SUCCESS: found website '{google_result}'")
                else:
                    logger.info("Google search failed: no valid website found")

            if enriched_website:
                logger.info(f"Website enrichment on: '{enriched_website}'")

                existing_contact = contact_repository.get_contact_by_url(enriched_website)
                if existing_contact:
                    logger.info(
                        "Website already exists in DB; reusing stored contact fields before scraping"
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

        synthetic_user_id = uuid.uuid4().hex
        enriched_email = f"postmaster+{synthetic_user_id}@{fallback_domain}"
        row_stats["synthetic_email_used"] = True
        logger.info(
            "Synthetic fallback email generated: "
            f"'{enriched_email}' (website_domain={'yes' if fallback_domain != 'nodomaine.com' else 'no'})"
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
    
    return contact_tuple, row_stats


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

    return (
        generic_domains,
        generic_users,
        generic_mx,
        site_builder_domains,
        not_visiting_domains,
    )
