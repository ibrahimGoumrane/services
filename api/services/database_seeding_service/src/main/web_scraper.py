"""Low-level page fetching utilities using NoDriver."""

import random
from typing import Callable, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..utils.driver import NoDriverDriver
from ..utils.url_utils import normalize_url, validate_website_http
from api.services.utils.logging_config import get_logger

logger = get_logger("dbSeeder.web_scraper")


class PageScraper:
    """Raw page fetcher with anti-bot helpers."""

    def __init__(
        self,
        driver: NoDriverDriver,
        excluded_domains: Optional[List[str]] = None,
        site_timeout_seconds: int = 20,
        page_load_timeout_seconds: int = 45,
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

        if not self.driver.tab:
            raise RuntimeError("Driver tab not initialized. Call setup() first.")

        self.driver.run(
            self.driver.tab.get(url), timeout_seconds=self.page_load_timeout_seconds
        )
        self.driver.run(self.driver.tab.sleep(0.8))

        self._handle_cf_challenge()
        self._accept_cookies()
        self._human_scroll()

        html = str(
            self.driver.run(
                self.driver.tab.get_content(),
                timeout_seconds=self.site_timeout_seconds,
            )
            or ""
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
                self.driver.run(
                    self.driver.tab.find("accept", best_match=True, timeout=2)
                )
                or self.driver.run(
                    self.driver.tab.find("agree", best_match=True, timeout=1)
                )
            )
            if btn:
                self.driver.run(btn.mouse_move())
                self.driver.run(self.driver.tab.sleep(0.15))
                self.driver.run(btn.mouse_click())
                logger.info("✓ Accepted cookie consent")
                self.driver.run(self.driver.tab.sleep(0.4))
        except Exception:
            logger.debug("Cookie banner not found or interaction failed (non-fatal)")

    def _human_scroll(self, steps: int = 2) -> None:
        """Scroll down in small random increments with pauses."""
        if not self.driver.tab:
            return
        try:
            for _ in range(steps):
                self.driver.run(
                    self.driver.tab.scroll_down(random.randint(150, 350))
                )
                self.driver.run(self.driver.tab.sleep(random.uniform(0.2, 0.4)))
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
