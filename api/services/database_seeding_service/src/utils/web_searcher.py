"""Google search automation utilities"""

import logging
import time
import random
from urllib.parse import quote_plus, urlparse, parse_qs
from typing import Tuple, Optional, List
from bs4 import BeautifulSoup

from .url_utils import is_pdf_url, is_excluded_domain, validate_website_http
from .web_scraper import NoDriverDriver


logger = logging.getLogger(__name__)


class GoogleSearcher:
    """Handles Google search automation with anti-bot measures"""
    
    def __init__(
        self,
        driver: NoDriverDriver,
        excluded_domains: Optional[List[str]] = None,
        generic_domains: Optional[List[str]] = None,
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
                self.driver.get(search_url)
                self.driver.sleep(random.uniform(1.0, 1.5))

                self._accept_google_cookies()
                time.sleep(random.uniform(0.8, 1.2))

                time.sleep(random.uniform(1.8, 2.5))
                html = self.driver.get_content()
                valid_results = self._extract_google_result_urls(html)

                if not valid_results:
                    logger.warning("No valid Google results found")
                    return None, False

                # Try candidates until one validates (normally one candidate, first valid top-to-bottom).
                for idx, url in enumerate(valid_results, 1):
                    logger.info(f"Trying result #{idx}: {url}")

                    if validate_website_http(url):
                        logger.info(f"✓ Result #{idx} is valid")
                        return url, True

                    logger.warning(f"✗ Result #{idx} failed validation")

                logger.warning("No valid website candidate passed HTTP validation")
                return None, False
            
            except Exception as e:
                logger.error(f"Error in Google search (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    if "timed out" in str(e).lower() or "timeout" in str(e).lower():
                        logger.warning("⚠️ Timeout detected, restarting driver...")
                        try:
                            self.driver.restart()
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
        Extract the first valid external website URL from Google results.

        Target selector order:
        - top-to-bottom result containers
        - first anchor matching .A6K0A a[jsname="UWckNb"]
        """
        soup = BeautifulSoup(html or "", "html.parser")
        urls: List[str] = []

        containers = soup.select(".A6K0A")
        for container in containers:
            # Not always a direct child; search the full subtree inside each result container.
            anchor = container.select_one('a[jsname="UWckNb"]')
            if not anchor:
                anchor = container.select_one("a.zReHs")
            if not anchor:
                continue

            href = (anchor.get("href") or "").strip()
            candidate = self._normalize_google_href(href)
            if not candidate:
                continue
            if self._should_skip_candidate(candidate):
                continue

            # Stop at first valid external link as requested.
            urls.append(candidate)
            return urls

        # Fallback 1: Google markup can vary; try global selector while preserving order.
        for anchor in soup.select('a[jsname="UWckNb"], a.zReHs'):
            href = (anchor.get("href") or "").strip()
            candidate = self._normalize_google_href(href)
            if not candidate:
                continue
            if self._should_skip_candidate(candidate):
                continue
            urls.append(candidate)
            return urls

        # Fallback 2: broader result links in Google blocks (still top-to-bottom first valid).
        for anchor in soup.select("div.g a[href], .tF2Cxc a[href], .MjjYud a[href]"):
            href = (anchor.get("href") or "").strip()
            candidate = self._normalize_google_href(href)
            if not candidate:
                continue
            if self._should_skip_candidate(candidate):
                continue
            urls.append(candidate)
            return urls

        return urls

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

    def _should_skip_candidate(self, candidate: str) -> bool:
        """Apply domain and content filtering rules for extracted links."""
        if not candidate:
            return True

        try:
            parsed = urlparse(candidate)
            netloc = (parsed.netloc or "").lower().replace("www.", "")
            normalized_generic_domains = {d.lower().replace("www.", "") for d in self.generic_domains}

            if any(netloc == domain or netloc.endswith(f".{domain}") for domain in normalized_generic_domains):
                return True

            if "google." in netloc or netloc.startswith("maps.google"):
                return True

            if is_pdf_url(candidate):
                return True

            if is_excluded_domain(candidate, self.excluded_domains):
                return True

            return False
        except Exception:
            return True
