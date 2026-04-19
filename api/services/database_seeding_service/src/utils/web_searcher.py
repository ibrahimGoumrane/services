"""Google search automation utilities"""

import logging
import time
import random
import unicodedata
from urllib.parse import quote_plus, urlparse, parse_qs
from typing import Tuple, Optional, List, Dict
from bs4 import BeautifulSoup

from .url_utils import validate_website_http
from .web_scraper import NoDriverDriver

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
_SEARCH_FIELD_SELECTOR = 'textarea[name="q"], input[name="q"], #APjFqb'
_LOCAL_ACTION_MAP: Dict[str, str] = {
    "website":       "website",
    "site web":      "website",
    "site internet": "website",
    "site":          "website",
    "call":          "call",
    "appeler":       "call",
    "appel":         "call",
    "directions":    "directions",
    "itineraire":    "directions",
    "itineraires":   "directions",
}

class GoogleSearcher:
    """Handles Google search automation with anti-bot measures (nodriver backend)."""

    def __init__(
        self,
        driver: NoDriverDriver,
        excluded_domains: Optional[List[str]] = None,
        generic_domains:  Optional[List[str]] = None,
        site_timeout_seconds: int = 30,
    ) -> None:
        self.driver               = driver
        self.excluded_domains     = excluded_domains or []
        self.generic_domains      = generic_domains  or []
        self.site_timeout_seconds = site_timeout_seconds
        # Combined exclusion list reused in every extraction call
        self._excl_for_validation = list(self.excluded_domains) + list(self.generic_domains)

    # ── Public API ────────────────────────────────────────────────────────────

    def search(
        self,
        business_name: str,
        location:      Optional[str] = None,
        max_retries:   int = 2,
    ) -> Tuple[Optional[str], bool]:
        """
        Search Google for a business and return the first valid result URL.

        Returns:
            (url, True)  – if a valid URL was found
            (None, False)– otherwise
        """
        query = f"'{business_name}' '{location}'." if location else f"'{business_name}'"

        for attempt in range(max_retries):
            try:
                logger.info(f"Google search: {query!r}  (attempt {attempt+1}/{max_retries})")
                url, ok = self._attempt_search(query)
                if ok:
                    return url, True

            except Exception as exc:
                logger.error(f"Search error (attempt {attempt+1}/{max_retries}): {exc}")
                if attempt < max_retries - 1:
                    self._maybe_restart_driver(exc)
                    time.sleep(2)
                    continue
                logger.error(f"Max retries ({max_retries}) reached")

        return None, False

    # ── Private: search flow ──────────────────────────────────────────────────

    def _attempt_search(self, query: str) -> Tuple[Optional[str], bool]:
        """Single attempt: navigate → type → submit → parse results."""
        self.driver.get("https://www.google.com", timeout_seconds=self.site_timeout_seconds)
        self.driver.sleep(random.uniform(1.0, 1.5))

        if not self._type_query(query):
            logger.debug("Typing failed – falling back to direct URL")
            self.driver.get(
                f"https://www.google.com/search?q={quote_plus(query)}",
                timeout_seconds=self.site_timeout_seconds,
            )

        self._accept_cookies()
        self.driver.sleep(random.uniform(2.0, 2.8))   # wait for SERP

        html = self.driver.get_content(timeout_seconds=self.site_timeout_seconds)
        candidates = self._extract_result_urls(html)

        if not candidates:
            logger.warning("No valid Google results found")
            return None, False

        for idx, url in enumerate(candidates, 1):
            logger.info(f"✓ Result #{idx}: {url}")
            return url, True

        return None, False

    # ── Private: typing ───────────────────────────────────────────────────────

    def _type_query(self, query: str) -> bool:
        """
        Fill Google's search field character-by-character using nodriver element APIs,
        then click the Google Search button.
        """
        if not query:
            return False
        try:
            search_field = self.driver.select(_SEARCH_FIELD_SELECTOR, timeout=3)
            if not search_field:
                return False

            self.driver.sleep(random.uniform(0.2, 0.5))

            for char in query:
                self.driver.send_keys(search_field, char)
                delay = random.uniform(0.04, 0.18)
                if random.random() < 0.08:
                    delay += random.uniform(0.25, 0.60)
                self.driver.sleep(delay)

            self.driver.sleep(random.uniform(0.4, 1.0))   # pre-submit review pause
            
            # Click the Google Search button
            search_button = self.driver.select('input[name="btnK"]', timeout=2)
            if not search_button:
                logger.debug("Search button not found")
                return False
            
            self.driver.click(search_button)
            logger.debug("✓ Clicked Google Search button")
            return True

        except Exception as exc:
            logger.debug(f"Typing failed: {exc}")
            return False

    # ── Private: cookie banner ────────────────────────────────────────────────

    def _accept_cookies(self) -> None:
        try:
            cookie_button = self.driver.find("accept all", best_match=True, timeout=2)
            if not cookie_button:
                cookie_button = self.driver.find("accept", best_match=True, timeout=2)
            if cookie_button:
                self.driver.click(cookie_button)
                logger.info("✓ Accepted Google cookies")
                self.driver.sleep(0.8)
        except Exception as exc:
            logger.debug(f"Cookie banner: {exc}")

    # ── Private: result extraction ────────────────────────────────────────────

    def _extract_result_urls(self, html: str) -> List[str]:
        """
        Parse SERP HTML and return validated external URLs.
        Priority: local-panel Website  →  organic .A6K0A containers.
        """
        soup = BeautifulSoup(html or "", "html.parser")

        # 1) Local knowledge panel
        local_panel = soup.select_one("div.zhZ3gf")
        if local_panel:
            actions = self._extract_local_actions(local_panel)
            logger.debug(f"Local panel actions: {sorted(actions)}")
            website = actions.get("website", "")
            if website:
                if validate_website_http(website, excluded_domains=self._excl_for_validation):
                    logger.info(f"Local panel website: {website}")
                    return [website]
                logger.debug(f"Local panel website failed validation: {website}")
        else:
            logger.debug("No local panel (div.zhZ3gf)")

        # 2) Organic results
        for container in soup.select(".A6K0A"):
            anchor = container.select_one('a[jsname="UWckNb"]')
            if not anchor:
                continue
            candidate = self._normalize_href(anchor.get("href", "").strip())
            if candidate and validate_website_http(
                candidate, excluded_domains=self._excl_for_validation
            ):
                return [candidate]

        return []

    def _extract_local_actions(self, root) -> Dict[str, str]:
        """Extract action → URL/value pairs from the local knowledge panel."""
        actions: Dict[str, str] = {}

        for block in root.select("div.bkaPDb"):
            label_node = block.select_one("span.aSAiSd")
            raw_label  = label_node.get_text(" ", strip=True) if label_node else ""
            key        = self._map_action_label(raw_label)
            if not key:
                continue

            if key == "call":
                phone_el = block.select_one("[data-phone-number]")
                phone    = (phone_el.get("data-phone-number") or "").strip() if phone_el else ""
                if phone:
                    actions[key] = phone
                continue

            link = block.select_one("a[href]")
            href = (link.get("href") or "").strip() if link else ""
            if not href:
                continue

            if key == "website":
                candidate = self._normalize_href(href)
                if candidate:
                    actions[key] = candidate
            elif href.startswith(("http://", "https://")):
                actions[key] = href
            elif href.startswith("/"):
                actions[key] = f"https://www.google.com{href}"

        return actions

    # ── Private: label / href normalization ───────────────────────────────────

    def _map_action_label(self, raw: str) -> str:
        """Normalize a panel action label (locale-agnostic) to an internal key."""
        normalized = unicodedata.normalize("NFKD", raw.strip().lower())
        stripped   = "".join(c for c in normalized if not unicodedata.combining(c))
        clean      = " ".join(stripped.split())
        return _LOCAL_ACTION_MAP.get(clean, "")

    @staticmethod
    def _normalize_href(href: str) -> str:
        """Resolve various Google SERP href formats to a plain target URL."""
        if not href:
            return ""
        if href.startswith("//"):
            return f"https:{href}".strip()
        if href.startswith(("/url?", "./url?")):
            qs = parse_qs(urlparse(href).query)
            return (qs.get("q", [""])[0] or qs.get("url", [""])[0]).strip()
        if href.startswith("/"):
            return ""   # internal Google link – discard
        if href.startswith(("http://", "https://")):
            return href.strip()
        return ""

    def _maybe_restart_driver(self, exc: Exception) -> None:
        """Restart the nodriver instance on timeout errors."""
        if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
            logger.warning("⚠️ Timeout – restarting driver …")
            try:
                self.driver.restart(reason="health")
                logger.info("✅ Driver restarted")
            except Exception as restart_exc:
                logger.error(f"Driver restart failed: {restart_exc}")