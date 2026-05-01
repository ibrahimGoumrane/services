"""Page scraping utilities using NoDriver"""
import random
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..utils.driver import NoDriverDriver
from ..utils.extractor_email import extract_emails_from_text
from ..utils.extractor_geo import extract_location_city_country
from ..utils.extractor_phone import extract_phones_from_text
from ..utils.url_utils import normalize_url, validate_website_http
from api.services.utils.logging_config import get_logger

logger = get_logger("dbSeeder.web_scraper")

class PageScraper:
    """Handles web page scraping with nodriver."""

    def __init__(
        self,
        driver: NoDriverDriver,
        excluded_domains: Optional[List[str]] = None,
        site_timeout_seconds: int = 30,
    ) -> None:
        self.driver = driver
        self.excluded_domains = excluded_domains or []
        self.site_timeout_seconds = site_timeout_seconds

    # ── Public API ─────────────────────────────────────────────────────────

    def find_contact_info_on_website(
        self,
        website_url: str,
    ) -> Tuple[List[str], List[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Crawl a website to collect emails, phones, location data, and a contact page URL.
        Each page is fetched exactly once; all extractions run on the cached HTML.

        Returns:
            (emails, phones, contact_page_url, location, city, country)
        """
        if not website_url:
            return [], [], None, None, None, None

        website_url = normalize_url(website_url)
        logger.info(f"[contact-flow] start website_url={website_url}")

        try:
            # ── 1. Scrape homepage ─────────────────────────────────────────
            homepage_html = self._fetch_page_html(website_url)
            if not homepage_html:
                return [], [], None, None, None, None

            all_emails = extract_emails_from_text(homepage_html)
            all_phones = extract_phones_from_text(homepage_html)
            location, city, country = extract_location_city_country(homepage_html)
            contact_candidates = self._extract_contact_links(website_url, homepage_html)

            contact_page = contact_candidates[0] if len(contact_candidates) else None

            # ── 2. Crawl contact pages if we're still missing email or phone
            if all_emails and all_phones:
                logger.info("✓ Homepage has both email and phone; skipping contact-page crawl")
            else:
                for url in contact_candidates:
                    try:
                        html = self._fetch_page_html(url)
                        if not html:
                            continue

                        all_emails.extend(extract_emails_from_text(html))
                        all_phones.extend(extract_phones_from_text(html))

                        if not (location and city and country):
                            location, city, country = extract_location_city_country(html)

                        if all_emails and all_phones:
                            break
                    except Exception:
                        logger.exception(f"[contact-flow] contact page scrape failed url={url}")

            all_emails = _dedupe(all_emails)
            all_phones = _dedupe(all_phones)
            logger.info(
                f"[contact-flow] done emails={len(all_emails)} phones={len(all_phones)} "
                f"contact_page={contact_page!r} location={location!r} city={city!r} country={country!r}"
            )
            return all_emails, all_phones, contact_page, location, city, country

        except Exception as exc:
            logger.error(f"Error finding contact info: {exc}")
            return [], [], None, None, None, None

    # ── Private: page fetch ────────────────────────────────────────────────

    def _fetch_page_html(self, url: str) -> str:
        """
        Navigate to *url*, clear anti-bot hurdles, and return the raw HTML.
        Each URL is fetched exactly once.
        """
        logger.info(f"[scrape] start url={url}")

        if not self._http_ok(url):
            logger.warning(f"✗ Pre-validation failed: {url}")
            return ""

        if not self.driver.tab:
            raise RuntimeError("Driver tab not initialized. Call setup() first.")

        self.driver.run(self.driver.tab.get(url), timeout_seconds=self.site_timeout_seconds)
        self.driver.run(self.driver.tab.sleep(1.5))

        self._handle_cf_challenge()
        self._accept_cookies()
        self._human_scroll()

        html = str(
            self.driver.run(self.driver.tab.get_content(), timeout_seconds=self.site_timeout_seconds) or ""
        )
        logger.info(f"[scrape] content length={len(html)} url={url}")
        return html

    # ── Private: anti-detection behaviour ─────────────────────────────────

    def _handle_cf_challenge(self) -> None:
        """Pass Cloudflare challenge if present."""
        if not self.driver.tab:
            return
        try:
            self.driver.run(self.driver.tab.verify_cf(flash=True))
            logger.info("✓ Passed Cloudflare challenge")
        except Exception:
            pass

    def _accept_cookies(self) -> None:
        """Dismiss cookie-consent banner with a hover → click."""
        if not self.driver.tab:
            return
        try:
            btn = (
                self.driver.run(self.driver.tab.find("accept", best_match=True, timeout=3))
                or self.driver.run(self.driver.tab.find("agree", best_match=True, timeout=2))
            )
            if btn:
                self.driver.run(btn.mouse_move())
                self.driver.run(self.driver.tab.sleep(0.3))
                self.driver.run(btn.mouse_click())
                logger.info("✓ Accepted cookie consent")
                self.driver.run(self.driver.tab.sleep(1.0))
        except Exception:
            logger.debug("Cookie banner not found or interaction failed (non-fatal)")

    def _human_scroll(self, steps: int = 4) -> None:
        """Scroll down in small random increments with pauses."""
        if not self.driver.tab:
            return
        try:
            for _ in range(steps):
                self.driver.run(self.driver.tab.scroll_down(random.randint(150, 350)))
                self.driver.run(self.driver.tab.sleep(random.uniform(0.4, 1.0)))
        except Exception as e:
            logger.debug(f"Human scroll failed (non-fatal): {e}")

    # ── Private: extraction helpers ────────────────────────────────────────

    def _extract_contact_links(self, base_url: str, html: str) -> List[str]:
        """Return deduplicated absolute URLs whose path contains 'contact'."""
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        candidates: List[str] = []
        for node in soup.select("a[href]"):
            href = (node.get("href") or "").strip()
            href_lower = href.lower()
            if (
                not href
                or "contact" not in href_lower
                or href_lower.startswith(("mailto:", "tel:", "javascript:", "#"))
            ):
                continue
            absolute = urljoin(base_url, href)
            if urlparse(absolute).scheme not in {"http", "https"}:
                continue
            normalized = normalize_url(absolute)
            if self._http_ok(normalized) :
                candidates.append(normalized)
            
        return _dedupe(candidates)

    # ── Private: utilities ─────────────────────────────────────────────────

    def _http_ok(self, url: str) -> bool:
        if not url:
            return False
        return validate_website_http(url, timeout=3, excluded_domains=self.excluded_domains)


def _dedupe(values: List[str]) -> List[str]:
    """Remove duplicates while preserving insertion order."""
    seen: set = set()
    result: List[str] = []
    for v in values:
        key = v.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(v.strip())
    return result
