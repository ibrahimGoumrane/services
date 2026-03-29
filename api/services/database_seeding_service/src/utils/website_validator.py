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
        skip_website_search: bool = False
    ):
        """
        Initialize website email validator.
        
        Args:
            skip_website_search: Skip Google search for missing websites
        """
        self.skip_website_search = skip_website_search
        
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
        
        self.scraper = PageScraper(self.driver, excluded_domains=list(self.not_visiting_domains))
        self.searcher = GoogleSearcher(
            self.driver,
            excluded_domains=list(self.not_visiting_domains),
            generic_domains=list(self.generic_domains),
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
    
    def quit(self) -> None:
        """Close WebDriver and cleanup"""
        if self.driver:
            self.driver.quit()
