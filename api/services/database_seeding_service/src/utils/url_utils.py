"""URL validation and processing utilities"""

import re
from urllib.parse import urlparse
import requests
import logging


logger = logging.getLogger(__name__)


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


def is_pdf_url(url: str) -> bool:
    """
    Check if URL points to a PDF file.
    
    Args:
        url: URL string to check
    
    Returns:
        True if URL is a PDF, False otherwise
    """
    if not url:
        return False
    
    url_lower = url.lower()
    
    # Check file extension in URL
    if url_lower.endswith('.pdf'):
        return True
    
    # Check if .pdf appears in the path
    parsed = urlparse(url_lower)
    if '.pdf' in parsed.path:
        return True
    
    return False


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
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace('www.', '')
        
        for excluded in excluded_domains:
            excluded_clean = str(excluded).lower().replace('www.', '').strip()
            
            if domain == excluded_clean or domain.endswith('.' + excluded_clean):
                logger.debug(f"Domain {domain} matches excluded domain {excluded_clean}")
                return True
        
        return False
    except Exception as e:
        logger.error(f"Error checking excluded domain for {url}: {e}")
        return False


def validate_website_http(url: str, timeout: int = 3, allow_pdf: bool = False) -> bool:
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
    
    # Check if URL is a PDF file
    if is_pdf_url(url) and not allow_pdf:
        logger.warning(f"✗ Skipping PDF file: {url}")
        return False
    
    # Normalize URL
    url = normalize_url(url)
    
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
        
        # Check if response is PDF content
        content_type = response.headers.get('Content-Type', '').lower()
        if 'application/pdf' in content_type and not allow_pdf:
            logger.warning(f"✗ Skipping PDF content: {url}")
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
