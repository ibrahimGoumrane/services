"""URL validation and processing utilities"""

import re
from urllib.parse import urlparse
import requests
import logging


logger = logging.getLogger(__name__)


DOWNLOADABLE_FILE_EXTENSIONS = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".ods",
    ".csv",
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
)


def extract_domain(url: str) -> str:
    """Extract a normalized registrable domain string from URL-like input."""
    if not url:
        return ""

    candidate = url.strip()
    if not candidate:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", candidate):
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    netloc = parsed.netloc or parsed.path.split("/", 1)[0]
    netloc = netloc.split("@")[-1].split(":", 1)[0].lower().strip().strip(".")
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def normalize_url(url: str) -> str:
    """
    Normalize a URL by ensuring it has a protocol (https by default).
    
    Args:
        url: URL string
    
    Returns:
        URL with protocol
    """
    if not url:
        return url
    
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    return url


def is_downloadable_file_url(url: str) -> bool:
    """Return True when URL path points to a non-HTML downloadable file."""
    if not url:
        return False

    parsed = urlparse(normalize_url(url))
    path = (parsed.path or "").lower()
    return any(path.endswith(ext) for ext in DOWNLOADABLE_FILE_EXTENSIONS)


def is_excluded_domain(url: str, excluded_domains: list) -> bool:
    """
    Check if a URL's domain is in the excluded domains list.
    
    Args:
        url: URL to check
        excluded_domains: List of excluded domain names
    
    Returns:
        True if domain is excluded, False otherwise
    """
    if not url or not excluded_domains:
        return False

    try:
        domain = extract_domain(url)
        if not domain:
            return False

        for excluded in excluded_domains:
            excluded_clean = extract_domain(str(excluded))
            if not excluded_clean:
                continue

            if domain == excluded_clean or domain.endswith('.' + excluded_clean):
                logger.debug(f"Domain {domain} matches excluded domain {excluded_clean}")
                return True

        return False
    except Exception as e:
        logger.error(f"Error checking excluded domain for {url}: {e}")
        return False


def validate_website_http(
    url: str,
    timeout: int = 3,
    allow_pdf: bool = False,
    excluded_domains: list[str] | None = None,
) -> bool:
    """
    Check if a website is accessible via HTTP GET request.
    
    Args:
        url: Website URL to check
        timeout: Request timeout in seconds
        allow_pdf: Whether to consider PDF content as valid
    
    Returns:
        True if website is accessible, False otherwise
    """
    if not url:
        return False
    
    if excluded_domains and is_excluded_domain(url, excluded_domains):
        logger.warning(f"✗ Skipping excluded domain: {url}")
        return False

    url = normalize_url(url)

    # Check if URL is a downloadable file
    if is_downloadable_file_url(url) and not allow_pdf:
        logger.warning(f"✗ Skipping downloadable file URL: {url}")
        return False

    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)

        final_url = response.url or url
        if excluded_domains and is_excluded_domain(final_url, excluded_domains):
            logger.warning(f"✗ Redirected to excluded domain: {final_url}")
            return False

        if is_downloadable_file_url(final_url) and not allow_pdf:
            logger.warning(f"✗ Redirected to downloadable file: {final_url}")
            return False

        # Keep only HTML pages for scraping when file downloads are not allowed.
        content_type = response.headers.get('Content-Type', '').lower()
        if not allow_pdf and content_type and 'text/html' not in content_type:
            logger.warning(f"✗ Skipping non-HTML content: {final_url} ({content_type})")
            return False
        
        if response.status_code == 200:
            logger.info(f"✓ Website accessible: {url}")
            return True
        else:
            logger.warning(f"✗ Website returned status {response.status_code}: {url}")
            return False
    
    except requests.exceptions.Timeout:
        logger.warning(f"✗ Website timeout: {url}")
        return False
    except requests.exceptions.ConnectionError:
        logger.warning(f"✗ Website connection error: {url}")
        return False
    except Exception as e:
        logger.warning(f"✗ Website error: {url} - {e}")
        return False
