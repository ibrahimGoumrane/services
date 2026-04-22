"""Page scraping utilities using NoDriver"""

import re
from typing import Optional, List, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

from .driver import NoDriverDriver
from .geo_extractor import extract_location_city_country
from .url_utils import normalize_url, validate_website_http
from .email_extractors import extract_emails_from_text
from .logging_config import get_logger


logger = get_logger("dbSeeder.web_scraper")

PHONE_REGEX = re.compile(
    r"(?<!\d)(?!\d{4}[-/]\d{2}[-/]\d{2}\b)(?:\+?\d[\d\s().\-/]{6,}\d)(?!\d)"
)

# Phrases that indicate an active Cloudflare challenge page
_CF_CHALLENGE_PHRASES = ("checking your browser", "just a moment", "please wait")


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

        Strategy:
        - Scrape the homepage first.
        - If both email and phone are found there, stop.
        - Otherwise crawl discovered contact-page links until both are collected.

        Returns:
            (emails, phones, contact_page_url, location, city, country)
        """
        if not website_url:
            return [], [], None, None, None, None

        website_url = normalize_url(website_url)
        logger.info(f"[contact-flow] start website_url={website_url}")

        all_emails: List[str] = []
        all_phones: List[str] = []
        contact_page: Optional[str] = None
        location = city = country = None

        try:
            # ── 1. Scrape homepage ─────────────────────────────────────────
            emails, phones, html = self._scrape_page(website_url)
            all_emails.extend(emails)
            all_phones.extend(phones)
            logger.info(
                f"[contact-flow] homepage emails={len(emails)} phones={len(phones)} website_url={website_url}"
            )

            location, city, country = self._geo(website_url, html)

            contact_candidates = self._extract_contact_links(website_url, html)
            logger.info(f"[contact-flow] contact candidates={len(contact_candidates)}")

            # Pick the first reachable contact link as the representative URL
            contact_page = self._first_valid(contact_candidates)

            # ── 2. Crawl contact pages if we're still missing email or phone
            if all_emails and all_phones:
                logger.info("✓ Homepage has both email and phone; skipping contact-page crawl")
            else:
                location, city, country, all_emails, all_phones = self._crawl_contact_pages(
                    contact_candidates, all_emails, all_phones, location, city, country
                )

            all_emails = self._dedupe(all_emails)
            all_phones = self._dedupe(all_phones)
            logger.info(
                f"[contact-flow] done emails={len(all_emails)} phones={len(all_phones)} "
                f"contact_page={contact_page!r} location={location!r} city={city!r} country={country!r}"
            )
            return all_emails, all_phones, contact_page, location, city, country

        except Exception as exc:
            logger.error(f"Error finding contact info: {exc}")
            return [], [], None, None, None, None

    # ── Private: crawl helpers ─────────────────────────────────────────────

    def _crawl_contact_pages(
        self,
        candidates: List[str],
        emails: List[str],
        phones: List[str],
        location: Optional[str],
        city: Optional[str],
        country: Optional[str],
    ) -> Tuple[Optional[str], Optional[str], Optional[str], List[str], List[str]]:
        """Visit each contact candidate until both email and phone are collected."""
        for url in candidates:
            if not self._http_ok(url):
                continue
            try:
                c_emails, c_phones, c_html = self._scrape_page(url)
                emails.extend(c_emails)
                phones.extend(c_phones)
                logger.info(
                    f"[contact-flow] contact page emails={len(c_emails)} phones={len(c_phones)} url={url}"
                )

                if not (location and city and country):
                    location, city, country = self._geo(url, c_html, location, city, country)

                if emails and phones:
                    break
            except Exception:
                logger.exception(f"[contact-flow] contact page scrape failed url={url}")

        return location, city, country, emails, phones

    def _scrape_page(self, url: str) -> Tuple[List[str], List[str], str]:
        """
        Navigate to *url*, wait through any anti-bot checks, simulate light
        human behaviour, then extract emails, phones, and return the raw HTML.

        Steps:
          1. HTTP pre-validation (avoids hanging on status-999 responses).
          2. Navigate + inject stealth fingerprint scripts.
          3. Cloudflare challenge detection and wait.
          4. Cookie-consent dismissal.
          5. Human-like scroll to trigger lazy-loaded content.
          6. Content extraction.
        """
        logger.info(f"[scrape] start url={url}")

        if not self._http_ok(url):
            logger.warning(f"✗ Pre-validation failed: {url}")
            return [], [], ""

        if not self.driver.tab:
            raise RuntimeError("Driver tab not initialized. Call setup() first.")

        # Navigate and immediately register fingerprint patches for this origin
        self.driver.run(self.driver.tab.get(url), timeout_seconds=self.site_timeout_seconds)
        self.driver.inject_stealth_scripts()

        # Wait briefly for the page to settle before probing for challenges
        self.driver.run(self.driver.tab.sleep(1.5))

        # Handle Cloudflare / bot challenges before touching anything else
        self._handle_cf_challenge()

        # Dismiss cookie banners with a hover-then-click pattern
        self._accept_cookies()

        # Scroll the page like a human to expose lazy-loaded contact data
        self._human_scroll()

        html = str(
            self.driver.run(self.driver.tab.get_content(), timeout_seconds=self.site_timeout_seconds) or ""
        )
        logger.info(f"[scrape] content length={len(html)} url={url}")

        emails = self._dedupe(extract_emails_from_text(html))
        phones = self._extract_phones(self._visible_text(html))
        logger.info(f"[scrape] done emails={len(emails)} phones={len(phones)} url={url}")
        return emails, phones, html

    # ── Private: anti-detection behaviour ─────────────────────────────────

    def _handle_cf_challenge(self) -> None:
        """
        Detect an active Cloudflare (or similar) challenge page and wait it out.

        nodriver passes CF's JS challenge automatically in most cases.
        This method simply gives it enough time and confirms we're clear
        before proceeding, matching the guide's recommended pattern.
        """
        if not self.driver.tab:
            return
        try:
            challenge = self.driver.run(
                self.driver.tab.find(_CF_CHALLENGE_PHRASES[0], best_match=True, timeout=2)
            )
            if challenge:
                logger.info("⏳ Cloudflare challenge detected — waiting for auto-resolution...")
                # Give nodriver time to solve the JS challenge automatically
                self.driver.run(self.driver.tab.sleep(8.0))
                logger.info("✓ Cloudflare wait complete")
        except Exception:
            # No challenge found — that's the happy path
            pass

    def _accept_cookies(self) -> None:
        """
        Click common cookie-consent buttons using hover → click (not a raw
        programmatic click) to mimic the mouse path a human would take.
        """
        if not self.driver.tab:
            return

        # JS scan for the button first so we know whether one exists at all
        scan_script = """
(() => {
    const kw = ['accept all', 'accept cookies', 'i accept', 'i agree', 'agree', 'got it', 'ok'];
    for (const n of document.querySelectorAll('button, a, [role="button"]')) {
        const t = (n.innerText || n.textContent || '').toLowerCase().trim();
        if (t && kw.some(k => t.includes(k))) return n.getAttribute('data-testid') || n.id || n.className || t;
    }
    return null;
})();
"""
        try:
            marker = self.driver.run(self.driver.tab.evaluate(scan_script, return_by_value=True))
            if not marker:
                return

            # Use nodriver's element API for the actual interaction so the
            # hover + click goes through the native input pipeline (not JS .click())
            btn = self.driver.run(
                self.driver.tab.find("accept", best_match=True, timeout=3)
            )
            if btn:
                # Hover first, then click — matches the guide's human-like pattern
                self.driver.run(btn.mouse_move())
                self.driver.run(self.driver.tab.sleep(0.3))
                self.driver.run(btn.mouse_click())
                logger.info("✓ Accepted cookie consent")
                self.driver.run(self.driver.tab.sleep(1.0))
        except Exception:
            logger.debug(f"Cookie banner not found or interaction failed (non-fatal)")

    def _human_scroll(self, steps: int = 4) -> None:
        """
        Scroll down in small random increments with pauses, matching the
        guide's recommendation to mimic natural reading behaviour.

        This also triggers lazy-loaded sections that may contain contact info.
        """
        if not self.driver.tab:
            return
        try:
            import random
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
            if normalized:
                candidates.append(normalized)
        return self._dedupe(candidates)

    def _visible_text(self, html: str) -> str:
        """Strip non-visible nodes and return plain text."""
        if not html:
            return ""
        soup = BeautifulSoup(html, "html.parser")
        for node in soup(["script", "style", "noscript", "svg"]):
            node.decompose()
        return soup.get_text(" ", strip=True)

    def _extract_phones(self, text: str) -> List[str]:
        """Return deduplicated, normalized phone numbers found in *text*."""
        if not text:
            return []
        normalized: List[str] = []
        for raw in PHONE_REGEX.findall(text):
            raw = raw.strip()
            compact = re.sub(r"[^\d+]", "", raw)
            digits = re.sub(r"\D", "", compact)
            if not (7 <= len(digits) <= 15):
                continue
            normalized.append(f"+{digits}" if compact.startswith("+") else digits)
        return self._dedupe(normalized)

    def _geo(
        self,
        url: str,
        html: str,
        location: Optional[str] = None,
        city: Optional[str] = None,
        country: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract geo fields, preserving any values already found."""
        loc, cty, ctr = extract_location_city_country(page_url=url, html=html)
        return location or loc, city or cty, country or ctr

    # ── Private: utilities ─────────────────────────────────────────────────

    def _http_ok(self, url: str) -> bool:
        return validate_website_http(url, timeout=3, excluded_domains=self.excluded_domains)

    def _first_valid(self, candidates: List[str]) -> Optional[str]:
        for url in candidates:
            if validate_website_http(url, timeout=2, excluded_domains=self.excluded_domains):
                return url
        return None

    @staticmethod
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