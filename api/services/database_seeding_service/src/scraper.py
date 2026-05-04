"""Main CSV processing and database seeding orchestrator."""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple , Literal
from urllib.parse import urlparse
from api.services.database_seeding_service.src.models import CsvRow, RowStats, PersonContactData, CompanyContactData, ScrapedWebData

import pandas as pd

from api.services.utils.job_manager import job_store

from api.models import CsvMapping
from api.services.database_seeding_service.src.models import ProcessingConfig
from api.services.database_seeding_service.src.utils import contact_repository, data_transformers, email_classifiers, mx_resolver
from api.services.database_seeding_service.src.utils.contact_repository import CONTACT_COLUMNS_MAP
from api.services.utils.logging_config import flush_buffered_log_handlers, get_logger, setup_logging
from api.services.database_seeding_service.src.utils.tld_country_mapper import get_country_from_email_domain
from api.services.database_seeding_service.src.utils.url_utils import extract_domain
from api.services.database_seeding_service.src.main.web_validator import WebsiteEmailValidator
from api.services.database_seeding_service.src.utils.extractor_social_media import extract_social_links_from_urls
from api.services.database_seeding_service.src.utils.exceptions import JobInterruptionRequested, WebsearchFailure
logger = get_logger(__name__)

SITE_TIMEOUT_SECONDS = 12
PERIODIC_BROWSER_RESTART_BATCHES = 100
_COMPANY_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "tmp", "company")
COMPANY_CACHE_FILE = os.path.join(_COMPANY_CACHE_DIR, "company_cache.json")




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


def _load_company_cache() -> Dict[str, dict]:
    try:
        if os.path.exists(COMPANY_CACHE_FILE):
            with open(COMPANY_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        logger.debug(f"Failed to load company cache: {exc}")
    return {}


def _save_company_cache(cache: Dict[str, dict]) -> None:
    try:
        os.makedirs(_COMPANY_CACHE_DIR, exist_ok=True)
        with open(COMPANY_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.debug(f"Failed to save company cache: {exc}")





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
    Uses CONTACT_COLUMNS_MAP for named index access instead of magic numbers.
    """
    def _col(name: str):
        idx = CONTACT_COLUMNS_MAP[name]
        return db_row[idx] if idx < len(db_row) else None

    db_email = _col("email")
    if db_email and not csv.email:
        db_email = str(db_email).strip().lower()
        if not (db_email.startswith("postmaster") and (csv.fname or csv.lname or csv.fullname or csv.name)):
            csv.email = db_email
    db_phone = _col("phone")
    if db_phone and not csv.phone:
        csv.phone = db_phone
    db_mobile = _col("mobile")
    if db_mobile and not csv.mobile:
        csv.mobile = db_mobile
    db_fax = _col("fax")
    if db_fax and not csv.fax:
        csv.fax = db_fax
    db_contact_form = _col("urlcontactform")
    if db_contact_form and not csv.contact_form_url:
        csv.contact_form_url = db_contact_form
    db_linkedin = _col("linkedin")
    if db_linkedin and not csv.linkedin:
        csv.linkedin = str(db_linkedin).strip() or None
    db_city = _col("city")
    if db_city and not csv.city:
        csv.city = str(db_city).strip()
    db_country = _col("country")
    if db_country and not csv.country:
        csv.country = str(db_country).strip()


# ---------------------------------------------------------------------------
# Website resolution (shared between person and company paths)
# ---------------------------------------------------------------------------

def _resolve_website(
    csv: CsvRow,
    validator: WebsiteEmailValidator,
    type : Literal["person", "company"] = "person"
) -> Tuple[str, bool, Optional[Dict[str, str]], Optional[Dict[str, Set[str]]]]:
    """
    Return *(resolved_url, needs_scraping, local_panel, social_links_from_candidates)*.

    Checks in order:
      1. CSV-provided URL → validate → if valid skip google search → DB dedup check
      2. Google search by company name and full name → validate → DB dedup check

    Back-fills *csv* from DB when the domain already exists.
    local_panel contains Google My Business data (phone, directions, etc.) if available.
    social_links_from_candidates contains social URLs found in *all* Google results,
    including those that were discarded by the domain filter (e.g. LinkedIn).
    """
    website = csv.website

    if website:
        website_valid = True
        if not validator.validate_website(website):
            logger.info(f"Website rejected by validator: {website}")
            # We wont return now because we can still find a valid website.
            website_valid = False

        existing = contact_repository.get_contact_by_domain(website)
        if existing and website_valid:
            logger.info("Website domain already in DB; reusing stored fields")
            _populate_from_db(existing, csv)
            return website, False, None, None
        if website_valid:
            return website, True, None, None

    # No URL from CSV – try Google search by company name
    search_social_links: Optional[Dict[str, Set[str]]] = None
    if not validator.skip_website_search and (csv.name):
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

        # Extract social links from ALL candidates (including excluded domains)
        if candidates:
            search_social_links = extract_social_links_from_urls(candidates)
            if search_social_links:
                logger.info(
                    f"Social links found in search candidates: "
                    f"{', '.join(f'{k}={len(v)}' for k, v in search_social_links.items())}"
                )

        for candidate in candidates:
            if validator.validate_website(candidate):
                logger.info(f"Website search SUCCESS: '{candidate}'")
                existing = contact_repository.get_contact_by_domain(candidate)
                if existing:
                    logger.info("Website domain (search result) already in DB; reusing stored fields")
                    _populate_from_db(existing, csv)
                    return candidate, False, local_panel, search_social_links
                return candidate, True, local_panel, search_social_links

    return "", False, None, search_social_links


# ---------------------------------------------------------------------------
# Website scraping
# ---------------------------------------------------------------------------

def _scrape_website(url: str, validator: WebsiteEmailValidator) -> ScrapedWebData:
    """Scrape *url* and return raw contact data."""
    logger.info(f"Scraping website for contact data: '{url}'")
    try:
        return  validator.find_contact_info_on_website(url)
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
    local_panel: Optional[Dict[str, str]] = None,
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
        person.phone = (".".join(scraped.phones).capitalize() or "").strip() or None
        if person.phone:
            logger.info(f"Phone found: '{person.phone}'")

    if not person.city and scraped.city:
        person.city = scraped.city
    if not person.country and scraped.country:
        person.country = scraped.country
    if not person.address and scraped.location:
        person.address = scraped.location

    # --- Fill gaps from Google local panel (never overwrite CSV values) ------
    if local_panel:
        if not person.phone and local_panel.get("phone"):
            person.phone = local_panel["phone"]
            logger.info(f"Phone from Google local panel: '{person.phone}'")
        if not person.address and local_panel.get("address"):
            person.address = local_panel["address"]
            logger.info(f"Address from Google local panel: '{person.address}'")

    # --- Social links ------------------------------------------------------
    if scraped.social_links:
        social_links = {platform: list(urls) for platform, urls in scraped.social_links.items()}
        if not person.linkedin and social_links.get("linkedin"):
            person.linkedin = social_links["linkedin"][0]
        if not person.whatsapp and social_links.get("whatsapp"):
            person.whatsapp = social_links["whatsapp"][0]
        if not person.facebook and social_links.get("facebook"):
            person.facebook = social_links["facebook"][0]
        if not person.instagram and social_links.get("instagram"):
            person.instagram = social_links["instagram"][0]
        if not person.tiktok and social_links.get("tiktok"):
            person.tiktok = social_links["tiktok"][0]
        if not person.youtube and social_links.get("youtube"):
            person.youtube = social_links["youtube"][0]
        if not person.telegram and social_links.get("telegram"):
            person.telegram = social_links["telegram"][0]
        if not person.calendly and social_links.get("calendly"):
            person.calendly = social_links["calendly"][0]

    # --- Names extracted from website text ---------------------------------
    # scraped.person_name maps to fullname, scraped.company_name maps to name
    if not person.fullname and scraped.person_name:
        person.fullname = scraped.person_name
    if not person.name and scraped.company_name:
        person.name = scraped.company_name

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

    # --- Syntax / deliverability status -----------------------------------
    if person.email.endswith("@nodomaine.com"):
        person.status = "synthetic"
    elif not mx_host:
        person.status = "ko"
    elif is_generic or is_user_generic:
        person.status = "generic"
    else:
        person.status = "valid"

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


def _enrich_extra_contacts(
    person: PersonContactData,
    extra_emails: List[str],
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    mx_cache: Dict,
    new_mx_records: List,
) -> List[Tuple]:
    extra_contacts: List[Tuple] = []
    for extra_email in extra_emails:
        extra_person = PersonContactData(
            email=extra_email.strip().lower(),
            fullname=person.fullname,
            fname=person.fname,
            lname=person.lname,
            website=person.website,
            position=person.position,
            phone=person.phone,
            mobile=person.mobile,
            fax=person.fax,
            name=person.name,
            address=person.address,
            city=person.city,
            zip_code=person.zip_code,
            country=person.country,
            contact_form_url=person.contact_form_url,
            linkedin=person.linkedin,
            image=person.image,
            sourcefile=person.sourcefile,
            ca=person.ca,
            activite=person.activite,
            whatsapp=person.whatsapp,
            facebook=person.facebook,
            instagram=person.instagram,
            tiktok=person.tiktok,
            youtube=person.youtube,
            telegram=person.telegram,
            calendly=person.calendly,
        )

        is_generic, is_user_generic = email_classifiers.classify_email(
            extra_person.email, generic_domains, generic_users, generic_mx, site_builder_domains
        )
        mx_host, is_generic = _resolve_mx(
            extra_person.email, is_generic, mx_cache, new_mx_records, generic_mx, site_builder_domains
        )
        extra_person.mx_host = mx_host
        extra_person.is_generic_email = is_generic
        extra_person.is_user_generic = is_user_generic

        if not mx_host:
            extra_person.status = "ko"
        elif is_generic or is_user_generic:
            extra_person.status = "generic"
        else:
            extra_person.status = "valid"

        extra_contacts.append(extra_person.to_tuple())
        logger.info(f"Extra person contact staged for '{extra_person.email}'")

    return extra_contacts


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
    local_panel: Optional[Dict[str, str]] = None,
) -> Optional[CompanyContactData]:
    """
    Build a :class:`CompanyContactData` record for the company associated with
    *website*.  Scraped web data is used only to fill gaps not already
    covered by CSV values.  Returns *None* if the domain cannot be extracted.
    """
    domain = extract_domain(website)
    if not domain:
        return None

    company = CompanyContactData(
        name=csv.name or None,
        website=website,
        phone=csv.phone,
        address=csv.address,
        city=csv.city or None,
        zip_code=csv.zip_code,
        country=csv.country or None,
        contact_form_url=csv.contact_form_url,
        linkedin=csv.linkedin,
        position=csv.position,
        image=csv.image,
        sourcefile=csv.sourcefile,
        ca=csv.ca,
        activite=csv.activite,
    )

    # --- Fill gaps from scraped data (never overwrite CSV values) -----------
    if scraped.contact_page and not company.contact_form_url:
        company.contact_form_url = scraped.contact_page
        logger.info(f"Contact form found: '{scraped.contact_page}'")

    if not company.email and scraped.emails:
        company.email = validator.email_validator.filter_emails(scraped.emails) or ""

    if not company.phone and scraped.phones:
        company.phone = (".".join(scraped.phones).capitalize() or "").strip() or None
        if company.phone:
            logger.info(f"Phone found: '{company.phone}'")

    if not company.city and scraped.city:
        company.city = scraped.city
    if not company.country and scraped.country:
        company.country = scraped.country
    if not company.address and scraped.location:
        company.address = scraped.location

    # --- Fill gaps from Google local panel (never overwrite CSV values) ------
    if local_panel:
        if not company.phone and local_panel.get("phone"):
            company.phone = local_panel["phone"]
            logger.info(f"Company phone from Google local panel: '{company.phone}'")
        if not company.address and local_panel.get("address"):
            company.address = local_panel["address"]
            logger.info(f"Company address from Google local panel: '{company.address}'")

    # --- Social links ------------------------------------------------------
    if scraped.social_links:
        social_links = {platform: list(urls) for platform, urls in scraped.social_links.items()}
        if not company.linkedin and social_links.get("linkedin"):
            company.linkedin = social_links["linkedin"][0]
        if not company.whatsapp and social_links.get("whatsapp"):
            company.whatsapp = social_links["whatsapp"][0]
        if not company.facebook and social_links.get("facebook"):
            company.facebook = social_links["facebook"][0]
        if not company.instagram and social_links.get("instagram"):
            company.instagram = social_links["instagram"][0]
        if not company.tiktok and social_links.get("tiktok"):
            company.tiktok = social_links["tiktok"][0]
        if not company.youtube and social_links.get("youtube"):
            company.youtube = social_links["youtube"][0]
        if not company.telegram and social_links.get("telegram"):
            company.telegram = social_links["telegram"][0]
        if not company.calendly and social_links.get("calendly"):
            company.calendly = social_links["calendly"][0]

    # --- Names extracted from website text ---------------------------------
    if not company.name and scraped.company_name:
        company.name = scraped.company_name

    # --- Company LinkedIn search ----------------------------------------
    if not validator.skip_website_search and company.name and not company.linkedin:
        try:
            logger.info(f"Company LinkedIn search: '{company.name}'")
            urls, _ = validator.search_google(
                company.name, looking_for="linkedin_company"
            )
            if urls:
                company.linkedin = urls[0]
                logger.info(f"Company LinkedIn found: '{company.linkedin}'")
        except Exception as exc:
            logger.debug(f"Company LinkedIn search failed (non-fatal): {exc}")

    # --- Synthetic e-mail fallback ----------------------------------------
    if not company.email or "@" not in company.email:
        company_local = _slugify_for_email(company.name or "") or "company"
        company.email = f"{company_local}@{domain}"
        logger.info(f"Company synthetic fallback email: '{company.email}'")

    # --- E-mail classification -------------------------------------------
    is_generic, is_user_generic = email_classifiers.classify_email(
        company.email, generic_domains, generic_users, generic_mx, site_builder_domains
    )

    # --- MX resolution ---------------------------------------------------
    mx_host, is_generic = _resolve_mx(
        company.email, is_generic, mx_cache, new_mx_records, generic_mx, site_builder_domains
    )
    company.mx_host = mx_host
    company.is_generic_email = is_generic
    company.is_user_generic = is_user_generic

    # --- Syntax / deliverability status -----------------------------------
    if company.email.endswith("@nodomaine.com"):
        company.status = "synthetic"
    elif not mx_host:
        company.status = "ko"
    elif is_generic or is_user_generic:
        company.status = "generic"
    else:
        company.status = "valid"

    # --- Country fallback from TLD ---------------------------------------
    if not company.country:
        try:
            from_tld = get_country_from_email_domain(company.email)
            if from_tld:
                company.country = from_tld
        except Exception as exc:
            logger.debug(f"Country enrichment failed for {company.email}: {exc}")

    # CSV city/country always win if present
    if csv.city:
        company.city = csv.city
    if csv.country:
        company.country = csv.country

    logger.info(
        f"Company email classification: is_generic={company.is_generic_email}, "
        f"is_user_generic={company.is_user_generic}"
    )
    return company


def _enrich_company_contact(
    csv: CsvRow,
    person_website: str,
    scraped: ScrapedWebData,
    validator: WebsiteEmailValidator,
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    mx_cache: Dict,
    new_mx_records: List,
    company_cache: Optional[Dict[str, dict]] = None,
) -> Tuple[List[Tuple], Optional[str]]:
    enriched: List[Tuple] = []
    company_linkedin: Optional[str] = None

    if not csv.name and not csv.website:
        logger.debug("Skipping company enrichment: no company name or website")
        return enriched, company_linkedin

    cache_key = (csv.name or "").strip().lower()

    website_company: str = ""
    company_local_panel: Optional[Dict[str, str]] = None
    scraped_company: ScrapedWebData = scraped
    needs_scraping_company = False
    from_cache = False
    company_search_social_links: Optional[Dict[str, Set[str]]] = None

    if company_cache is not None and cache_key and cache_key in company_cache:
        cached_entry = company_cache[cache_key]
        if cached_entry is None:
            logger.debug(f"Company cache hit (no website found): '{csv.name}'")
            return enriched, company_linkedin
        logger.info(f"Company cache hit for '{csv.name}' -> '{cached_entry.get('website')}'")
        website_company = cached_entry["website"]
        company_local_panel = cached_entry.get("local_panel")
        scraped_company = ScrapedWebData.from_dict(cached_entry.get("scraped", {}))
        from_cache = True
    else:
        website_company, needs_scraping_company, company_local_panel, company_search_social_links = _resolve_website(csv, validator, type="company")

        if not website_company:
            if company_cache is not None and cache_key:
                company_cache[cache_key] = None
                logger.debug(f"Company cache miss (no website found): '{csv.name}'")
            return enriched, company_linkedin

        if not needs_scraping_company:
            return enriched, company_linkedin

    if not from_cache:
        if website_company != person_website:
            logger.info(f"Company website resolved to a different URL than the person website; scraping separately: '{website_company}'")
            scraped_company = _scrape_website(website_company, validator)
        else:
            scraped_company = scraped

        # Merge social links discovered in company Google search candidates
        if company_search_social_links:
            if scraped_company.social_links:
                for platform, urls in company_search_social_links.items():
                    scraped_company.social_links.setdefault(platform, set()).update(urls)
            else:
                scraped_company.social_links = company_search_social_links

    if company_cache is not None and cache_key and not from_cache:
        company_cache[cache_key] = {
            "website": website_company,
            "local_panel": company_local_panel,
            "scraped": scraped_company.to_dict(),
        }
        logger.debug(f"Company cache stored: '{csv.name}' -> '{website_company}'")

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
        local_panel=company_local_panel,
    )
    if company is not None:
        enriched.append(company.to_tuple())
        logger.info(f"Company contact staged for '{person_website}'")
        company_linkedin = company.linkedin

    return enriched, company_linkedin


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
    company_cache: Optional[Dict[str, str]] = None,
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
    website, needs_scraping, person_local_panel, search_social_links = _resolve_website(csv, validator, type="person")

    # ------------------------------------------------------------------
    # 5. Scrape the website once (shared by both person + company paths)
    # ------------------------------------------------------------------
    scraped = ScrapedWebData()

    if needs_scraping:
        scraped = _scrape_website(website, validator)

    # Merge social links discovered in Google search candidates (e.g. LinkedIn)
    if search_social_links:
        if scraped.social_links:
            for platform, urls in search_social_links.items():
                scraped.social_links.setdefault(platform, set()).update(urls)
        else:
            scraped.social_links = search_social_links

    # ------------------------------------------------------------------
    # 6. Enrich person contact
    #   Scraped data will contain multiple emails , we will use the first as the main email for person 
    #   The others will be saved in the extra_contacts with the same shared data as the main one.
    # ------------------------------------------------------------------
    # The main person 
    
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
            local_panel=person_local_panel,
        )
    main_email = person.email if person.email and "@" in person.email else None
    extra_emails = [e for e in scraped.emails if e != main_email]
    extra_contacts.extend(
        _enrich_extra_contacts(
            person=person,
            extra_emails=extra_emails,
            generic_domains=generic_domains,
            generic_users=generic_users,
            generic_mx=generic_mx,
            site_builder_domains=site_builder_domains,
            mx_cache=mx_cache,
            new_mx_records=new_mx_records,
        )
    )
        


    # ------------------------------------------------------------------
    # 7. Enrich company contact (only when URL was found via Google search)
    # ------------------------------------------------------------------
    company_tuples, company_linkedin = _enrich_company_contact(
        csv=csv,
        person_website=website,
        scraped=scraped,
        validator=validator,
        generic_domains=generic_domains,
        generic_users=generic_users,
        generic_mx=generic_mx,
        site_builder_domains=site_builder_domains,
        mx_cache=mx_cache,
        new_mx_records=new_mx_records,
        company_cache=company_cache,
    )
    
    if company_linkedin and not person.linkedin:
        person.linkedin = company_linkedin
    extra_contacts.extend(company_tuples)

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
        module_name="dbSeeder", job_id=job_id, max_size=config.batch_size
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
            on_bad_lines="skip",
        )
        stats["total_rows"] = len(contacts_df)
        logger.info(f"Loaded {stats['total_rows']} contacts")

        # Insert synthetic warmup row at the top to prime the browser
        _warmup_row = {col: "" for col in contacts_df.columns}
        _warmup_row[contacts_df.columns[0]] = "__WARMUP__"
        warmup = pd.DataFrame([_warmup_row])
        contacts_df = pd.concat([warmup, contacts_df], ignore_index=True)
        stats["total_rows"] = len(contacts_df) - 1
        logger.info("Inserted synthetic warmup row at index 0")
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
    company_cache: Dict[str, str] = _load_company_cache()
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

            is_warmup = (row_number == 1)
            if not is_warmup:
                stats["processed"] += 1
            else:
                logger.info("Processing synthetic warmup row...")

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
                    company_cache=company_cache,
                )

                if is_warmup:
                    logger.info("Warmup row completed, discarding results")
                    continue

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

                        if batches_processed % PERIODIC_BROWSER_RESTART_BATCHES == 0:
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
                    _save_company_cache(company_cache)

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
            except WebsearchFailure as exc:
                logger.warning(f"Web search failure for row {stats['processed']}: {exc}")
                stats["errors"].append(f"Row {stats['processed']}: {exc}")
                continue
            except Exception as exc:
                logger.warning(f"Error processing row {stats['processed']}: {exc}")
                stats["errors"].append(f"Row {stats['processed']}: {exc}")
                continue

    finally:
        if validator:
            logger.info("Closing NoDriver browser...")
            validator.quit()
        _save_company_cache(company_cache)
        logger.info(f"Company cache saved ({len(company_cache)} entries)")

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
    logger = setup_logging(module_name="dbSeeder", job_id=job_id, max_size=1)

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
            "whatsapp": contact_data[24],
            "facebook": contact_data[25],
            "instagram": contact_data[26],
            "tiktok": contact_data[27],
            "youtube": contact_data[28],
            "telegram": contact_data[29],
            "calendly": contact_data[30],
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