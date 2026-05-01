"""High-level website and email validation orchestrator"""

from typing import List, Optional, Tuple

from ..utils.email_validators import EmailValidator
from api.services.utils.logging_config import get_logger
from ..utils.url_utils import validate_website_http
from .web_scraper import NoDriverDriver, PageScraper
from .web_searcher import GoogleSearcher

logger = get_logger("dbSeeder.web_validator")


class WebsiteEmailValidator:
    """
    Main orchestrator for website validation and email extraction.

    Composes multiple utilities:
    - NoDriverDriver: Browser management
    - PageScraper: Page scraping and email finding
    - GoogleSearcher: Google search for websites
    - EmailValidator: Email filtering
    """

    def __init__(
        self,
        skip_website_search: bool = False,
        site_timeout_seconds: int = 30,
    ):
        """
        Initialize website email validator.

        Args:
            skip_website_search: Skip Google search for missing websites
        """
        self.skip_website_search = skip_website_search
        self.site_timeout_seconds = site_timeout_seconds

        self.driver: Optional[NoDriverDriver] = None
        self.scraper: Optional[PageScraper] = None
        self.searcher: Optional[GoogleSearcher] = None
        self.email_validator: Optional[EmailValidator] = None

        # Load filter lists
        self.generic_domains = set()
        self.generic_users = set()
        self.not_visiting_domains = set()
        self.site_builder_domains = set()

    def setup_driver(self) -> None:
        """Initialize NoDriver browser"""
        self.driver = NoDriverDriver()
        self.driver.setup()

        self.scraper = PageScraper(
            self.driver,
            excluded_domains=list(self.not_visiting_domains),
            site_timeout_seconds=self.site_timeout_seconds,
        )
        self.searcher = GoogleSearcher(
            self.driver,
            excluded_domains=list(self.not_visiting_domains),
            generic_domains=list(self.generic_domains),
            site_timeout_seconds=self.site_timeout_seconds,
        )

    def update_reference_filters(
        self,
        generic_domains: set[str],
        generic_users: set[str],
        site_builder_domains: set[str],
        not_visiting_domains: set[str],
    ) -> None:
        """Refresh runtime filter sets without recreating browser session."""
        self.generic_domains = set(generic_domains)
        self.generic_users = set(generic_users)
        self.site_builder_domains = set(site_builder_domains)
        self.not_visiting_domains = set(not_visiting_domains)

        if self.searcher:
            self.searcher.excluded_domains = list(self.not_visiting_domains)
            self.searcher.generic_domains = list(self.generic_domains)

        if self.scraper:
            self.scraper.excluded_domains = list(self.not_visiting_domains)

        self.email_validator = EmailValidator(
            generic_domains=list(self.generic_domains),
            generic_users=list(self.generic_users),
            site_builder_domains=list(self.site_builder_domains),
            excluded_domains=list(self.not_visiting_domains),
        )

    def validate_website(self, url: str) -> bool:
        """
        Check if a website is accessible via HTTP.

        Args:
            url: Website URL

        Returns:
            True if website is accessible, False otherwise
        """
        return validate_website_http(
            url,
            excluded_domains=list(self.not_visiting_domains),
        )

    def find_contact_info_on_website(
        self,
        website_url: str,
    ) -> Tuple[
        List[str], List[str], Optional[str], Optional[str], Optional[str], Optional[str]
    ]:
        """Find emails, phones, contact page, and geo hints in one scraper call."""
        if not self.scraper:
            logger.error("Scraper not initialized. Call setup_driver() first.")
            return [], [], None, None, None, None

        logger.info(
            f"[validator] find_contact_info_on_website start website_url={website_url}"
        )
        result = self.scraper.find_contact_info_on_website(website_url)
        logger.info(
            f"[validator] find_contact_info_on_website done website_url={website_url} "
            f"emails={len(result[0])} phones={len(result[1])} contact_page={result[2]!r} "
            f"location={result[3]!r} city={result[4]!r} country={result[5]!r}"
        )
        return result



    def prepare_next_batch(self) -> None:
        """Close popup tabs and create a clean tab for the next batch cycle."""
        if self.driver:
            try:
                self.driver.cleanup_tabs_for_next_batch()
                logger.debug("prepare_next_batch succeeded")
            except Exception as exc:
                logger.debug(f"prepare_next_batch failed: {exc}")
                raise

    def restart_browser(self, reason: str = "manual") -> None:
        if not self.driver:
            return
        try:
            self.driver.restart(reason=reason)
            logger.debug(f"restart_browser succeeded (reason={reason})")
        except Exception as exc:
            logger.debug(f"restart_browser failed (reason={reason}): {exc}")
            raise

    def quit(self) -> None:
        """Close WebDriver and cleanup"""
        if self.driver:
            self.driver.quit()
