"""Google search automation utilities"""

import logging
import random
import time
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, urlparse

from bs4 import BeautifulSoup

from .url_utils import validate_website_http
from .web_scraper import NoDriverDriver


logger = logging.getLogger(__name__)

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

# Phrases present on Google's CAPTCHA / unusual-traffic interstitial
_GOOGLE_CAPTCHA_PHRASES = (
    "unusual traffic",
    "notre système a détecté",
    "i'm not a robot",
    "recaptcha",
    "verify you are human",
)


class GoogleSearcher:
    """Google search automation with anti-bot measures (nodriver backend)."""

    def __init__(
        self,
        driver: NoDriverDriver,
        excluded_domains: Optional[List[str]] = None,
        generic_domains: Optional[List[str]] = None,
        site_timeout_seconds: int = 30,
    ) -> None:
        self.driver = driver
        self.site_timeout_seconds = site_timeout_seconds
        # Merged exclusion list used in every URL-validation call
        self._excluded = list(excluded_domains or []) + list(generic_domains or [])

    # ── Public API ─────────────────────────────────────────────────────────

    def search(
        self,
        business_name: str,
        location: Optional[str] = None,
        max_retries: int = 2,
    ) -> Tuple[Optional[str], bool]:
        """
        Search Google for a business and return the first valid result URL.

        Returns:
            (url, True)   – a valid URL was found
            (None, False) – otherwise
        """
        query = f"'{business_name}' '{location}'." if location else f"'{business_name}'"

        for attempt in range(max_retries):
            try:
                logger.info(f"Google search: {query!r}  (attempt {attempt + 1}/{max_retries})")
                url, ok = self._attempt_search(query)
                if ok:
                    return url, True
            except Exception as exc:
                logger.error(f"Search error (attempt {attempt + 1}/{max_retries}): {exc}")
                if attempt < max_retries - 1:
                    self._maybe_restart_driver(exc)
                    time.sleep(2)

        logger.error(f"Max retries ({max_retries}) reached for query: {query!r}")
        return None, False

    # ── Private: search flow ───────────────────────────────────────────────

    def _attempt_search(self, query: str) -> Tuple[Optional[str], bool]:
        """
        Single attempt: navigate to Google → inject stealth scripts →
        type query with human-like delays → handle CAPTCHA → parse SERP.
        """
        if not self.driver.tab:
            raise RuntimeError("Driver tab not initialized. Call setup() first.")

        self.driver.run(
            self.driver.tab.get("https://www.google.com"),
            timeout_seconds=self.site_timeout_seconds,
        )

        # Inject canvas-noise + WebGL-spoof before Google's fingerprinting fires
        self.driver.inject_stealth_scripts()

        self.driver.run(self.driver.tab.sleep(random.uniform(1.0, 1.5)))

        if not self._type_query(query):
            logger.debug("Typing failed – falling back to direct URL navigation")
            self.driver.run(
                self.driver.tab.get(f"https://www.google.com/search?q={quote_plus(query)}"),
                timeout_seconds=self.site_timeout_seconds,
            )

        self._accept_cookies()

        # Wait for the SERP to render
        self.driver.run(self.driver.tab.sleep(random.uniform(2.0, 2.8)))

        # Check for CAPTCHA / unusual-traffic interstitial before reading results
        if self._google_captcha_detected():
            logger.warning("⚠️ Google CAPTCHA detected — search aborted for this attempt")
            return None, False

        html = str(
            self.driver.run(
                self.driver.tab.get_content(),
                timeout_seconds=self.site_timeout_seconds,
            )
            or ""
        )

        url = self._extract_first_result(html)
        if url:
            logger.info(f"✓ Result: {url}")
            return url, True

        logger.warning("No valid Google results found")
        return None, False

    # ── Private: CAPTCHA detection ─────────────────────────────────────────

    def _google_captcha_detected(self) -> bool:
        """
        Return True if the current page looks like a Google CAPTCHA or
        unusual-traffic interstitial.

        Checks the live page text via nodriver's find() so it works even
        when the interstitial is injected dynamically after page load.
        """
        if not self.driver.tab:
            return False
        for phrase in _GOOGLE_CAPTCHA_PHRASES:
            try:
                match = self.driver.run(
                    self.driver.tab.find(phrase, best_match=True, timeout=1)
                )
                if match:
                    logger.warning(f"CAPTCHA phrase found: {phrase!r}")
                    return True
            except Exception:
                pass
        return False

    # ── Private: typing ────────────────────────────────────────────────────

    def _type_query(self, query: str) -> bool:
        """
        Fill the search field character-by-character with randomised delays
        (occasional longer pauses every ~12 chars to mimic reading/thinking),
        then click the Search button.
        """
        if not query or not self.driver.tab:
            return False
        try:
            field = self.driver.run(self.driver.tab.select(_SEARCH_FIELD_SELECTOR, timeout=3))
            if not field:
                return False

            self.driver.run(self.driver.tab.sleep(random.uniform(0.2, 0.5)))

            for char in query:
                self.driver.run(field.send_keys(char))
                delay = random.uniform(0.04, 0.18)
                # Occasional longer pause (simulates hesitation / thinking)
                if random.random() < 0.08:
                    delay += random.uniform(0.25, 0.60)
                self.driver.run(self.driver.tab.sleep(delay))

            # Brief review pause before submitting — humans don't type and
            # instantly hit Enter
            self.driver.run(self.driver.tab.sleep(random.uniform(0.4, 1.0)))

            button = self.driver.run(self.driver.tab.select('input[name="btnK"]', timeout=2))
            if not button:
                return False

            # Hover over the button before clicking (native input pipeline)
            self.driver.run(button.mouse_move())
            self.driver.run(self.driver.tab.sleep(random.uniform(0.1, 0.3)))
            self.driver.run(button.mouse_click())
            return True

        except Exception as exc:
            logger.debug(f"Typing failed: {exc}")
            return False

    # ── Private: cookie banner ─────────────────────────────────────────────

    def _accept_cookies(self) -> None:
        """Dismiss Google's cookie/consent banner with a hover → click."""
        if not self.driver.tab:
            return
        try:
            btn = self.driver.run(
                self.driver.tab.find("accept all", best_match=True, timeout=2)
            ) or self.driver.run(
                self.driver.tab.find("accept", best_match=True, timeout=2)
            )
            if btn:
                self.driver.run(btn.mouse_move())
                self.driver.run(self.driver.tab.sleep(random.uniform(0.1, 0.3)))
                self.driver.run(btn.mouse_click())
                logger.info("✓ Accepted Google cookies")
                self.driver.run(self.driver.tab.sleep(0.8))
        except Exception as exc:
            logger.debug(f"Cookie banner: {exc}")

    # ── Private: result extraction ─────────────────────────────────────────

    def _extract_first_result(self, html: str) -> Optional[str]:
        """
        Parse SERP HTML and return the first validated external URL.

        Priority:
          1. Local knowledge-panel "Website" action
          2. First organic result in .A6K0A containers
        """
        soup = BeautifulSoup(html or "", "html.parser")

        # 1) Local knowledge panel
        panel = soup.select_one("div.zhZ3gf")
        if panel:
            actions = self._extract_local_actions(panel)
            website = actions.get("website", "")
            if website and self._url_ok(website):
                logger.info(f"Local panel website: {website}")
                return website
            logger.debug(f"Local panel website failed validation: {website!r}")
        else:
            logger.debug("No local panel found (div.zhZ3gf)")

        # 2) Organic results
        for container in soup.select(".A6K0A"):
            anchor = container.select_one('a[jsname="UWckNb"]')
            if not anchor:
                continue
            candidate = self._resolve_href(anchor.get("href", "").strip())
            if candidate and self._url_ok(candidate):
                return candidate

        return None

    def _extract_local_actions(self, root) -> Dict[str, str]:
        """Return a mapping of action-key → URL/value from the local panel."""
        actions: Dict[str, str] = {}
        for block in root.select("div.bkaPDb"):
            label_el = block.select_one("span.aSAiSd")
            key = self._normalize_label(label_el.get_text(" ", strip=True) if label_el else "")
            if not key:
                continue

            if key == "call":
                phone_el = block.select_one("[data-phone-number]")
                if phone_el:
                    phone = (phone_el.get("data-phone-number") or "").strip()
                    if phone:
                        actions[key] = phone
                continue

            link = block.select_one("a[href]")
            href = (link.get("href") or "").strip() if link else ""
            if not href:
                continue

            if key == "website":
                resolved = self._resolve_href(href)
                if resolved:
                    actions[key] = resolved
            elif href.startswith(("http://", "https://")):
                actions[key] = href
            elif href.startswith("/"):
                actions[key] = f"https://www.google.com{href}"

        return actions

    # ── Private: label / href helpers ─────────────────────────────────────

    @staticmethod
    def _normalize_label(raw: str) -> str:
        """Map a panel action label (locale-agnostic) to an internal key."""
        normalized = unicodedata.normalize("NFKD", raw.strip().lower())
        stripped = "".join(c for c in normalized if not unicodedata.combining(c))
        return _LOCAL_ACTION_MAP.get(" ".join(stripped.split()), "")

    @staticmethod
    def _resolve_href(href: str) -> str:
        """Resolve Google SERP href formats to a plain target URL."""
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

    def _url_ok(self, url: str) -> bool:
        return validate_website_http(url, excluded_domains=self._excluded)

    def _maybe_restart_driver(self, exc: Exception) -> None:
        if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
            logger.warning("⚠️ Timeout detected – restarting driver...")
            try:
                self.driver.restart(reason="health")
                logger.info("✅ Driver restarted")
            except Exception as restart_exc:
                logger.error(f"Driver restart failed: {restart_exc}")