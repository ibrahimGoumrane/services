"""Page scraping utilities using NoDriver"""

import re
from typing import Optional, List, Tuple
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
from bs4 import BeautifulSoup


load_dotenv()

from .nodriver import NoDriverDriver
from .geo_extractor import extract_location_city_country
from .url_utils import normalize_url, validate_website_http
from .email_extractors import extract_emails_from_text
from .logging_config import get_logger


logger = get_logger("dbSeeder.web_scraper")

PHONE_REGEX = re.compile(r"(?<!\d)(?!\d{4}[-/]\d{2}[-/]\d{2}\b)(?:\+?\d[\d\s().\-/]{6,}\d)(?!\d)")


class PageScraper:
    """Handles web page scraping with nodriver"""
    
    def __init__(
        self,
        driver: NoDriverDriver,
        excluded_domains: Optional[List[str]] = None,
        prevalidate_http: bool = False,
        site_timeout_seconds: int = 30,
    ):
        """
        Initialize page scraper.
        
        Args:
            driver: NoDriverDriver instance
            excluded_domains: Domains blocked from browsing
            prevalidate_http: When True, run HTTP validation before browser navigation.
                Keep False to avoid excessive extra HTTP requests.
        """
        self.driver = driver
        self.excluded_domains = excluded_domains or []
        self.prevalidate_http = prevalidate_http
        self.site_timeout_seconds = site_timeout_seconds
    
    def accept_cookies(self) -> bool:
        """Try to accept cookie consent banners"""
        try:
            script = """
(() => {
  const keywords = ['accept all', 'accept cookies', 'i accept', 'i agree', 'agree', 'got it', 'ok'];
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
                logger.info("✓ Accepted cookie consent")
                self.driver.sleep(1.0)
                return True
        
        except Exception as e:
            logger.debug(f"No cookie banner found: {e}")
        
        return False
    


    def _dedupe_preserve_order(self, values: List[str]) -> List[str]:
        seen = set()
        unique_values: List[str] = []
        for value in values:
            key = (value or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique_values.append((value or "").strip())
        return unique_values

    def _extract_phone_numbers_from_text(self, text: str) -> List[str]:
        if not text:
            return []

        candidates = PHONE_REGEX.findall(text)
        normalized: List[str] = []
        for candidate in candidates:
            raw = (candidate or "").strip()
            if not raw:
                continue

            compact = re.sub(r"[^\d+]", "", raw)
            digits = re.sub(r"\D", "", compact)
            if len(digits) < 7 or len(digits) > 15:
                continue

            if compact.startswith("+"):
                normalized.append(f"+{digits}")
            else:
                normalized.append(digits)

        return self._dedupe_preserve_order(normalized)

    def _extract_visible_text(self, html: str) -> str:
        """Extract user-visible text only, ignoring script/style/non-content nodes."""
        if not html:
            return ""

        soup = BeautifulSoup(html, "html.parser")
        for node in soup(["script", "style", "noscript", "svg"]):
            node.decompose()

        return soup.get_text(" ", strip=True)

    def _extract_contact_links_from_home_html(self, base_url: str, html: str) -> List[str]:
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        candidates: List[str] = []

        for node in soup.select("a[href]"):
            href = (node.get("href") or "").strip()
            if not href:
                continue

            href_lower = href.lower()
            if "contact" not in href_lower:
                continue

            if href_lower.startswith("mailto:") or href_lower.startswith("tel:"):
                continue

            if href_lower.startswith("javascript:") or href.startswith("#"):
                continue

            absolute_url = urljoin(base_url, href)
            parsed = urlparse(absolute_url)
            if parsed.scheme not in {"http", "https"}:
                continue

            normalized = normalize_url(absolute_url)
            if normalized:
                candidates.append(normalized)

        return self._dedupe_preserve_order(candidates)



    def _scrape_page_contact_data(self, url: str) -> Tuple[List[str], List[str], str]:
        logger.info(f"[scrape] start url={url}")
        # ALWAYS pre-validate before browser navigation to avoid hanging on anti-bot responses (e.g., 999)
        if not validate_website_http(
            url,
            timeout=3,
            excluded_domains=self.excluded_domains,
        ):
            logger.warning(f"✗ Website returned status 999: {url}")
            return [], [], ""

        logger.info(f"[scrape] http ok, navigating url={url}")
        self.driver.get(url, timeout_seconds=self.site_timeout_seconds)
        logger.info(f"[scrape] navigation complete url={url}")
        self.driver.sleep(1.0)

        current_url = self.driver.current_url
        logger.debug(f"[scrape] current_url={current_url}")
        
        logger.info(f"[scrape] accepting cookies url={url}")
        self.accept_cookies()
        self.driver.sleep(1.0)

        logger.info(f"[scrape] reading content url={url}")
        page_html = self.driver.get_content(timeout_seconds=self.site_timeout_seconds)
        logger.info(f"[scrape] content length={len(page_html or '')} url={url}")
        emails = self._dedupe_preserve_order(extract_emails_from_text(page_html))
        visible_text = self._extract_visible_text(page_html)
        phones = self._extract_phone_numbers_from_text(visible_text)
        logger.info(f"[scrape] done url={url} emails={len(emails)} phones={len(phones)}")
        return emails, phones, page_html
    
    def find_contact_page(self, base_url: str) -> Optional[str]:
        """
        Try to find a contact page on the website.
        
        Args:
            base_url: Base website URL
        
        Returns:
            Contact page URL if found, None otherwise
        """
        base_url = normalize_url(base_url)

        try:
            _, _, homepage_html = self._scrape_page_contact_data(base_url)
            discovered = self._extract_contact_links_from_home_html(base_url, homepage_html)
            for contact_url in discovered:
                if validate_website_http(
                    contact_url,
                    timeout=2,
                    excluded_domains=self.excluded_domains,
                ):
                    logger.info(f"✓ Found contact page from homepage href: {contact_url}")
                    return contact_url
        except Exception:
            pass

        logger.debug("No contact page found")
        return None

    def find_contact_info_on_website(
        self,
        website_url: str,
    ) -> Tuple[List[str], List[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Find emails, phones, and a contact page URL in a single crawl flow.

        Rules:
        - Scrape homepage first.
        - If both email and phone are present on homepage, skip contact-page crawl.
        - Otherwise crawl homepage-discovered contact links to fill missing values.
        """
        if not website_url:
            return [], [], None, None, None, None

        website_url = normalize_url(website_url)
        logger.info(f"[contact-flow] start website_url={website_url}")
        all_emails: List[str] = []
        all_phones: List[str] = []
        selected_contact_page: Optional[str] = None
        extracted_location: Optional[str] = None
        extracted_city: Optional[str] = None
        extracted_country: Optional[str] = None

        try:
            logger.info(f"[contact-flow] scraping homepage website_url={website_url}")
            homepage_emails, homepage_phones, homepage_html = self._scrape_page_contact_data(website_url)
            all_emails.extend(homepage_emails)
            all_phones.extend(homepage_phones)
            logger.info(
                f"[contact-flow] homepage result website_url={website_url} emails={len(homepage_emails)} phones={len(homepage_phones)} html_chars={len(homepage_html or '')}"
            )

            logger.info(f"[contact-flow] geo extract homepage website_url={website_url}")
            loc, city, country = extract_location_city_country(page_url=website_url, html=homepage_html)
            extracted_location = extracted_location or loc
            extracted_city = extracted_city or city
            extracted_country = extracted_country or country
            logger.info(
                f"[contact-flow] homepage geo website_url={website_url} location={extracted_location!r} city={extracted_city!r} country={extracted_country!r}"
            )

            contact_candidates = self._extract_contact_links_from_home_html(website_url, homepage_html)
            logger.info(f"[contact-flow] contact candidates found={len(contact_candidates)} website_url={website_url}")
            for candidate in contact_candidates:
                logger.debug(f"[contact-flow] validating contact candidate={candidate}")
                if validate_website_http(
                    candidate,
                    timeout=2,
                    excluded_domains=self.excluded_domains,
                ):
                    selected_contact_page = candidate
                    logger.info(f"[contact-flow] selected contact page={selected_contact_page}")
                    break

            has_email = bool(all_emails)
            has_phone = bool(all_phones)
            if has_email and has_phone:
                logger.info("✓ Homepage already has both email and phone; skipping contact-page crawl")
            else:
                logger.debug("At least one value missing on homepage; checking contact pages")
                for contact_url in contact_candidates:
                    try:
                        logger.info(f"[contact-flow] scraping contact page={contact_url}")
                        if not validate_website_http(
                            contact_url,
                            timeout=2,
                            excluded_domains=self.excluded_domains,
                        ):
                            logger.info(f"[contact-flow] contact page rejected by validator={contact_url}")
                            continue

                        if not selected_contact_page:
                            selected_contact_page = contact_url

                        contact_emails, contact_phones, contact_html = self._scrape_page_contact_data(contact_url)
                        all_emails.extend(contact_emails)
                        all_phones.extend(contact_phones)
                        logger.info(
                            f"[contact-flow] contact page result url={contact_url} emails={len(contact_emails)} phones={len(contact_phones)} html_chars={len(contact_html or '')}"
                        )

                        if not (extracted_location and extracted_city and extracted_country):
                            logger.info(f"[contact-flow] geo extract contact page={contact_url}")
                            loc2, city2, country2 = extract_location_city_country(page_url=contact_url, html=contact_html)
                            extracted_location = extracted_location or loc2
                            extracted_city = extracted_city or city2
                            extracted_country = extracted_country or country2
                            logger.info(
                                f"[contact-flow] contact geo url={contact_url} location={extracted_location!r} city={extracted_city!r} country={extracted_country!r}"
                            )

                        has_email = bool(all_emails)
                        has_phone = bool(all_phones)
                        if has_email and has_phone:
                            break
                    except Exception:
                        logger.exception(f"[contact-flow] contact page scrape failed url={contact_url}")
                        continue

            all_emails = self._dedupe_preserve_order(all_emails)
            all_phones = self._dedupe_preserve_order(all_phones)
            logger.info(
                f"[contact-flow] done website_url={website_url} emails={len(all_emails)} phones={len(all_phones)} contact_page={selected_contact_page!r} location={extracted_location!r} city={extracted_city!r} country={extracted_country!r}"
            )
            return (
                all_emails,
                all_phones,
                selected_contact_page,
                extracted_location,
                extracted_city,
                extracted_country,
            )

        except Exception as exc:
            logger.error(f"Error finding contact info on website: {exc}")
            return [], [], None, None, None, None
        finally:
            try:
                logger.info(f"[contact-flow] resetting browser to about:blank website_url={website_url}")
                self.driver.get("about:blank")
            except Exception:
                pass

    
