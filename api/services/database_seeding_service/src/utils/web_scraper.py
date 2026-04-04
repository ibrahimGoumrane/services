"""NoDriver browser management and page scraping utilities"""

import asyncio
import logging
import os
import random
from typing import Optional, List
from urllib.parse import urljoin
from dotenv import load_dotenv

load_dotenv()
import nodriver as uc


from .url_utils import normalize_url, validate_website_http
from .email_extractors import extract_emails_from_text


logger = logging.getLogger(__name__)

# User agents for rotation
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0',
]


class NoDriverDriver:
    """Manages nodriver browser lifecycle and anti-bot-friendly defaults"""
    
    def __init__(self):
        """Initialize nodriver manager"""
        self.browser = None
        self.tab = None
        self._loop = None
        self._restart_epoch = 0
        self._last_health_restart_epoch = 0

    def _run(self, coro, timeout_seconds: Optional[float] = None):
        """Execute nodriver coroutine in the dedicated event loop."""
        if not self._loop:
            raise RuntimeError("Driver loop not initialized. Call setup() first.")
        if timeout_seconds and timeout_seconds > 0:
            return self._loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout_seconds))
        return self._loop.run_until_complete(coro)

    @property
    def current_url(self) -> str:
        """Return current tab URL when available."""
        if not self.tab or not getattr(self.tab, "target", None):
            return ""
        return str(getattr(self.tab.target, "url", "") or "")
    
    def setup(self) -> None:
        """Initialize nodriver browser and a reusable tab."""
        logger.info("Setting up NoDriver browser...")
        user_agent = random.choice(USER_AGENTS)
        logger.info(f"Using user agent: {user_agent[:50]}...")

        headless = os.getenv("NODRIVER_HEADLESS", "false").lower() in {"1", "true", "yes"}
        browser_args = [
            f"--user-agent={user_agent}",
            "--disable-dev-shm-usage",
        ]

        try:
            self._loop = uc.loop()
            self.browser = self._run(uc.start(headless=headless, browser_args=browser_args))
            self.tab = self._run(self.browser.get("about:blank"))

            width = random.randint(1366, 1920)
            height = random.randint(768, 1080)
            try:
                self._run(self.tab.set_window_size(width=width, height=height))
            except Exception:
                pass

            logger.info("✅ NoDriver browser initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize NoDriver browser: {e}")
            raise
    
    def restart(self, reason: str = "manual") -> None:
        """Restart the browser if unresponsive."""
        logger.warning("⚠️ Restarting NoDriver browser...")
        try:
            self.quit()
        except Exception:
            pass
        self.setup()
        self._restart_epoch += 1
        if reason == "health":
            self._last_health_restart_epoch = self._restart_epoch

    @property
    def restart_epoch(self) -> int:
        return self._restart_epoch

    def had_health_restart_since(self, since_epoch: int) -> bool:
        return self._last_health_restart_epoch > since_epoch

    def get(self, url: str, timeout_seconds: Optional[float] = None) -> None:
        """Navigate the current tab to the given URL."""
        if not self.tab:
            raise RuntimeError("Tab not initialized. Call setup() first.")
        self._run(self.tab.get(url), timeout_seconds=timeout_seconds)

    def sleep(self, seconds: float) -> None:
        """Async-aware sleep on the active tab."""
        if not self.tab:
            return
        self._run(self.tab.sleep(seconds))

    def get_content(self, timeout_seconds: Optional[float] = None) -> str:
        """Fetch current page HTML."""
        if not self.tab:
            return ""
        content = self._run(self.tab.get_content(), timeout_seconds=timeout_seconds)
        return str(content or "")

    def evaluate(self, expression: str, return_by_value: bool = True):
        """Evaluate JavaScript expression in current tab."""
        if not self.tab:
            return None
        return self._run(self.tab.evaluate(expression, return_by_value=return_by_value))

    def _list_tabs(self) -> List:
        if not self.browser:
            return []

        tabs = getattr(self.browser, "tabs", None)
        if isinstance(tabs, dict):
            return [tab for tab in tabs.values() if tab is not None]
        if isinstance(tabs, list):
            return [tab for tab in tabs if tab is not None]
        if tabs:
            return [tabs]
        return [self.tab] if self.tab else []

    def cleanup_tabs_for_next_batch(self) -> None:
        """Close popups/new tabs and reset to a clean working tab between batches."""
        if not self.browser:
            return

        tabs = self._list_tabs()
        primary_tab = self.tab or (tabs[0] if tabs else None)
        closed_tabs = 0
        close_errors = 0

        for tab in tabs:
            if tab is None or tab is primary_tab:
                continue
            try:
                self._run(tab.close())
                closed_tabs += 1
            except Exception:
                close_errors += 1
                continue

        try:
            self.tab = self._run(self.browser.get("about:blank"))
            logger.debug(f"Batch tab reset succeeded (closed_tabs={closed_tabs})")
        except Exception:
            self.tab = primary_tab
            try:
                self.get("about:blank")
            except Exception:
                pass

        if close_errors > 0:
            logger.debug(f"Batch tab cleanup had close errors (errors={close_errors})")
    
    def quit(self) -> None:
        """Close the browser and clean up loop resources."""
        try:
            if self.browser:
                self.browser.stop()
                logger.info("NoDriver browser closed")
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")
        finally:
            self.browser = None
            self.tab = None
            if self._loop:
                try:
                    self._loop.close()
                except Exception:
                    pass
                self._loop = None


class PageScraper:
    """Handles web page scraping with nodriver"""
    
    def __init__(
        self,
        driver: NoDriverDriver,
        excluded_domains: Optional[List[str]] = None,
        prevalidate_http: bool = False,
        site_timeout_seconds: int = 30,
    ):
        """
        Initialize page scraper.
        
        Args:
            driver: NoDriverDriver instance
            excluded_domains: Domains blocked from browsing
            prevalidate_http: When True, run HTTP validation before browser navigation.
                Keep False to avoid excessive extra HTTP requests.
        """
        self.driver = driver
        self.excluded_domains = excluded_domains or []
        self.prevalidate_http = prevalidate_http
        self.site_timeout_seconds = site_timeout_seconds
    
    def accept_cookies(self) -> bool:
        """Try to accept cookie consent banners"""
        try:
            script = """
(() => {
  const keywords = ['accept all', 'accept cookies', 'i accept', 'i agree', 'agree', 'got it', 'ok'];
  const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
  for (const node of nodes) {
    const txt = (node.innerText || node.textContent || '').toLowerCase().trim();
    if (!txt) continue;
    if (keywords.some(k => txt.includes(k))) {
      node.click();
      return true;
    }
  }
  return false;
})();
"""
            clicked = self.driver.evaluate(script, return_by_value=True)
            if bool(clicked):
                logger.info("✓ Accepted cookie consent")
                self.driver.sleep(1.0)
                return True
        
        except Exception as e:
            logger.debug(f"No cookie banner found: {e}")
        
        return False
    
    def find_emails_on_page(self, url: str) -> Optional[List[str]]:
        """
        Find emails on a specific page using nodriver.
        
        Args:
            url: URL to scrape
        
        Returns:
            List of emails found, or None
        """
        try:
            if self.prevalidate_http and not validate_website_http(
                url,
                timeout=3,
                excluded_domains=self.excluded_domains,
            ):
                logger.warning(f"⏭️ Skipping URL rejected by validator: {url}")
                return None
            
            logger.info(f"Searching for email on: {url}")
            self.driver.get(url, timeout_seconds=self.site_timeout_seconds)
            self.driver.sleep(1.0)

            current_url = self.driver.current_url
            if not validate_website_http(
                current_url,
                timeout=3,
                excluded_domains=self.excluded_domains,
            ):
                logger.warning(f"⏭️ Skipping page URL rejected by validator: {current_url}")
                return None

            self.accept_cookies()
            self.driver.sleep(1.0)

            page_text = self.driver.get_content(timeout_seconds=self.site_timeout_seconds)
            emails = extract_emails_from_text(page_text)
            
            if emails:
                logger.info(f"✓ Found {len(emails)} valid email(s): {emails}")
                return emails
            
            return None

        except Exception as e:
            logger.error(f"Error finding email on page: {e}")
            return None
    
    def find_contact_page(self, base_url: str) -> Optional[str]:
        """
        Try to find a contact page on the website.
        
        Args:
            base_url: Base website URL
        
        Returns:
            Contact page URL if found, None otherwise
        """
        common_paths = [
            '/contact',
            '/contact-us',
            '/contactus',
            '/about/contact',
            '/contact.html',
            '/contact.php'
        ]
        
        base_url = normalize_url(base_url)
        
        for path in common_paths:
            contact_url = urljoin(base_url, path)
            
            try:
                if validate_website_http(
                    contact_url,
                    timeout=2,
                    excluded_domains=self.excluded_domains,
                ):
                    logger.info(f"✓ Found contact page: {contact_url}")
                    return contact_url
            except Exception:
                continue
        
        logger.debug("No contact page found")
        return None
    
    def find_emails_on_website(self, website_url: str) -> Optional[List[str]]:
        """
        Search for emails on a website (main page and contact page).
        
        Args:
            website_url: Website URL to search
        
        Returns:
            List of unique emails found, or None
        """
        if not website_url:
            return None
        
        website_url = normalize_url(website_url)

        all_emails = []
        
        # Try main page
        emails = self.find_emails_on_page(website_url)
        if emails:
            all_emails.extend(emails)
            logger.info(f"✓ Found {len(emails)} email(s) on main page")
        else:
            logger.debug("No emails found on main page")
        
        # Try contact page if no emails on main page
        if not all_emails:
            logger.debug("Checking contact page...")
            contact_url = self.find_contact_page(website_url)
            if contact_url:
                emails = self.find_emails_on_page(contact_url)
                if emails:
                    all_emails.extend(emails)
                    logger.info(f"✓ Found {len(emails)} email(s) on contact page")
        
        # Deduplicate
        if all_emails:
            seen = set()
            unique_emails = []
            for email in all_emails:
                email_lower = email.lower()
                if email_lower not in seen:
                    seen.add(email_lower)
                    unique_emails.append(email)
            
            try:
                self.driver.get("about:blank")
            except Exception:
                pass
            
            return unique_emails
        
        try:
            self.driver.get("about:blank")
        except Exception:
            pass
        
        return None
