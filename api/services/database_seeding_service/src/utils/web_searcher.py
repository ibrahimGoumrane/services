"""Google search automation utilities"""

import logging
import time
import random
import re
from urllib.parse import quote_plus, urlparse, parse_qs
from typing import Tuple, Optional, List

from .url_utils import is_pdf_url, is_excluded_domain, validate_website_http
from .web_scraper import NoDriverDriver


logger = logging.getLogger(__name__)


class GoogleSearcher:
    """Handles Google search automation with anti-bot measures"""
    
    def __init__(self, driver: NoDriverDriver, excluded_domains: Optional[List[str]] = None):
        """
        Initialize Google searcher.
        
        Args:
            driver: NoDriverDriver instance
            excluded_domains: Domains to exclude from search results
        """
        self.driver = driver
        self.excluded_domains = excluded_domains or []
    
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

                # Try results until one validates
                for idx, url in enumerate(valid_results, 1):
                    logger.info(f"Trying result #{idx}: {url}")

                    if validate_website_http(url):
                        logger.info(f"✓ Result #{idx} is valid")
                        return url, True

                    logger.warning(f"✗ Result #{idx} failed validation")

                logger.warning("⚠️ Returning first result despite validation failure")
                return valid_results[0], False
            
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
        """Extract candidate target URLs from Google result HTML."""
        seen = set()
        urls: List[str] = []

        for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
            href = (href or "").strip()
            if not href:
                continue

            candidate = ""
            if href.startswith("/url?"):
                parsed = parse_qs(urlparse(href).query)
                candidate = parsed.get("q", [""])[0]
            elif href.startswith("http"):
                candidate = href

            if not candidate:
                continue

            candidate_lower = candidate.lower()
            if "google.com" in candidate_lower:
                continue
            if is_pdf_url(candidate):
                continue
            if is_excluded_domain(candidate, self.excluded_domains):
                continue
            if candidate in seen:
                continue

            seen.add(candidate)
            urls.append(candidate)

        return urls
