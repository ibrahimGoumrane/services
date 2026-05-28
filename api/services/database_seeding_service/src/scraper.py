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


def _merge_scraped_data(base: ScrapedWebData, incoming: ScrapedWebData) -> ScrapedWebData:
    """Merge two ScrapedWebData, with base values taking priority except for collections which are deduplicated."""
    social_links: Dict[str, Set[str]] = {}
    for d in (base.social_links, incoming.social_links):
        for platform, urls in d.items():
            social_links.setdefault(platform, set()).update(urls)

    return ScrapedWebData(
        emails=list(dict.fromkeys([*base.emails, *incoming.emails])),
        phones=list(dict.fromkeys([*base.phones, *incoming.phones])),
        all_urls=list(dict.fromkeys([*base.all_urls, *incoming.all_urls])),
        contact_page=base.contact_page or incoming.contact_page,
        location=base.location or incoming.location,
        city=base.city or incoming.city,
        country=base.country or incoming.country,
        zip_code=base.zip_code or incoming.zip_code,
        social_links=social_links,
        person_name=base.person_name or incoming.person_name,
        company_name=base.company_name or incoming.company_name,
    )


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


def _build_urls_field(
    all_urls: List[str],
    website: Optional[str],
    contact_form_url: Optional[str],
    social_links: Dict[str, Any],
) -> Optional[str]:
    """Filter out main site, contact form, and social URLs; return comma-separated string."""
    if not all_urls:
        return None
    main_site = (website or "").rstrip("/")
    contact = (contact_form_url or "").rstrip("/")
    social_urls = set()
    for urls in social_links.values():
        social_urls.update(urls)

    filtered = []
    for url in all_urls:
        url_stripped = url.rstrip("/")
        if url_stripped == main_site or url_stripped == contact:
            continue
        if url_stripped in social_urls:
            continue
        filtered.append(url)

    return ",".join(filtered) if filtered else None


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


_SCRAPABLE_COLUMN_INDICES = frozenset([
    0,   # email
    1,   # fullname (from scraped.person_name)
    6,   # phone
    9,   # name (from scraped.company_name)
    10,  # address (from scraped.location)
    11,  # city
    13,  # country
    14,  # urlcontactform
    15,  # linkedin
    24,  # whatsapp
    25,  # facebook
    26,  # instagram
    27,  # tiktok
    28,  # youtube
    29,  # telegram
    30,  # calendly
    31,  # twitter
    32,  # signal
    33,  # urls
])


def _is_db_record_scrape_complete(db_row: Tuple) -> bool:
    return all(
        db_row[i] is not None and str(db_row[i]).strip() != ""
        for i in _SCRAPABLE_COLUMN_INDICES
        if i < len(db_row)
    )


# ---------------------------------------------------------------------------
# Website resolution (shared between person and company paths)
# ---------------------------------------------------------------------------

def _resolve_all_websites(
    csv: CsvRow,
    validator: WebsiteEmailValidator,
    type: Literal["person", "company"] = "person",
) -> Tuple[List[str], Optional[Dict[str, str]], Optional[Dict[str, Set[str]]]]:
    """
    Like ``_resolve_website`` but returns **all** valid candidate URLs
    from Google search that are not already in the database.

    Back-fills *csv* from DB when a domain is already known.

    Returns:
        (urls_to_scrape, local_panel, search_social_links)
        urls_to_scrape - valid URLs not in DB (empty list if none found)
        local_panel    - Google My Business data or None
        search_social_links - social URLs from all Google candidates
    """
    website = csv.website

    if website:
        if not validator.validate_website(website):
            logger.info(f"Website rejected by validator: {website}")
            return [], None, None

        exact_match = contact_repository.get_contact_by_exact_url(website)
        if exact_match:
            logger.info("Exact URL already scraped; reusing stored fields")
            _populate_from_db(exact_match, csv)
            return [], None, None

        existing = contact_repository.get_contact_by_domain(website)
        if existing:
            logger.info("Website domain already in DB; reusing stored fields")
            _populate_from_db(existing, csv)
            if _is_db_record_scrape_complete(existing):
                logger.info("Existing DB record is complete; skipping scrape")
                return [], None, None
            logger.info("Existing DB record incomplete; will scrape to fill gaps")
            return [website], None, None
        return [website], None, None

    search_social_links: Optional[Dict[str, Set[str]]] = None
    urls_to_scrape: List[str] = []
    local_panel: Optional[Dict[str, str]] = None

    if not validator.skip_website_search and csv.name:
        search_query = ""
        if type == "person":
            if csv.fullname:
                search_query = f"{csv.fullname} {csv.name}"
            else:
                search_query = f"{csv.fname} {csv.lname} {csv.name}"
        else:
            search_query = f"{csv.name} {csv.location}"
        logger.info(f"Multi-website search: '{search_query}'")
        urls, local_panel = validator.search_google(search_query)

        candidates: List[str] = []
        if local_panel and local_panel.get("website"):
            candidates.append(local_panel["website"])
        candidates.extend(urls)

        if candidates:
            search_social_links = extract_social_links_from_urls(candidates)
            if search_social_links:
                logger.info(
                    f"Social links found in search candidates: "
                    f"{', '.join(f'{k}={len(v)}' for k, v in search_social_links.items())}"
                )

        for candidate in candidates:
            if not validator.validate_website(candidate):
                continue
            logger.info(f"Website search candidate valid: '{candidate}'")
            existing = contact_repository.get_contact_by_domain(candidate)
            if existing:
                logger.info(f"Website domain already in DB; reusing stored fields: '{candidate}'")
                _populate_from_db(existing, csv)
                continue
            urls_to_scrape.append(candidate)

    return urls_to_scrape, local_panel, search_social_links

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
            person.linkedin = ",".join(social_links["linkedin"])
        if not person.whatsapp and social_links.get("whatsapp"):
            person.whatsapp = ",".join(social_links["whatsapp"])
        if not person.facebook and social_links.get("facebook"):
            person.facebook = ",".join(social_links["facebook"])
        if not person.instagram and social_links.get("instagram"):
            person.instagram = ",".join(social_links["instagram"])
        if not person.tiktok and social_links.get("tiktok"):
            person.tiktok = ",".join(social_links["tiktok"])
        if not person.youtube and social_links.get("youtube"):
            person.youtube = ",".join(social_links["youtube"])
        if not person.telegram and social_links.get("telegram"):
            person.telegram = ",".join(social_links["telegram"])
        if not person.calendly and social_links.get("calendly"):
            person.calendly = ",".join(social_links["calendly"])
        if not person.twitter and social_links.get("twitter"):
            person.twitter = ",".join(social_links["twitter"])
        if not person.signal and social_links.get("signal"):
            person.signal = ",".join(social_links["signal"])

    # --- Non-social page URLs ----------------------------------------------
    person.urls = _build_urls_field(
        scraped.all_urls, person.website, person.contact_form_url, scraped.social_links or {}
    )

    # --- Names extracted from website text ---------------------------------
    # scraped.person_name maps to fullname, scraped.company_name maps to name
    if not person.fullname and scraped.person_name:
        person.fullname = scraped.person_name
    if not person.name and scraped.company_name:
        person.name = scraped.company_name

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
            company.linkedin = ",".join(social_links["linkedin"])
        if not company.whatsapp and social_links.get("whatsapp"):
            company.whatsapp = ",".join(social_links["whatsapp"])
        if not company.facebook and social_links.get("facebook"):
            company.facebook = ",".join(social_links["facebook"])
        if not company.instagram and social_links.get("instagram"):
            company.instagram = ",".join(social_links["instagram"])
        if not company.tiktok and social_links.get("tiktok"):
            company.tiktok = ",".join(social_links["tiktok"])
        if not company.youtube and social_links.get("youtube"):
            company.youtube = ",".join(social_links["youtube"])
        if not company.telegram and social_links.get("telegram"):
            company.telegram = ",".join(social_links["telegram"])
        if not company.calendly and social_links.get("calendly"):
            company.calendly = ",".join(social_links["calendly"])
        if not company.twitter and social_links.get("twitter"):
            company.twitter = ",".join(social_links["twitter"])
        if not company.signal and social_links.get("signal"):
            company.signal = ",".join(social_links["signal"])

    # --- Non-social page URLs ----------------------------------------------
    company.urls = _build_urls_field(
        scraped.all_urls, company.website, company.contact_form_url, scraped.social_links or {}
    )

    # --- Names extracted from website text ---------------------------------
    if not company.name and scraped.company_name:
        company.name = scraped.company_name

    # --- Synthetic e-mail fallback ----------------------------------------
    if not company.email or "@" not in company.email:
        company_local = _slugify_for_email(company.name or "") or "company"
        company.email = f"{company_local}@{domain}"
        logger.info(f"Company synthetic fallback email: '{company.email}'")

    # --- Extra emails → comment -------------------------------------------
    main_email = company.email if company.email and "@" in company.email else None
    extra_emails = [e for e in scraped.emails if e != main_email]
    if extra_emails:
        company.comment = ",".join(extra_emails)

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
    validator: WebsiteEmailValidator,
    generic_domains: Set[str],
    generic_users: Set[str],
    generic_mx: Set[str],
    site_builder_domains: Set[str],
    mx_cache: Dict,
    new_mx_records: List,
    scraped_cache: Dict[str, ScrapedWebData],
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
    merged_scraped_company: ScrapedWebData = ScrapedWebData()
    from_cache = False
    company_search_social_links: Optional[Dict[str, Set[str]]] = None

    if company_cache is not None and cache_key and cache_key in company_cache:
        cached_entry = company_cache[cache_key]
        if cached_entry is None:
            logger.debug(f"Company cache hit (no website found): '{csv.name}'")
            return enriched, company_linkedin
        logger.info(f"Company cache hit for '{csv.name}' -> '{cached_entry.get('websites', ['?'])[0]}'")
        website_company = cached_entry["websites"][0] if cached_entry["websites"] else ""
        company_local_panel = cached_entry.get("local_panel")
        merged_scraped_company = ScrapedWebData.from_dict(cached_entry.get("scraped", {}))
        from_cache = True
    else:
        company_urls, company_local_panel, company_search_social_links = _resolve_all_websites(
            csv, validator, type="company"
        )

        if not company_urls:
            if company_cache is not None and cache_key:
                company_cache[cache_key] = None
                logger.debug(f"Company cache miss (no website found): '{csv.name}'")
            return enriched, company_linkedin

        website_company = company_urls[0] if company_urls else ""

        # Scrape all company URLs, deduplicating via scraped_cache
        for url in company_urls:
            if url in scraped_cache:
                scraped = scraped_cache[url]
            else:
                scraped = _scrape_website(url, validator)
                scraped_cache[url] = scraped
            merged_scraped_company = _merge_scraped_data(merged_scraped_company, scraped)

        # Merge social links discovered in Google search candidates
        if company_search_social_links:
            if merged_scraped_company.social_links:
                for platform, urls in company_search_social_links.items():
                    merged_scraped_company.social_links.setdefault(platform, set()).update(urls)
            else:
                merged_scraped_company.social_links = company_search_social_links

    if company_cache is not None and cache_key and not from_cache:
        company_cache[cache_key] = {
            "websites": [website_company],
            "local_panel": company_local_panel,
            "scraped": merged_scraped_company.to_dict(),
        }
        logger.debug(f"Company cache stored: '{csv.name}'")

    company = _enrich_company(
        csv=csv,
        website=website_company,
        scraped=merged_scraped_company,
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
        logger.info(f"Company contact staged for '{website_company}'")
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
    config: ProcessingConfig,
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
    # 4. Shared scraped cache (URL -> ScrapedWebData) for cross-path dedup
    # ------------------------------------------------------------------
    scraped_cache: Dict[str, ScrapedWebData] = {}

    # ------------------------------------------------------------------
    # 5. Person enrichment (multi-URL when Google search is involved)
    # ------------------------------------------------------------------
    person_search_enabled = config.enable_person_search and config.enable_web_scraping

    if person_search_enabled:
        person_urls, person_local_panel, search_social_links = _resolve_all_websites(
            csv, validator, type="person"
        )

        person_scraped = ScrapedWebData()
        if person_urls:
            for url in person_urls:
                scraped = _scrape_website(url, validator)
                scraped_cache[url] = scraped
                person_scraped = _merge_scraped_data(person_scraped, scraped)

        # Merge social links discovered in Google search candidates
        if search_social_links:
            if person_scraped.social_links:
                for platform, urls in search_social_links.items():
                    person_scraped.social_links.setdefault(platform, set()).update(urls)
            else:
                person_scraped.social_links = search_social_links

        person_website = person_urls[0] if person_urls else ""
        person = _enrich_person(
            csv=csv,
            website=person_website,
            scraped=person_scraped,
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
        # Store extra scraped emails in comment
        main_email = person.email if person.email and "@" in person.email else None
        extra_emails = [e for e in person_scraped.emails if e != main_email]
        if extra_emails:
            person.comment = ",".join(extra_emails)
    else:
        # Person without web enrichment (still runs MX/classification/synthetic email)
        person = _enrich_person(
            csv=csv,
            website="",
            scraped=ScrapedWebData(),
            validator=validator,
            generic_domains=generic_domains,
            generic_users=generic_users,
            generic_mx=generic_mx,
            site_builder_domains=site_builder_domains,
            mx_cache=mx_cache,
            new_mx_records=new_mx_records,
            row_stats=row_stats,
            local_panel=None,
        )

    # ------------------------------------------------------------------
    # 6. Company enrichment (multi-URL with scraped_cache dedup)
    # ------------------------------------------------------------------
    if config.enable_company_search and config.enable_web_scraping:
        company_tuples, company_linkedin = _enrich_company_contact(
            csv=csv,
            validator=validator,
            generic_domains=generic_domains,
            generic_users=generic_users,
            generic_mx=generic_mx,
            site_builder_domains=site_builder_domains,
            mx_cache=mx_cache,
            new_mx_records=new_mx_records,
            scraped_cache=scraped_cache,
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

    logger.info("Setting up Selenium browser for web enrichment...")
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
        logger.info("Selenium browser ready")
    except Exception as exc:
        logger.error(f"Failed to setup Selenium: {exc}")
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
                    config=config,
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
            logger.info("Closing Selenium browser...")
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
    urls: List[str],
    enable_web_scraping: bool = True,
    skip_google_search: bool = False,
    enable_person_search: bool = True,
    enable_company_search: bool = True,
    sourcefile: str | None = None,
    job_id: str | None = None,
) -> Dict[str, Any]:
    """Scrape multiple URLs sequentially, save records, and return aggregated stats."""
    global logger
    logger = setup_logging(module_name="dbSeeder", job_id=job_id, max_size=1)

    total_urls = len(urls)
    stats: Dict[str, Any] = {
        "total_rows": total_urls,
        "processed": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "emails_found": 0,
        "websites_found": total_urls,
        "mx_failed": 0,
        "rows_skipped_no_required_field": 0,
        "rows_skipped_invalid_mx": 0,
        "rows_skipped_no_email_found": 0,
        "errors": [],
        "contact_form_discoveries": 0,
        "synthetic_emails_created": 0,
    }

    logger.info(
        "SEED_MULTI_URL_START "
        f"job_id={job_id or 'none'} "
        f"urls={total_urls} "
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

        for idx, url in enumerate(urls, start=1):
            logger.info(f"Processing URL {idx}/{total_urls}: '{url}'")

            contact_data, row_stats, extra_contacts = _process_contact_row(
                row={"url": (url or "").strip()},
                generic_domains=generic_domains,
                generic_users=generic_users,
                generic_mx=generic_mx,
                site_builder_domains=site_builder_domains,
                sourcefile=sourcefile or (url or "multi-url"),
                csv_mapping=CsvMapping(url="url"),
                default_values={},
                mx_cache={},
                new_mx_records=[],
                validator=validator,
                config=ProcessingConfig(
                    csv_file_path="__single_url__",
                    csv_mapping=CsvMapping(url="url"),
                    enable_web_scraping=enable_web_scraping,
                    skip_google_search=skip_google_search,
                    enable_person_search=enable_person_search,
                    enable_company_search=False,
                    sourcefile=sourcefile,
                ),
            )

            stats["processed"] += 1
            _merge_row_stats(stats, row_stats)

            if contact_data is None:
                stats["skipped"] += 1
                stats["mx_failed"] += 1
                stats["rows_skipped_no_email_found"] += 1
                continue

            if extra_contacts:
                inserted, updated = contact_repository.batch_create_contacts([*extra_contacts, contact_data])
            else:
                inserted, updated = contact_repository.batch_create_contacts([contact_data])

            stats["inserted"] += inserted
            stats["updated"] += updated

            if contact_data[0] and "@" in str(contact_data[0]):
                stats["emails_found"] += 1

        return stats
    finally:
        if validator:
            validator.quit()
        elapsed = time.time() - start_time
        logger.info(
            "SEED_MULTI_URL_END "
            f"processed={stats['processed']} "
            f"total={stats['total_rows']} "
            f"inserted={stats['inserted']} "
            f"updated={stats['updated']} "
            f"errors={len(stats['errors'])} "
            f"elapsed={data_transformers.format_eta(elapsed)}"
        )
        flush_buffered_log_handlers(logger)