"""High-level website and email validation orchestrator"""

from typing import List, Optional, Tuple
import logging

from .web_scraper import NoDriverDriver, PageScraper
from .web_searcher import GoogleSearcher
from .email_validators import EmailValidator
from .url_utils import validate_website_http

from . import contact_repository


logger = logging.getLogger(__name__)


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
    
    def setup_email_filters(self) -> None:
        """Load email filter lists from database"""
        logger.info("Loading email filters from database...")
        
        try:
            self.generic_domains = set(contact_repository.get_all_generic_domains())
            self.generic_users = set(contact_repository.get_all_generic_users())
            self.not_visiting_domains = set(contact_repository.get_all_not_visiting_domains())
            self.site_builder_domains = set(contact_repository.get_all_site_builder_domains())
            
            logger.info(f"Loaded {len(self.generic_domains)} generic domains")
            logger.info(f"Loaded {len(self.generic_users)} generic users")
            logger.info(f"Loaded {len(self.not_visiting_domains)} not visiting domains")
            logger.info(f"Loaded {len(self.site_builder_domains)} site builder domains")
            
            # Update the searcher's excluded domains now that they are loaded
            # (setup_driver() creates the searcher before filters are available)
            if self.searcher:
                self.searcher.excluded_domains = list(self.not_visiting_domains)
                self.searcher.generic_domains = list(self.generic_domains)
                logger.info(f"Updated GoogleSearcher with {len(self.not_visiting_domains)} excluded domains")

            if self.scraper:
                self.scraper.excluded_domains = list(self.not_visiting_domains)
            
            # Create email validator with filters
            self.email_validator = EmailValidator(
                generic_domains=list(self.generic_domains),
                generic_users=list(self.generic_users),
                site_builder_domains=list(self.site_builder_domains),
                excluded_domains=list(self.not_visiting_domains)
            )
            
            logger.info("✅ Email filters loaded successfully")
        
        except Exception as e:
            logger.error(f"Failed to load email filters: {e}")
            raise
    
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
    
    def find_email_on_website(self, website_url: str) -> Optional[List[str]]:
        """
        Find emails on a website using NoDriver scraping.
        
        Args:
            website_url: Website URL to search
        
        Returns:
            List of emails found, or None
        """
        if not self.scraper:
            logger.error("Scraper not initialized. Call setup_driver() first.")
            return None
        
        return self.scraper.find_emails_on_website(website_url)
    
    def find_contact_page(self, base_url: str) -> Optional[str]:
        """
        Try to find a contact page on the website.
        
        Args:
            base_url: Base website URL
        
        Returns:
            Contact page URL if found, None otherwise
        """
        if not self.scraper:
            logger.error("Scraper not initialized. Call setup_driver() first.")
            return None
        
        return self.scraper.find_contact_page(base_url)
    
    def google_search_business(
        self,
        business_name: str,
        location: Optional[str] = None,
        max_retries: int = 2
    ) -> Tuple[Optional[str], bool]:
        """
        Search Google for a business and return first valid result.
        
        Args:
            business_name: Business name to search for
            location: Optional location for more specific search
            max_retries: Number of retries on timeout
        
        Returns:
            Tuple of (url, is_valid)
        """
        if not self.searcher:
            logger.error("Searcher not initialized. Call setup_driver() first.")
            return None, False
        
        return self.searcher.search(business_name, location, max_retries)
    
    def filter_emails(self, emails: List[str]) -> List[str]:
        """
        Filter emails against allowlists.
        
        Args:
            emails: List of emails to filter
        
        Returns:
            List of valid emails
        """
        if not self.email_validator:
            logger.error("Email validator not initialized. Call setup_email_filters() first.")
            return []
        
        return self.email_validator.filter_emails(emails)

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

    def restart_epoch(self) -> int:
        if not self.driver:
            return 0
        return self.driver.restart_epoch

    def had_health_restart_since(self, since_epoch: int) -> bool:
        if not self.driver:
            return False
        return self.driver.had_health_restart_since(since_epoch)
    
    def quit(self) -> None:
        """Close WebDriver and cleanup"""
        if self.driver:
            self.driver.quit()
