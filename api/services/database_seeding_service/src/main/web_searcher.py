"""Google search automation utilities"""

import random
import unicodedata
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
import time

from api.services.database_seeding_service.src.utils.exceptions import WebsearchFailure
from api.services.database_seeding_service.src.utils.url_utils import validate_website_http
from .web_scraper import NoDriverDriver
from api.services.utils.log_socket import get_seeding_logger

logger = get_seeding_logger()

# In case we need to update these in the future. 
_SEARCH_FIELD_SELECTOR = '#APjFqb'
_SUBMIT_BUTTON_SELECTOR = 'body > div.L3eUgb > div.o3j99.ikrT4e.om7nvf > form > div:nth-child(1) > div > div.FPdoLc.lJ9FBc > center > input.gNO89b'
_LOCAL_PANEL_SELECTOR = 'div.zhZ3gf'
_ORGANIC_RESULT_CONTAINER_SELECTOR = '.A6K0A'
_ORGANIC_RESULT_ANCHOR_SELECTOR = 'a[jsname="UWckNb"]'
_LOCAL_ACTION_BLOCK_SELECTOR = 'div.bkaPDb'
_LOCAL_ACTION_LABEL_SELECTOR = 'span.aSAiSd'
_PHONE_ELEMENT_SELECTOR = '[data-phone-number]'
_ACTION_LINK_SELECTOR = 'a[href]'
_ADDRESS_CONTAINER_SELECTOR = '[data-attrid="kc:/location/location:address"]'
_ADDRESS_TEXT_SELECTOR = 'span.LrzXr'

_LOCAL_ACTION_MAP: Dict[str, str] = {
    "website": "website",
    "site web": "website",
    "site internet": "website",
    "site": "website",
    "call": "call",
    "appeler": "call",
    "appel": "call",
    "directions": "directions",
    "itineraire": "directions",
    "itineraires": "directions",
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
        site_timeout_seconds: int = 20,
        page_load_timeout_seconds: int = 45,
    ) -> None:
        self.driver = driver
        self.site_timeout_seconds = site_timeout_seconds
        self.page_load_timeout_seconds = page_load_timeout_seconds
        self._excluded: List[str] = []
    # ── Public API ─────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        validate_urls: bool = True,
    ) -> Tuple[List[str], Optional[Dict[str, str]]]:
        """
        Search Google for *query* and return **all** candidate URLs plus any
        local-knowledge-panel data.

        Args:
            query: Raw Google search query.
            validate_urls: If ``True``, run ``validate_website_http`` on every
                           organic result. Set to ``False`` for hosts that block
                           bots (e.g. LinkedIn).

        Returns:
            (urls, local_panel)
            urls        – list of external URLs from organic results
            local_panel – dict with keys like ``website``, ``call``,
                          ``directions``, etc. (``None`` if no panel found)
        """
        if not query:
            return [], None

        logger.info(f"Google search: {query!r}")

        try:
            html = self._run_google_search(query)
            if html is None:
                return [], None

            local_panel = self._extract_local_panel(html)
            urls = self._extract_all_results(html, validate=validate_urls)

            if local_panel:
                logger.info(f"Local panel data: {local_panel}")
            logger.info(f"Found {len(urls)} organic result(s)")
            return urls, local_panel

        except Exception as exc:
            logger.error(f"Search error for {query!r}: {exc}")
            return [], None

    # ── Private: shared Google flow ────────────────────────────────────────

    def _run_google_search(self, query: str) -> Optional[str]:
        """
        Navigate to Google, type *query*, handle cookies/CAPTCHA, and return
        the raw SERP HTML.  Returns ``None`` on CAPTCHA or fatal error.
        """
        if not self.driver.tab:
            raise RuntimeError("Driver tab not initialized. Call setup() first.")

        self.driver.run(
            self.driver.tab.get("https://www.google.com"),
            timeout_seconds=self.page_load_timeout_seconds,
        )
        self.driver.run(self.driver.tab.sleep(random.uniform(0.5, 0.8)))

        if not self._type_query(query):
            # If this dont work early return
            raise WebsearchFailure("Failed to submit search query , Verify selectors and page structure")

        self._accept_cookies()
        self.driver.run(self.driver.tab.sleep(random.uniform(1.0, 1.5)))

        if self._google_captcha_detected():
            logger.warning("⚠️ Google CAPTCHA detected — waiting for manual resolution…")
            
            while self._google_captcha_detected():
                time.sleep(2)
            logger.info("✅ CAPTCHA resolved — continuing search")

        html = str(
            self.driver.run(
                self.driver.tab.get_content(),
                timeout_seconds=self.site_timeout_seconds,
            )
            or ""
        )
        return html

    # ── Private: CAPTCHA detection ─────────────────────────────────────────

    def _google_captcha_detected(self) -> bool:
        """
        Return True if the current page looks like a Google CAPTCHA or
        unusual-traffic interstitial.
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
        Fill the search field character-by-character with randomised delays,
        then click the Search button.
        """
        if not query or not self.driver.tab:
            return False
        try:
            field = self.driver.run(
                self.driver.tab.select(_SEARCH_FIELD_SELECTOR, timeout=3)
            )
            if not field:
                return False

            self.driver.run(self.driver.tab.sleep(random.uniform(0.1, 0.2)))

            for char in query:
                self.driver.run(field.send_keys(char))
                delay = random.uniform(0.02, 0.08)
                if random.random() < 0.08:
                    delay += random.uniform(0.1, 0.2)
                self.driver.run(self.driver.tab.sleep(delay))

            self.driver.run(self.driver.tab.sleep(random.uniform(0.2, 0.4)))

            button = self.driver.run(
                self.driver.tab.select(_SUBMIT_BUTTON_SELECTOR, timeout=1)
            )
            if not button:
                return False

            self.driver.run(button.mouse_move())
            self.driver.run(self.driver.tab.sleep(random.uniform(0.05, 0.15)))
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
            btn = (
                self.driver.run(
                    self.driver.tab.find("accept all", best_match=True, timeout=1)
                )
                or self.driver.run(
                    self.driver.tab.find("accept", best_match=True, timeout=1)
                )
            )
            if btn:
                self.driver.run(btn.mouse_move())
                self.driver.run(self.driver.tab.sleep(random.uniform(0.05, 0.15)))
                self.driver.run(btn.mouse_click())
                logger.info("✓ Accepted Google cookies")
                self.driver.run(self.driver.tab.sleep(0.3))
        except Exception as exc:
            logger.debug(f"Cookie banner: {exc}")

    # ── Private: result extraction ─────────────────────────────────────────

    def _extract_local_panel(self, html: str) -> Optional[Dict[str, str]]:
        """Parse SERP HTML and return local-knowledge-panel actions if present."""
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        panel = soup.select_one(_LOCAL_PANEL_SELECTOR)
        if not panel:
            logger.debug(f"No local panel found ({_LOCAL_PANEL_SELECTOR})")
            return None
        return self._extract_local_actions(panel)

    def _extract_all_results(self, html: str, *, validate: bool = True) -> List[str]:
        """
        Parse SERP HTML and return **all** external URLs from organic results.

        Args:
            html: Raw SERP HTML.
            validate: If ``True``, only keep URLs that pass
                      ``validate_website_http``.
        """
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        urls: List[str] = []
        seen: set = set()

        for container in soup.select(_ORGANIC_RESULT_CONTAINER_SELECTOR):
            anchor = container.select_one(_ORGANIC_RESULT_ANCHOR_SELECTOR)
            if not anchor:
                continue
            candidate = _resolve_href(anchor.get("href", "").strip())
            if not candidate:
                continue
            if validate and not self._url_ok(candidate):
                continue
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                urls.append(candidate)

        return urls

    def _extract_local_actions(self, root) -> Dict[str, str]:
        """Return a mapping of action-key → URL/value from the local panel."""
        actions: Dict[str, str] = {}
        for block in root.select(_LOCAL_ACTION_BLOCK_SELECTOR):
            label_el = block.select_one(_LOCAL_ACTION_LABEL_SELECTOR)
            key = _normalize_label(
                label_el.get_text(" ", strip=True) if label_el else ""
            )
            if not key:
                continue

            if key == "call":
                phone_el = block.select_one(_PHONE_ELEMENT_SELECTOR)
                if phone_el:
                    phone = (phone_el.get("data-phone-number") or "").strip()
                    if phone:
                        actions["phone"] = phone
                continue

            link = block.select_one(_ACTION_LINK_SELECTOR)
            href = (link.get("href") or "").strip() if link else ""
            if not href:
                continue

            if key == "website":
                resolved = _resolve_href(href)
                if resolved:
                    actions[key] = resolved
            elif href.startswith(("http://", "https://")):
                actions[key] = href
            elif href.startswith("/"):
                actions[key] = f"https://www.google.com{href}"
        
        # Extract the address field
        container = root.select_one(_ADDRESS_CONTAINER_SELECTOR)

        if container:
            address_el = container.select_one(_ADDRESS_TEXT_SELECTOR)
            if address_el:
                address = address_el.get_text(strip=True)   
                if address:
                        actions["address"] = address
                        
        return actions

    def refresh_excluded(self , excluded_domains , generic_domains) -> None:
        """Recompute the merged exclusion list from current mutable attributes."""
        self._excluded = list(excluded_domains) + list(generic_domains)

    def _url_ok(self, url: str, required_domain: Optional[str] = None) -> bool:
        if required_domain:
            host = (urlparse(url).netloc or "").strip().lower()
            if host.startswith("www."):
                host = host[4:]
            if not host.endswith(required_domain):
                return False
        return validate_website_http(url, excluded_domains=self._excluded)

    def _maybe_restart_driver(self, exc: Exception) -> None:
        if "timeout" in str(exc).lower() or "timed out" in str(exc).lower():
            logger.warning("⚠️ Timeout detected – restarting driver...")
            try:
                self.driver.restart(reason="health")
                logger.info("✅ Driver restarted")
            except Exception as restart_exc:
                logger.error(f"Driver restart failed: {restart_exc}")


# ── Module-level helpers ─────────────────────────────────────────────────


def _normalize_label(raw: str) -> str:
    """Map a panel action label (locale-agnostic) to an internal key."""
    normalized = unicodedata.normalize("NFKD", raw.strip().lower())
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    return _LOCAL_ACTION_MAP.get(" ".join(stripped.split()), "")


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
        return ""  # internal Google link – discard
    if href.startswith(("http://", "https://")):
        return href.strip()
    return ""
