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


class GoogleSearcher:
    """Handles Google search automation with anti-bot measures"""
    
    def __init__(
        self,
        driver: NoDriverDriver,
        excluded_domains: Optional[List[str]] = None,
        generic_domains: Optional[List[str]] = None,
        site_timeout_seconds: int = 30,
    ):
        """
        Initialize Google searcher.
        
        Args:
            driver: NoDriverDriver instance
            excluded_domains: Domains to exclude from search results
            generic_domains: Generic domains to skip in search candidates
        """
        self.driver = driver
        self.excluded_domains = excluded_domains or []
        self.generic_domains = generic_domains or []
        self.site_timeout_seconds = site_timeout_seconds
    
    def search(
        self,
        business_name: str,
        location: Optional[str] = None,
        max_retries: int = 2
    ) -> Tuple[Optional[str], bool]:
        """
        Google search for a business and return first valid result.
        
        Args:
            business_name: Business name to search for
            location: Optional location for more specific search
            max_retries: Number of retries on timeout
        
        Returns:
            Tuple of (url, is_valid) where is_valid indicates if URL passed validation
        """
        for attempt in range(max_retries):
            try:
                search_query = f"'{business_name}' '{location}'." if location else f"'{business_name}'"
                logger.info(f"Googling: {search_query} (Attempt {attempt + 1}/{max_retries})")

                search_url = f"https://www.google.com/search?q={quote_plus(search_query)}"
                self.driver.get(search_url, timeout_seconds=self.site_timeout_seconds)
                self.driver.sleep(random.uniform(1.0, 1.5))

                self._accept_google_cookies()
                time.sleep(random.uniform(0.8, 1.2))

                time.sleep(random.uniform(1.8, 2.5))
                html = self.driver.get_content(timeout_seconds=self.site_timeout_seconds)
                valid_results = self._extract_google_result_urls(html)

                if not valid_results:
                    logger.warning("No valid Google results found")
                    return None, False

                # Extractor returns candidates already validated by validate_website_http.
                for idx, url in enumerate(valid_results, 1):
                    logger.info(f"Trying result #{idx}: {url}")

                    logger.info(f"✓ Result #{idx} is valid")
                    return url, True

                logger.warning("No valid website candidate passed HTTP validation")
                return None, False
            
            except Exception as e:
                logger.error(f"Error in Google search (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                        logger.warning("⚠️ Timeout detected, restarting driver...")
                        try:
                            self.driver.restart(reason="health")
                            logger.info("✅ Driver restarted")
                        except Exception as restart_error:
                            logger.error(f"Failed to restart driver: {restart_error}")
                            return None, False
                    time.sleep(2)
                    continue
                else:
                    logger.error(f"Max retries ({max_retries}) reached")
                    return None, False
        
        return None, False
    
    def _accept_google_cookies(self) -> None:
        """Accept Google's cookie banner if present"""
        try:
            script = """
(() => {
  const keywords = ['accept all', 'accept', 'i agree', 'agree'];
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
                logger.info("✓ Accepted Google cookies")
                self.driver.sleep(1.0)
        except Exception as e:
            logger.debug(f"No Google cookie banner found: {e}")

    def _extract_google_result_urls(self, html: str) -> List[str]:
        """
        Extract the first HTTP-validated external website URL from Google results.
        Priority order:
        1) Local actions panel Website (only if div.zhZ3gf exists)
        2) Organic result containers (.A6K0A) with primary anchor (jsname="UWckNb")
        """
        soup = BeautifulSoup(html or "", "html.parser")
        excluded_for_validation = list(self.excluded_domains) + list(self.generic_domains)

        # Strict gate: never parse local actions unless the local panel root exists.
        local_panel_root = soup.select_one("div.zhZ3gf")
        if local_panel_root:
            local_actions = self._extract_local_panel_actions(local_panel_root)
            logger.debug(f"Local actions detected: {sorted(local_actions.keys())}")

            local_website = local_actions.get("website", "")
            if local_website:
                if validate_website_http(local_website, excluded_domains=excluded_for_validation):
                    logger.info(f"Using local panel website candidate: {local_website}")
                    return [local_website]
                logger.debug(f"Local panel website candidate failed validation: {local_website}")
            else:
                logger.debug("Local panel found but no Website action URL extracted")
        else:
            logger.debug("Local panel root div.zhZ3gf not found; skipping local action parsing")

        for container in soup.select(".A6K0A"):
            anchor = container.select_one('a[jsname="UWckNb"]')
            if not anchor:
                continue

            href = (anchor.get("href") or "").strip()
            candidate = self._normalize_google_href(href)
            if not candidate:
                continue

            if not validate_website_http(candidate, excluded_domains=excluded_for_validation):
                continue

            return [candidate]

        return []

    def _extract_local_panel_actions(self, local_panel_root) -> Dict[str, str]:
        """Extract available local panel actions and associated values/URLs."""
        actions: Dict[str, str] = {}

        for action_block in local_panel_root.select("div.bkaPDb"):
            label_node = action_block.select_one("span.aSAiSd")
            label = (label_node.get_text(" ", strip=True).lower() if label_node else "")
            action_key = self._normalize_local_action_label(label)
            if not action_key:
                continue

            if action_key == "call":
                phone_anchor = action_block.select_one("[data-phone-number]")
                phone_number = (
                    (phone_anchor.get("data-phone-number") if phone_anchor else "") or ""
                ).strip()
                if phone_number:
                    actions[action_key] = phone_number
                continue

            link_node = action_block.select_one("a[href]")
            href = ((link_node.get("href") if link_node else "") or "").strip()
            if not href:
                continue

            if action_key == "website":
                candidate = self._normalize_google_href(href)
                if candidate:
                    actions[action_key] = candidate
                continue

            if href.startswith("http://") or href.startswith("https://"):
                actions[action_key] = href
            elif href.startswith("/"):
                actions[action_key] = f"https://www.google.com{href}"

        return actions

    def _normalize_local_action_label(self, label: str) -> str:
        """Map local panel action label text to stable internal keys."""
        normalized = self._canonicalize_local_action_label(label)
        mapping = {
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
        return mapping.get(normalized, "")

    def _canonicalize_local_action_label(self, label: str) -> str:
        """Normalize action labels across locales (spacing/case/accents)."""
        normalized = (label or "").strip().lower()
        normalized = unicodedata.normalize("NFKD", normalized)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = " ".join(normalized.split())
        return normalized

    def _normalize_google_href(self, href: str) -> str:
        """Normalize Google SERP hrefs to direct target URLs."""
        if not href:
            return ""

        if href.startswith("//"):
            return f"https:{href}".strip()

        if href.startswith("/url?") or href.startswith("./url?"):
            parsed = parse_qs(urlparse(href).query)
            # Google can use q= or url= depending on page variant.
            candidate = (parsed.get("q", [""])[0] or "").strip()
            if not candidate:
                candidate = (parsed.get("url", [""])[0] or "").strip()
            return candidate

        if href.startswith("/"):
            # Ignore internal Google relative links.
            return ""

        if href.startswith("http://") or href.startswith("https://"):
            return href.strip()

        return ""
