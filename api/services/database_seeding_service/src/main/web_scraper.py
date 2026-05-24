"""Low-level page fetching utilities using Selenium."""

import random
from typing import Callable, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..utils.driver import SeleniumDriver
from ..utils.url_utils import normalize_url
from api.services.utils.logging_config import get_logger

logger = get_logger("dbSeeder.web_scraper")

MAX_CONTENT_LENGTH = 3_000_000  # 2MB


class PageScraper:
    """Raw page fetcher with anti-bot helpers."""

    def __init__(
        self,
        driver: SeleniumDriver,
        excluded_domains: Optional[List[str]] = None,
        site_timeout_seconds: int = 8,
        page_load_timeout_seconds: int = 18,
    ) -> None:
        self.driver = driver
        self.excluded_domains = excluded_domains or []
        self.site_timeout_seconds = site_timeout_seconds
        self.page_load_timeout_seconds = page_load_timeout_seconds

    # ── Public API ─────────────────────────────────────────────────────────

    def fetch_page(self, url: str) -> str:
        """
        Navigate to *url*, clear anti-bot hurdles, and return the raw HTML.
        """
        logger.info(f"[scrape] start url={url}")

        if not self.driver.driver:
            raise RuntimeError("Driver not initialized. Call setup() first.")

        self.driver.get(url, timeout=self.page_load_timeout_seconds)
        self.driver.sleep(0.3)

        self._accept_cookies()
        self._human_scroll()

        html = self.driver.page_source or ""
        if len(html) > MAX_CONTENT_LENGTH:
            logger.warning(
                f"[scrape] Skipping oversized page ({len(html)} bytes): {url}"
            )
            return ""
        logger.info(f"[scrape] content length={len(html)} url={url}")
        return html

    # ── Private: anti-detection behaviour ─────────────────────────────────

    def _accept_cookies(self) -> None:
        """Dismiss cookie-consent banner with a hover → click."""
        if not self.driver.driver:
            return
        try:
            btn = (
                self.driver.find_text("allow", timeout=1)
                or self.driver.find_text("accept", timeout=1)
                or self.driver.find_text("agree", timeout=0.5)
            )
            if btn:
                self.driver.move_and_click(btn)
                logger.info("✓ Accepted cookie consent")
                self.driver.sleep(0.15)
        except Exception:
            logger.debug("Cookie banner not found or interaction failed (non-fatal)")

    def _human_scroll(self, steps: int = 2) -> None:
        """Scroll down in small random increments with pauses."""
        if not self.driver.driver:
            return
        try:
            for _ in range(steps):
                self.driver.scroll(random.randint(150, 350))
                self.driver.sleep(random.uniform(0.08, 0.15))
        except Exception as e:
            logger.debug(f"Human scroll failed (non-fatal): {e}")


# ── Module-level helpers ─────────────────────────────────────────────────


def extract_contact_links(
    base_url: str,
    html: str,
    url_ok: Optional[Callable[[str], bool]] = None,
) -> List[str]:
    """
    Return deduplicated absolute URLs whose path contains 'contact'.
    Only includes URLs that pass *url_ok* when provided.
    """
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
        if url_ok and not url_ok(normalized):
            continue
        candidates.append(normalized)

    return _dedupe(candidates)


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


def extract_all_links(
    base_url: str,
    html: str,
) -> List[str]:
    """Return all absolute page URLs found in <a href> tags, excluding mailto/tel/js/anchors."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    candidates: List[str] = []
    for node in soup.select("a[href]"):
        href = (node.get("href") or "").strip()
        if not href:
            continue
        href_lower = href.lower()
        if href_lower.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).scheme not in {"http", "https"}:
            continue
        normalized = normalize_url(absolute)
        candidates.append(normalized)
    return _dedupe(candidates)
