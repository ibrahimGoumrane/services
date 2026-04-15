"""NoDriver browser management and page scraping utilities"""

import asyncio
import logging
import os
import random
import re
from typing import Optional, List, Tuple
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import geograpy


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

PHONE_REGEX = re.compile(r"(?<!\d)(?!\d{4}[-/]\d{2}[-/]\d{2}\b)(?:\+?\d[\d\s().\-/]{6,}\d)(?!\d)")


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

            # Warm up the browser session on Google before normal crawling.
            try:
                self.tab = self._run(self.browser.get("https://www.google.com"))
                self._run(self.tab.sleep(random.uniform(1.0, 1.6)))
            except Exception as warmup_exc:
                logger.debug(f"Google warmup step failed: {warmup_exc}")

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
    


    def _dedupe_preserve_order(self, values: List[str]) -> List[str]:
        seen = set()
        unique_values: List[str] = []
        for value in values:
            key = (value or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique_values.append((value or "").strip())
        return unique_values

    def _extract_phone_numbers_from_text(self, text: str) -> List[str]:
        if not text:
            return []

        candidates = PHONE_REGEX.findall(text)
        normalized: List[str] = []
        for candidate in candidates:
            raw = (candidate or "").strip()
            if not raw:
                continue

            compact = re.sub(r"[^\d+]", "", raw)
            digits = re.sub(r"\D", "", compact)
            if len(digits) < 7 or len(digits) > 15:
                continue

            if compact.startswith("+"):
                normalized.append(f"+{digits}")
            else:
                normalized.append(digits)

        return self._dedupe_preserve_order(normalized)

    def _extract_visible_text(self, html: str) -> str:
        """Extract user-visible text only, ignoring script/style/non-content nodes."""
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        for node in soup(["script", "style", "noscript", "svg"]):
            node.decompose()

        return soup.get_text(" ", strip=True)

    def _extract_contact_links_from_home_html(self, base_url: str, html: str) -> List[str]:
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        candidates: List[str] = []

        for node in soup.select("a[href]"):
            href = (node.get("href") or "").strip()
            if not href:
                continue

            href_lower = href.lower()
            if "contact" not in href_lower:
                continue

            if href_lower.startswith("mailto:") or href_lower.startswith("tel:"):
                continue

            if href_lower.startswith("javascript:") or href.startswith("#"):
                continue

            absolute_url = urljoin(base_url, href)
            parsed = urlparse(absolute_url)
            if parsed.scheme not in {"http", "https"}:
                continue

            normalized = normalize_url(absolute_url)
            if normalized:
                candidates.append(normalized)

        return self._dedupe_preserve_order(candidates)

    def _extract_geo_from_visible_text(self, text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract (location, city, country) from visible text using geograpy3 when available."""
        if not text or geograpy is None:
            return None, None, None

        try:
            context = geograpy.get_place_context(text=text)
            cities = list(getattr(context, "cities", []) or [])
            countries = list(getattr(context, "countries", []) or [])
            regions = list(getattr(context, "regions", []) or [])

            city = (cities[0] if cities else "") or ""
            country = (countries[0] if countries else "") or ""
            location = city or country or ((regions[0] if regions else "") or "")

            return (
                location.strip() or None,
                city.strip() or None,
                country.strip() or None,
            )
        except Exception:
            return None, None, None

    def _scrape_page_contact_data(self, url: str) -> Tuple[List[str], List[str], str]:
        if self.prevalidate_http and not validate_website_http(
            url,
            timeout=3,
            excluded_domains=self.excluded_domains,
        ):
            logger.warning(f"⏭️ Skipping URL rejected by validator: {url}")
            return [], [], ""

        logger.info(f"Searching for contact data on: {url}")
        self.driver.get(url, timeout_seconds=self.site_timeout_seconds)
        self.driver.sleep(1.0)

        current_url = self.driver.current_url
        if not validate_website_http(
            current_url,
            timeout=3,
            excluded_domains=self.excluded_domains,
        ):
            logger.warning(f"⏭️ Skipping page URL rejected by validator: {current_url}")
            return [], [], ""

        self.accept_cookies()
        self.driver.sleep(1.0)

        page_html = self.driver.get_content(timeout_seconds=self.site_timeout_seconds)
        emails = self._dedupe_preserve_order(extract_emails_from_text(page_html))
        visible_text = self._extract_visible_text(page_html)
        phones = self._extract_phone_numbers_from_text(visible_text)
        return emails, phones, page_html
    
    def find_contact_page(self, base_url: str) -> Optional[str]:
        """
        Try to find a contact page on the website.
        
        Args:
            base_url: Base website URL
        
        Returns:
            Contact page URL if found, None otherwise
        """
        base_url = normalize_url(base_url)

        try:
            _, _, homepage_html = self._scrape_page_contact_data(base_url)
            discovered = self._extract_contact_links_from_home_html(base_url, homepage_html)
            for contact_url in discovered:
                if validate_website_http(
                    contact_url,
                    timeout=2,
                    excluded_domains=self.excluded_domains,
                ):
                    logger.info(f"✓ Found contact page from homepage href: {contact_url}")
                    return contact_url
        except Exception:
            pass

        logger.debug("No contact page found")
        return None

    def find_contact_info_on_website(
        self,
        website_url: str,
    ) -> Tuple[List[str], List[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Find emails, phones, and a contact page URL in a single crawl flow.

        Rules:
        - Scrape homepage first.
        - If both email and phone are present on homepage, skip contact-page crawl.
        - Otherwise crawl homepage-discovered contact links to fill missing values.
        """
        if not website_url:
            return [], [], None, None, None, None

        website_url = normalize_url(website_url)
        all_emails: List[str] = []
        all_phones: List[str] = []
        selected_contact_page: Optional[str] = None
        extracted_location: Optional[str] = None
        extracted_city: Optional[str] = None
        extracted_country: Optional[str] = None

        try:
            homepage_emails, homepage_phones, homepage_html = self._scrape_page_contact_data(website_url)
            all_emails.extend(homepage_emails)
            all_phones.extend(homepage_phones)

            homepage_visible_text = self._extract_visible_text(homepage_html)
            loc, city, country = self._extract_geo_from_visible_text(homepage_visible_text)
            extracted_location = extracted_location or loc
            extracted_city = extracted_city or city
            extracted_country = extracted_country or country

            contact_candidates = self._extract_contact_links_from_home_html(website_url, homepage_html)
            for candidate in contact_candidates:
                if validate_website_http(
                    candidate,
                    timeout=2,
                    excluded_domains=self.excluded_domains,
                ):
                    selected_contact_page = candidate
                    break

            has_email = bool(all_emails)
            has_phone = bool(all_phones)
            if has_email and has_phone:
                logger.info("✓ Homepage already has both email and phone; skipping contact-page crawl")
            else:
                logger.debug("At least one value missing on homepage; checking contact pages")
                for contact_url in contact_candidates:
                    try:
                        if not validate_website_http(
                            contact_url,
                            timeout=2,
                            excluded_domains=self.excluded_domains,
                        ):
                            continue

                        if not selected_contact_page:
                            selected_contact_page = contact_url

                        contact_emails, contact_phones, _ = self._scrape_page_contact_data(contact_url)
                        all_emails.extend(contact_emails)
                        all_phones.extend(contact_phones)

                        if not (extracted_location and extracted_city and extracted_country):
                            contact_visible_text = self._extract_visible_text(_)
                            loc2, city2, country2 = self._extract_geo_from_visible_text(contact_visible_text)
                            extracted_location = extracted_location or loc2
                            extracted_city = extracted_city or city2
                            extracted_country = extracted_country or country2

                        has_email = bool(all_emails)
                        has_phone = bool(all_phones)
                        if has_email and has_phone:
                            break
                    except Exception:
                        continue

            all_emails = self._dedupe_preserve_order(all_emails)
            all_phones = self._dedupe_preserve_order(all_phones)
            return (
                all_emails,
                all_phones,
                selected_contact_page,
                extracted_location,
                extracted_city,
                extracted_country,
            )

        except Exception as exc:
            logger.error(f"Error finding contact info on website: {exc}")
            return [], [], None, None, None, None
        finally:
            try:
                self.driver.get("about:blank")
            except Exception:
                pass

    
