"""Main CSV processing and database seeding orchestrator."""

import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple , Literal
from urllib.parse import urlparse
from .models import ProcessingConfig, CsvRow , RowStats , PersonContactData , CompanyContactData , ScrapedWebData

import pandas as pd

from api.services.utils.job_manager import job_store

from api.models import CsvMapping
from .models import ProcessingConfig
from .utils import contact_repository, data_transformers, email_classifiers, mx_resolver
from api.services.utils.logging_config import flush_buffered_log_handlers, get_logger, setup_logging
from .utils.tld_country_mapper import get_country_from_email_domain
from .utils.url_utils import extract_domain
from .main.web_validator import WebsiteEmailValidator

logger = get_logger(__name__)

SITE_TIMEOUT_SECONDS = 30
PERIODIC_BROWSER_RESTART_BATCHES = 100




# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _format_reference_preview(values: set[str], limit: int = 5) -> str:
    preview = sorted((v for v in values if v), key=str.lower)[:limit]
    if not preview:
        return "[]"
    suffix = "" if len(values) <= limit else f" ... (+{len(values) - limit} more)"
    return f"{preview}{suffix}"


def _slugify_for_email(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    cleaned: list[str] = []
    previous_dot = False
    for ch in raw:
        if ch.isalnum():
            cleaned.append(ch)
            previous_dot = False
        elif ch in {" ", ".", "-", "_"}:
            if not previous_dot and cleaned:
                cleaned.append(".")
                previous_dot = True
    return "".join(cleaned).strip(".")


def _prefer_named_synthetic_email(domain: str, fname: str, lname: str, fullname: str) -> Optional[str]:
    if not domain:
        domain = "nodomaine.com"
    first_initial = (fname or "").strip().lower()[:1]
    last_token = _slugify_for_email(lname)
    if first_initial and last_token:
        return f"{first_initial}.{last_token}@{domain}"
    fullname_token = _slugify_for_email(fullname)
    if fullname_token:
        return f"{fullname_token}@{domain}"
    return None


def _is_linkedin_url(url: Optional[str]) -> bool:
    if not url:
        return False
    raw = str(url).strip()
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        parsed = urlparse(candidate)
    except Exception:
        return False
    host = (parsed.netloc or "").lower().strip().removeprefix("www.")
    if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
        return False
    path = (parsed.path or "").lower().lstrip("/")
    return path.startswith("in/") or path.startswith("company/")


def _merge_row_stats(stats: Dict[str, Any], row_stats: RowStats) -> None:
    if row_stats.contact_form_found:
        stats["contact_form_discoveries"] += 1
    if row_stats.synthetic_email_used:
        stats["synthetic_emails_created"] += 1


class JobInterruptionRequested(Exception):
    """Raised when a pause/stop signal is detected during row processing."""





# ---------------------------------------------------------------------------
# CSV row extraction
# ---------------------------------------------------------------------------

def _extract_csv_row(
    row: Any,
    csv_mapping: CsvMapping,
    default_values: Dict[str, Any],
    sourcefile: Optional[str],
) -> CsvRow:
    """
    Extract and normalise every mapped field from the raw CSV row.
    Default values are applied here as fallbacks; they are NOT overridden later.
    """
    def _get(field: str, fallback: Any = "") -> Any:
        value = data_transformers.get_mapped_value(row, csv_mapping.get(field))
        if value is not None and str(value).strip() != "":
            return value
        default_value = default_values.get(field)
        if default_value is not None and str(default_value).strip() != "":
            return default_value
        return fallback

    fname = data_transformers.format_fname(_get("fname", ""))
    lname = data_transformers.format_lname(_get("lname", ""))
    fullname_raw = str(_get("fullname", "") or "").strip()
    fullname = fullname_raw or " ".join(part for part in [fname, lname] if part)

    activite = (
        str(_get("activite", "") or "")
        or str(_get("secteur", "") or "")
    )

    location = str(
        _get("location", "")
        or _get("city", "")
        or _get("country", "")
        or _get("position", "")
        or ""
    )

    return CsvRow(
        fullname=fullname,
        fname=fname,
        lname=lname,
        name=str(_get("name", "") or "").strip(),
        email=str(_get("email", "") or "").strip().lower(),
        website=str(_get("url", "") or "").strip(),
        phone=_get("phone", None),
        mobile=_get("mobile", None),
        fax=_get("fax", None),
        linkedin=str(_get("linkedin", "") or "").strip() or None,
        position=_get("position", None),
        address=_get("address", None),
        city=str(_get("city", "") or ""),
        zip_code=_get("zip", None),
        country=str(_get("country", "") or ""),
        location=location,
        contact_form_url=_get("urlcontactform", None),
        image=_get("image", None),
        sourcefile=str(_get("sourcefile", "") or "").strip() or sourcefile,
        ca=_get("ca", None),
        activite=activite,
    )


# ---------------------------------------------------------------------------
# Domain dedup helper
# ---------------------------------------------------------------------------

def _populate_from_db(
    db_row: Tuple,
    csv: CsvRow,
) -> None:
    """
    Back-fill empty fields on *csv* from an existing DB record.
    Never overwrites values that already came from the CSV.
    """
    if db_row[0] and not csv.email:
        db_email = str(db_row[0]).strip().lower()
        # Avoid clobbering a real person row with a postmaster fallback.
        if not (db_email.startswith("postmaster") and (csv.fname or csv.lname or csv.fullname or csv.name)):
            csv.email = db_email
    if db_row[6] and not csv.phone:
        csv.phone = db_row[6]
    if db_row[7] and not csv.mobile:
        csv.mobile = db_row[7]
    if db_row[8] and not csv.fax:
        csv.fax = db_row[8]
    if db_row[14] and not csv.contact_form_url:
        csv.contact_form_url = db_row[14]
    if db_row[15] and not csv.linkedin:
        csv.linkedin = str(db_row[15]).strip() or None
    if db_row[11] and not csv.city:
        csv.city = str(db_row[11]).strip()
    if db_row[13] and not csv.country:
        csv.country = str(db_row[13]).strip()


# ---------------------------------------------------------------------------
# Website resolution (shared between person and company paths)
# ---------------------------------------------------------------------------

def _resolve_website(
    csv: CsvRow,
    validator: WebsiteEmailValidator,
    type : Literal["person", "company"] = "person"
) -> Tuple[str, bool]:
    """
    Return *(resolved_url, needs_scraping)*.

    Checks in order:
      1. CSV-provided URL → validate → DB dedup check
      2. Google search by company name → validate → DB dedup check

    Back-fills *csv* from DB when the domain already exists.
    """
    website = csv.website

    if website:
        if not validator.validate_website(website):
            logger.info(f"Website rejected by validator: {website}")
            return "", False

        existing = contact_repository.get_contact_by_domain(website)
        if existing:
            logger.info("Website domain already in DB; reusing stored fields")
            _populate_from_db(existing, csv)
            return website, False

        return website, True

    # No URL from CSV – try Google search by company name
    if not validator.skip_website_search and csv.name:
        search_query = ""
        if type == "person":
            if csv.fullname:
                search_query = f"{csv.fullname} {csv.name}"
            else:
                search_query = f"{csv.fname} {csv.lname} {csv.name}"
        else:
            search_query = f"{csv.name} {csv.location}"
        logger.info(f"Website search: '{search_query}'")
        urls, local_panel = validator.search_google(search_query, looking_for="website")

        # Prefer the local-panel website, then fall back to organic results
        candidates: List[str] = []
        if local_panel and local_panel.get("website"):
            candidates.append(local_panel["website"])
        candidates.extend(urls)

        for candidate in candidates:
            if validator.validate_website(candidate):
                logger.info(f"Website search SUCCESS: '{candidate}'")
                existing = contact_repository.get_contact_by_domain(candidate)
                if existing:
                    logger.info("Website domain (search result) already in DB; reusing stored fields")
                    _populate_from_db(existing, csv)
                    return candidate, False
                return candidate, True

    return "", False


# ---------------------------------------------------------------------------
# Website scraping
# ---------------------------------------------------------------------------

def _scrape_website(url: str, validator: WebsiteEmailValidator) -> ScrapedWebData:
    """Scrape *url* and return raw contact data."""
    logger.info(f"Scraping website for contact data: '{url}'")
    try:
        emails, phones, contact_page, location, city, country = (
            validator.find_contact_info_on_website(url)
        )
        logger.info(
            f"Scraping done: emails={len(emails)} phones={len(phones)} "
            f"contact_page={contact_page!r} city={city!r} country={country!r}"
        )
        return ScrapedWebData(
            emails=emails or [],
            phones=phones or [],
            contact_page=contact_page,
            location=location,
            city=city,
            country=country,
        )
    except Exception as exc:
        logger.warning(f"Web enrichment error for '{url}': {exc}")
        return ScrapedWebData()


# ---------------------------------------------------------------------------
# Email / MX helpers
# ---------------------------------------------------------------------------

def _resolve_mx(
    email: str,
    is_generic: bool,
    mx_cache: Dict,
    new_mx_records: List,
    generic_mx: Set[str],
    site_builder_domains: Set[str],
) -> Tuple[Optional[str], bool]:
    """
    Return *(mx_host, is_generic_email)*.
    MX is only looked up for non-generic, non-synthetic emails.
    """
    if email.endswith("@nodomaine.com"):
        return None, is_generic

    _, domain = email.split("@", 1)
    domain = domain.strip().lower()
    mx_host = None

    try:
        mx_host, mx_root = mx_resolver.resolve_mx_record(domain, mx_cache, new_mx_records)
        if not mx_host:
            logger.info(f"No valid MX record for domain '{domain}'; preserving row anyway")
        if mx_root:
            mx_root_email = f"mx@{mx_root}"
            mx_is_generic, _ = email_classifiers.classify_email(
                mx_root_email,
                generic_domains=set(),
                generic_users=set(),
                generic_mx=generic_mx,
                site_builder_domains=site_builder_domains,
            )
            is_generic = bool(is_generic or mx_is_generic)
    except Exception as exc:
        logger.warning(f"MX resolution error for {domain}: {exc}; preserving row anyway")

    return mx_host, is_generic


def _make_synthetic_email(
    domain: str,
    fname: str,
    lname: str,
    fullname: str,
    name: str,
) -> str:
    """Build the best possible synthetic / fallback email for a domain."""
    real_domain = domain or "nodomaine.com"
    candidate = _prefer_named_synthetic_email(real_domain, fname, lname, fullname)
    if candidate:
        return candidate
    company_token = _slugify_for_email(name)
    if company_token:
        logger.info(f"Using company-name synthetic email (Email Cleaning Rule): '{company_token}@{real_domain}'")
        return f"{company_token}@{real_domain}"
    return f"postmaster+{uuid.uuid4().hex}@{real_domain}"


# ---------------------------------------------------------------------------
# Person enrichment
# ---------------------------------------------------------------------------

def _enrich_person(
    csv: CsvRow,
    website: str,
    scraped: ScrapedWebData,
    validator: WebsiteEmailValidator,
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    mx_cache: Dict,
    new_mx_records: List,
    row_stats: RowStats,
) -> PersonContactData:
    """
    Build a fully-enriched :class:`PersonContactData` for the person identity
    extracted from *csv*.  Scraped web data is used only to fill gaps not already
    covered by CSV values.
    """

    person = PersonContactData(
        fullname=csv.fullname or " ".join(part for part in [csv.fname, csv.lname] if part) or None,
        fname=csv.fname or None,
        lname=csv.lname or None,
        name=csv.name or None,
        website=website or None,
        position=csv.position,
        phone=csv.phone,
        mobile=csv.mobile,
        fax=csv.fax,
        linkedin=csv.linkedin,
        address=csv.address,
        city=csv.city or None,
        zip_code=csv.zip_code,
        country=csv.country or None,
        contact_form_url=csv.contact_form_url,
        image=csv.image,
        sourcefile=csv.sourcefile,
        ca=csv.ca,
        activite=csv.activite,
        email=csv.email,
    )

    # --- Fill gaps from scraped data (never overwrite CSV values) -----------
    if scraped.contact_page and not person.contact_form_url:
        person.contact_form_url = scraped.contact_page
        row_stats.contact_form_found = True
        logger.info(f"Contact form found: '{scraped.contact_page}'")

    if not person.email and scraped.emails:
        person.email = validator.email_validator.filter_emails(scraped.emails)

    if not person.phone and scraped.phones:
        person.phone = (scraped.phones[0] or "").strip() or None
        if person.phone:
            logger.info(f"Phone found: '{person.phone}'")

    if not person.city and scraped.city:
        person.city = scraped.city
    if not person.country and scraped.country:
        person.country = scraped.country

    # --- Person LinkedIn search -------------------------------------------
    if not validator.skip_website_search and not person.linkedin and person.fullname:
        try:
            logger.info(f"Person LinkedIn search: '{person.fullname}'")
            urls, _ = validator.search_google(
                person.fullname, looking_for="linkedin_profile"
            )
            if urls:
                person.linkedin = urls[0]
                logger.info(f"Person LinkedIn found: '{person.linkedin}'")
        except Exception as exc:
            logger.debug(f"Person LinkedIn search failed (non-fatal): {exc}")

    # --- Synthetic e-mail fallback ----------------------------------------
    if not person.email or "@" not in person.email:
        fallback_domain = extract_domain(website) or "nodomaine.com"
        person.email = _make_synthetic_email(
            fallback_domain,
            csv.fname or "",
            csv.lname or "",
            csv.fullname or "",
            csv.name or "",
        )
        row_stats.synthetic_email_used = True
        logger.info(f"Synthetic fallback email: '{person.email}'")

    # --- E-mail classification -------------------------------------------
    is_generic, is_user_generic = email_classifiers.classify_email(
        person.email, generic_domains, generic_users, generic_mx, site_builder_domains
    )

    # --- MX resolution ---------------------------------------------------
    mx_host, is_generic = _resolve_mx(
        person.email, is_generic, mx_cache, new_mx_records, generic_mx, site_builder_domains
    )
    person.mx_host = mx_host
    person.is_generic_email = is_generic
    person.is_user_generic = is_user_generic

    # --- Country fallback from TLD ---------------------------------------
    if not person.country:
        try:
            from_tld = get_country_from_email_domain(person.email)
            if from_tld:
                person.country = from_tld
        except Exception as exc:
            logger.debug(f"Country enrichment failed for {person.email}: {exc}")

    # CSV city/country always win if present
    if csv.city:
        person.city = csv.city
    if csv.country:
        person.country = csv.country

    logger.info(
        f"Person email classification: is_generic={person.is_generic_email}, "
        f"is_user_generic={person.is_user_generic}"
    )
    return person


# ---------------------------------------------------------------------------
# Company enrichment
# ---------------------------------------------------------------------------

def _enrich_company(
    csv: CsvRow,
    website: str,
    scraped: ScrapedWebData,
    validator: WebsiteEmailValidator,
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    mx_cache: Dict,
    new_mx_records: List,
) -> Optional[CompanyContactData]:
    """
    Build a :class:`CompanyContactData` record for the company associated with
    *website*.  Returns *None* if the domain cannot be extracted.
    """
    domain = extract_domain(website)
    if not domain:
        return None

    company = CompanyContactData(
        name=csv.name or None,
        website=website,
        phone=scraped.phones[0] if scraped.phones else csv.phone,
        address=csv.address,
        city=scraped.city or csv.city or None,
        zip_code=csv.zip_code,
        country=scraped.country or csv.country or None,
        contact_form_url=scraped.contact_page or csv.contact_form_url,
        linkedin=csv.linkedin,
        position=csv.position,
        image=csv.image,
        sourcefile=csv.sourcefile,
        ca=csv.ca,
        activite=csv.activite,
    )

    # --- Best email for company ------------------------------------------
    selected_email = ""
    for candidate in scraped.emails:
        candidate = (candidate or "").strip().lower()
        if candidate and "@" in candidate:
            selected_email = candidate
            break

    if not selected_email:
        company_local = _slugify_for_email(csv.name or "") or "company"
        selected_email = f"{company_local}@{domain}"

    company.email = selected_email

    # --- Company LinkedIn search ----------------------------------------
    if not validator.skip_website_search and csv.name and not company.linkedin:
        try:
            logger.info(f"Company LinkedIn search: '{csv.name}'")
            urls, _ = validator.search_google(
                csv.name, looking_for="linkedin_company"
            )
            if urls:
                company.linkedin = urls[0]
                logger.info(f"Company LinkedIn found: '{company.linkedin}'")
        except Exception as exc:
            logger.debug(f"Company LinkedIn search failed (non-fatal): {exc}")

    # --- Email classification + MX --------------------------------------
    is_generic, is_user_generic = email_classifiers.classify_email(
        company.email, generic_domains, generic_users, generic_mx, site_builder_domains
    )
    mx_host, is_generic = _resolve_mx(
        company.email, is_generic, False, mx_cache, new_mx_records, generic_mx, site_builder_domains
    )
    company.mx_host = mx_host
    company.is_generic_email = is_generic
    company.is_user_generic = is_user_generic

    return company


# ---------------------------------------------------------------------------
# LinkedIn URL helpers (called before enrichment)
# ---------------------------------------------------------------------------

def _handle_linkedin_in_website(csv: CsvRow) -> None:
    """
    Inspect *csv.website*:
    - Valid LinkedIn /in/ or /company/ URL  → move to *csv.linkedin*, clear website.
    - Any other linkedin.com URL            → discard website.
    Mutates *csv* in-place.
    """
    if not csv.website:
        return

    if _is_linkedin_url(csv.website):
        csv.linkedin = csv.linkedin or csv.website
        logger.info("Input URL is a valid LinkedIn URL (/in/ or /company/); storing in linkedin field")
        csv.website = ""
    elif "linkedin.com" in csv.website.lower():
        logger.info(f"Input URL is a LinkedIn URL with unsupported path, discarding: '{csv.website}'")
        csv.website = ""


def _sanitize_linkedin_field(csv: CsvRow) -> None:
    """Discard invalid LinkedIn values from the mapped linkedin field."""
    if csv.linkedin and not _is_linkedin_url(csv.linkedin):
        logger.info(f"LinkedIn URL in 'linkedin' field has unsupported path format, discarding: '{csv.linkedin}'")
        csv.linkedin = None


# ---------------------------------------------------------------------------
# Main row processor
# ---------------------------------------------------------------------------

def _process_contact_row(
    row: Any,
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    sourcefile: Optional[str],
    csv_mapping: CsvMapping,
    default_values: Dict[str, Any],
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]],
    new_mx_records: List[Tuple[str, str, str]],
    validator: WebsiteEmailValidator,
) -> Tuple[Optional[Tuple], RowStats, List[Tuple]]:
    """
    Process one CSV row and return:
      - a DB tuple for the *person* contact (or None to skip)
      - row-level stats
      - zero or more extra DB tuples for associated *company* contacts
    """
    row_stats = RowStats()
    extra_contacts: List[Tuple] = []

    # ------------------------------------------------------------------
    # 1. Extract every CSV value up-front (defaults applied here only)
    # ------------------------------------------------------------------
    csv = _extract_csv_row(row, csv_mapping, default_values, sourcefile)

    # ------------------------------------------------------------------
    # 2. Skip entirely if no identity field is present
    # ------------------------------------------------------------------
    if not (csv.fullname or csv.fname or csv.lname or csv.name or csv.email or csv.website):
        logger.info("Skipped: row has none of fullname/fname/lname/name/email/url")
        return None, row_stats, extra_contacts

    # ------------------------------------------------------------------
    # 3. Sanitise LinkedIn fields
    # ------------------------------------------------------------------
    _sanitize_linkedin_field(csv)
    _handle_linkedin_in_website(csv)

    # ------------------------------------------------------------------
    # 4. Resolve website (DB dedup + optional Google search)
    #    Back-fills csv fields from DB when domain already known.
    # ------------------------------------------------------------------
    website, needs_scraping = _resolve_website(csv, validator, type="person")

    # ------------------------------------------------------------------
    # 5. Scrape the website once (shared by both person + company paths)
    # ------------------------------------------------------------------
    scraped = ScrapedWebData()

    if needs_scraping:
        scraped = _scrape_website(website, validator)

    # ------------------------------------------------------------------
    # 6. Enrich person contact
    # ------------------------------------------------------------------
    person = _enrich_person(
        csv=csv,
        website=website,
        scraped=scraped,
        validator=validator,
        generic_domains=generic_domains,
        generic_users=generic_users,
        generic_mx=generic_mx,
        site_builder_domains=site_builder_domains,
        mx_cache=mx_cache,
        new_mx_records=new_mx_records,
        row_stats=row_stats,
    )

    # ------------------------------------------------------------------
    # 7. Enrich company contact (only when URL was found via Google search)
    # ------------------------------------------------------------------

    website_company, needs_scraping_company = _resolve_website(csv, validator, type="company")

    if needs_scraping_company:
        scraped_company = _scrape_website(website_company, validator)

        company = _enrich_company(
            csv=csv,
            website=website_company,
            scraped=scraped_company,
            validator=validator,
            generic_domains=generic_domains,
            generic_users=generic_users,
            generic_mx=generic_mx,
            site_builder_domains=site_builder_domains,
            mx_cache=mx_cache,
            new_mx_records=new_mx_records,
        )
        if company is not None:
            extra_contacts.append(company.to_tuple())
            logger.info(f"Company contact staged for '{website}'")

            # Back-fill company LinkedIn onto the person record if missing
            if company.linkedin and not person.linkedin:
                person.linkedin = company.linkedin


    return person.to_tuple(), row_stats, extra_contacts


# ---------------------------------------------------------------------------
# Batch insert
# ---------------------------------------------------------------------------

def _insert_batch(
    contact_batch: List[Tuple],
    new_mx_records: List[Tuple[str, str, str]],
    stats: Dict[str, Any],
    start_time: float,
    total_rows: int,
    processed: int,
) -> None:
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
                    f"ETA: {data_transformers.format_eta(eta_seconds)}"
                )
            except Exception as exc:
                logger.error(f"Failed to insert contacts batch: {exc}")

    except Exception as exc:
        logger.error(f"Batch insertion error: {exc}")


# ---------------------------------------------------------------------------
# Reference data loader
# ---------------------------------------------------------------------------

def _load_reference_data(
    log_prefix: str = "Loaded",
) -> tuple[set[str], set[str], set[str], set[str], set[str]]:
    generic_domains = set(contact_repository.get_all_generic_domains())
    generic_users = set(contact_repository.get_all_generic_users())
    generic_mx = set(contact_repository.get_all_mxrecords())
    site_builder_domains = set(contact_repository.get_all_site_builder_domains())
    not_visiting_domains = set(contact_repository.get_all_not_visiting_domains())

    logger.debug(
        f"{log_prefix}: "
        f"{len(generic_domains)} generic domains, "
        f"{len(generic_users)} generic users, "
        f"{len(generic_mx)} generic MX, "
        f"{len(site_builder_domains)} site builder domains, "
        f"{len(not_visiting_domains)} not-visiting domains"
    )
    return generic_domains, generic_users, generic_mx, site_builder_domains, not_visiting_domains


# ---------------------------------------------------------------------------
# Main CSV orchestrator
# ---------------------------------------------------------------------------

def process_database_seeding(
    config: ProcessingConfig,
    job_id: str | None = None,
) -> Dict[str, Any]:
    """Read CSV, enrich contacts, validate MX, classify emails, and batch-write to DB."""
    global logger
    logger = setup_logging(
        module_name="dbSeeder", job_id=job_id, buffer_size=config.batch_size
    )

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
        generic_domains, generic_users, generic_mx, site_builder_domains, not_visiting_domains = (
            _load_reference_data()
        )
    except Exception as exc:
        logger.error(f"Failed to load reference data: {exc}")
        stats["errors"].append(f"Reference data loading failed: {exc}")
        return stats

    start_row = 1
    if job_id:
        existing_job = job_store.get_job(job_id)
        if existing_job is not None and existing_job.current_row > 1:
            start_row = existing_job.current_row
            for key in ("processed", "inserted", "updated", "skipped"):
                stats[key] = int(existing_job.result.get(key, 0)) if existing_job.result else 0
            logger.info(f"Loaded job progress for {job_id}: resume from row {start_row}")

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
        raise exc

    contact_batch: List[Tuple] = []
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    new_mx_records: List[Tuple[str, str, str]] = []
    batches_processed = 0
    start_time = time.time()

    try:
        for row_number, (_, row) in enumerate(contacts_df.iterrows(), start=1):
            if row_number < start_row:
                continue

            if job_id and job_store.is_job_pause_requested(job_id):
                logger.info(f"Job {job_id} pause requested, stopping at checkpoint row {row_number}")
                job_store.update(job_id, "status", "paused")
                raise JobInterruptionRequested("pause")

            stats["processed"] += 1

            try:
                contact_data, row_stats, extra_contacts = _process_contact_row(
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

                _merge_row_stats(stats, row_stats)

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

                    csv_row = _extract_csv_row(row, config.csv_mapping, config.default_values or {}, None)
                    has_required = bool(
                        csv_row.fullname or csv_row.fname or csv_row.lname
                        or csv_row.name or csv_row.email
                    )
                    if not has_required:
                        stats["rows_skipped_no_required_field"] += 1
                    elif csv_row.email:
                        stats["rows_skipped_invalid_mx"] += 1
                    else:
                        stats["rows_skipped_no_email_found"] += 1

                if (
                    len(contact_batch) >= config.batch_size
                    or stats["processed"] == stats["total_rows"]
                ):
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
                        job_store.update(
                            job_id, "progress",
                            {"current_row": next_checkpoint_row, "total_rows": stats["total_rows"], "result": stats},
                        )

                    batches_processed += 1
                    is_last_batch = stats["processed"] >= stats["total_rows"]

                    if validator and not is_last_batch:
                        try:
                            validator.prepare_next_batch()
                            logger.debug("Stray tabs closed, primary tab reset to about:blank")
                        except Exception as exc:
                            logger.debug(f"Stray tab cleanup failed (non-fatal): {exc}")

                        is_attached = bool(getattr(validator.driver, "_attach_port", None))
                        if batches_processed % PERIODIC_BROWSER_RESTART_BATCHES == 0 and not is_attached:
                            try:
                                validator.restart_browser(reason="periodic")
                                logger.debug(f"Periodic browser restart completed at batch {batches_processed}")
                            except Exception as exc:
                                logger.debug(f"Periodic browser restart failed: {exc}")

                    try:
                        generic_domains, generic_users, generic_mx, site_builder_domains, not_visiting_domains = (
                            _load_reference_data(log_prefix="Batch reference refresh")
                        )
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
                        logger.debug(f"Batch reference refresh completed for batch {batches_processed}")
                    except Exception as exc:
                        logger.debug(f"Failed to refresh reference data after batch {batches_processed}: {exc}")

                    flush_buffered_log_handlers(logger)
                    contact_batch.clear()
                    new_mx_records.clear()

            except JobInterruptionRequested as exc:
                if not job_id:
                    raise
                if str(exc) == "cancelled":
                    logger.info(f"Job {job_id} cancellation acknowledged during row {row_number}")
                    break
                logger.info(f"Job {job_id} pause acknowledged during row {row_number}")
                job_store.update(job_id, "status", "paused")
                flush_buffered_log_handlers(logger)
                return stats
            except Exception as exc:
                logger.warning(f"Error processing row {stats['processed']}: {exc}")
                stats["errors"].append(f"Row {stats['processed']}: {exc}")
                continue

    finally:
        if validator:
            logger.info("Closing NoDriver browser...")
            validator.quit()

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
        _final_job = job_store.get_job(job_id)
        if _final_job and _final_job.status == "paused":
            job_store.update(
                job_id, "progress",
                {"current_row": stats["processed"] + 1, "total_rows": stats["total_rows"], "result": stats},
            )
        elif stats["processed"] >= stats["total_rows"]:
            job_store.update(
                job_id, "progress",
                {"current_row": stats["total_rows"], "total_rows": stats["total_rows"], "result": stats},
            )

    flush_buffered_log_handlers(logger)
    return stats


# ---------------------------------------------------------------------------
# Single-URL entry point
# ---------------------------------------------------------------------------

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
        "total_rows": 1, "processed": 1, "inserted": 0, "updated": 0, "skipped": 0,
        "emails_found": 0, "websites_found": 1, "mx_failed": 0,
        "rows_skipped_no_required_field": 0, "rows_skipped_invalid_mx": 0,
        "rows_skipped_no_email_found": 0, "errors": [],
        "contact_form_discoveries": 0, "synthetic_emails_created": 0, "url_result": None,
    }

    logger.info(
        "SEED_SINGLE_URL_START "
        f"job_id={job_id or 'none'} "
        f"url='{url}' "
        f"web_scraping={'on' if enable_web_scraping else 'off'} "
        f"google_search={'on' if (enable_web_scraping and not skip_google_search) else 'off'}"
    )

    start_time = time.time()
    validator = WebsiteEmailValidator(
            skip_website_search=skip_google_search,
            site_timeout_seconds=SITE_TIMEOUT_SECONDS,
        )
    validator.setup_driver()
    try:
        generic_domains, generic_users, generic_mx, site_builder_domains, not_visiting_domains = (
            _load_reference_data()
        )

        
        validator.update_reference_filters(
            generic_domains=generic_domains,
            generic_users=generic_users,
            site_builder_domains=site_builder_domains,
            not_visiting_domains=not_visiting_domains,
        )

        contact_data, row_stats, extra_contacts = _process_contact_row(
            row={"url": (url or "").strip()},
            generic_domains=generic_domains,
            generic_users=generic_users,
            generic_mx=generic_mx,
            site_builder_domains=site_builder_domains,
            sourcefile=sourcefile or (url or "single-url"),
            csv_mapping=CsvMapping(url="url"),
            default_values={},
            mx_cache={},
            new_mx_records=[],
            validator=validator,
        )

        _merge_row_stats(stats, row_stats)

        if contact_data is None:
            stats.update(skipped=1, mx_failed=1, rows_skipped_no_email_found=1)
            return stats

        inserted, updated = contact_repository.batch_create_contacts([*extra_contacts, contact_data])
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
            validator.quit()
        elapsed = time.time() - start_time
        logger.info(
            "SEED_SINGLE_URL_END "
            f"inserted={stats['inserted']} "
            f"updated={stats['updated']} "
            f"errors={len(stats['errors'])} "
            f"elapsed={data_transformers.format_eta(elapsed)}"
        )
        flush_buffered_log_handlers(logger)