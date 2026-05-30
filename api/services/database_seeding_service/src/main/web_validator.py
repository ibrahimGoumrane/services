"""High-level orchestrator that exposes scraper, searcher, and email validator."""

from typing import Dict, List, Optional, Set, Tuple

from ..models import ScrapedWebData
from ..utils.email_validators import EmailValidator
from ..utils.extractor_email import extract_emails_from_text
from ..utils.extractor_geo import extract_location_city_country
from ..utils.extractor_name import extract_name_company
from ..utils.extractor_phone import extract_phones_from_text
from ..utils.extractor_social_media import extract_social_links
from ..utils.url_utils import normalize_url, validate_website_http
from .web_scraper import SeleniumDriver, PageScraper, _dedupe, extract_all_links, extract_contact_links
from .web_searcher import GoogleSearcher
from api.services.utils.logging_config import get_seeding_logger

logger = get_seeding_logger("dbSeeder.web_validator")


class WebsiteEmailValidator:
    """
    Session-scoped dependency container.

    Owns the browser lifecycle and exposes:
    - ``scraper``  – page scraping + contact extraction
    - ``searcher`` – Google SERP automation
    - ``email_validator`` – e-mail filtering
    """

    def __init__(
        self,
        skip_website_search: bool = False,
        site_timeout_seconds: int = 12,
    ):
        self.skip_website_search = skip_website_search
        self.site_timeout_seconds = site_timeout_seconds

        self.driver: Optional[SeleniumDriver] = None
        self.scraper: Optional[PageScraper] = None
        self.searcher: Optional[GoogleSearcher] = None

        # Filter sets – mutated at runtime via update_reference_filters()
        self.generic_domains: Set[str] = set()
        self.generic_users: Set[str] = set()
        self.site_builder_domains: Set[str] = set()
        self.not_visiting_domains: Set[str] = set()

        # Created eagerly so update_reference_filters() can mutate it even
        # before setup_driver() is called.
        self.email_validator = EmailValidator()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def setup_driver(self) -> None:
        """Launch the browser and wire up scraper + searcher."""
        self.driver = SeleniumDriver()
        self.driver.setup()

        self.scraper = PageScraper(
            self.driver,
            excluded_domains=list(self.not_visiting_domains),
            site_timeout_seconds=self.site_timeout_seconds,
            page_load_timeout_seconds=18,
        )
        self.searcher = GoogleSearcher(
            self.driver,
            site_timeout_seconds=self.site_timeout_seconds,
            page_load_timeout_seconds=18,
        )

        self.searcher.refresh_excluded(self.not_visiting_domains, self.generic_domains)

    def update_reference_filters(
        self,
        generic_domains: Set[str],
        generic_users: Set[str],
        site_builder_domains: Set[str],
        not_visiting_domains: Set[str],
    ) -> None:
        """Refresh runtime filter sets without recreating the browser session."""
        self.generic_domains = set(generic_domains)
        self.generic_users = set(generic_users)
        self.site_builder_domains = set(site_builder_domains)
        self.not_visiting_domains = set(not_visiting_domains)

        # Propagate to searcher
        if self.searcher:
            self.searcher.refresh_excluded(
                self.not_visiting_domains, self.generic_domains
            )

        # Propagate to scraper
        if self.scraper:
            self.scraper.excluded_domains = list(self.not_visiting_domains)

        # Mutate email_validator in-place (avoids recreating the object)
        self.email_validator.generic_domains = self.generic_domains
        self.email_validator.generic_users = self.generic_users
        self.email_validator.site_builder_domains = self.site_builder_domains
        self.email_validator.excluded_domains = self.not_visiting_domains

    # ── Conveniences ───────────────────────────────────────────────────────

    def validate_website(self, url: str) -> bool:
        """Check whether *url* is reachable and not on the exclusion list."""
        return validate_website_http(
            url, excluded_domains=list(self.not_visiting_domains)
        )

    def search_google(
        self,
        query: str,
    ) -> Tuple[List[str], Optional[Dict[str, str]]]:
        """
        Run a Google search for *query* and return URLs + optional local-panel data.

        Returns:
            (urls, local_panel) – same shape as ``GoogleSearcher.search``.
        """
        if not self.searcher:
            logger.error("Searcher not initialized. Call setup_driver() first.")
            return [], None

        return self.searcher.search(query, validate_urls=True)

    def find_contact_info_on_website(
        self,
        website_url: str,
    ) -> ScrapedWebData:
        """
        Scrape *website_url* for emails, phones, contact page, geo hints,
        and social-media links.
        Orchestrates homepage fetch → extraction → optional contact-page crawl.
        """
        empty = ScrapedWebData()
        if not self.scraper:
            logger.error("Scraper not initialized. Call setup_driver() first.")
            return empty

        if not website_url:
            return empty

        website_url = normalize_url(website_url)
        logger.info(f"[validator] find_contact_info_on_website start url={website_url}")

        try:
            # ── 1. Fetch & extract homepage ──────────────────────────────
            homepage_html = self.scraper.fetch_page(website_url)
            if not homepage_html:
                logger.info(
                    f"[validator] find_contact_info_on_website done url={website_url} "
                    f"(empty homepage)"
                )
                return empty

            all_emails = extract_emails_from_text(homepage_html)
            all_phones = extract_phones_from_text(homepage_html)
            location, city, country, zip_code = extract_location_city_country(
                homepage_html
            )
            all_social_links = extract_social_links(homepage_html)
            contact_candidates = extract_contact_links(
                website_url, homepage_html, url_ok=validate_website_http
            )
            contact_page = contact_candidates[0] if contact_candidates else None
            person_name, company_name = extract_name_company(homepage_html)
            all_page_links = extract_all_links(website_url, homepage_html)

            # ── 2. Crawl contact pages if missing information ─────────
            if (len(all_emails) > 0 and len(all_phones) > 0 
                and len(all_social_links.keys()) >= 5
                and (location or city or country)
                and person_name and company_name):
                logger.info(
                    "[validator] Home page has all required information; skipping contact-page crawl"
                )
            else:
                logger.info("[validator] Contact-page crawl triggered")
                for url in contact_candidates:
                    try:
                        html = self.scraper.fetch_page(url)
                        if not html:
                            continue

                        all_emails.extend(extract_emails_from_text(html))
                        all_phones.extend(extract_phones_from_text(html))
                        all_page_links.extend(extract_all_links(website_url, html))

                        if not (location and city and country):
                            location, city, country, zip_code = (
                                extract_location_city_country(html)
                            )

                        if not person_name or not company_name:
                            page_person, page_company = extract_name_company(html)
                            if not person_name:
                                person_name = page_person
                            if not company_name:
                                company_name = page_company

                        page_socials = extract_social_links(html)
                        for platform, urls in page_socials.items():
                            all_social_links.setdefault(platform, set()).update(urls)

                        # Quit after finding at least one email and one phone
                        if len(all_emails) > 0 and len(all_phones) > 0:
                            break
                    except Exception:
                        logger.exception(
                            f"[validator] contact page scrape failed url={url}"
                        )

            all_emails = _dedupe(all_emails)
            all_phones = _dedupe(all_phones)
            all_page_links = _dedupe(all_page_links)

            logger.info(
                f"[validator] find_contact_info_on_website done url={website_url} "
                f"emails={len(all_emails)} phones={len(all_phones)} "
                f"contact_page={contact_page!r} location={location!r} "
                f"city={city!r} country={country!r} zip_code={zip_code!r} "
                f"socials={list(all_social_links.keys())} "
                f"page_links={len(all_page_links)} "
                f"person_name={person_name!r} company_name={company_name!r}"
            )
            return ScrapedWebData(
                emails=all_emails,
                phones=all_phones,
                contact_page=contact_page,
                location=location,
                city=city,
                country=country,
                zip_code=zip_code,
                social_links=all_social_links,
                person_name=person_name,
                company_name=company_name,
                all_urls=all_page_links,
            )

        except Exception as exc:
            import traceback
            logger.error(f"[validator] Error finding contact info: {exc} | {type(exc).__name__}", exc_info=True)
            traceback.print_exc()
            return empty

    def prepare_next_batch(self) -> None:
        """Close popup tabs and create a clean tab for the next batch cycle."""
        if not self.driver:
            return
        try:
            self.driver.cleanup_tabs_for_next_batch()
            logger.debug("prepare_next_batch succeeded")
        except Exception as exc:
            logger.debug(f"prepare_next_batch failed: {exc}")
            raise

    def restart_browser(self, reason: str = "manual") -> None:
        """Restart the underlying browser."""
        if not self.driver:
            return
        try:
            self.driver.restart(reason=reason)
            logger.debug(f"restart_browser succeeded (reason={reason})")
        except Exception as exc:
            logger.debug(f"restart_browser failed (reason={reason}): {exc}")
            raise

    def quit(self) -> None:
        """Close the browser and release resources."""
        if self.driver:
            self.driver.quit()
