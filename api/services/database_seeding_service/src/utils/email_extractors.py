"""Email extraction from text utilities"""

import re
from typing import List, Optional
import logging


logger = logging.getLogger(__name__)


# Email regex pattern
EMAIL_PATTERN = re.compile(
    r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
)

# File extensions that should not be matched as emails
EXCLUDED_EXTENSIONS = (
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico',
    '.tiff', '.tif', '.avif',  # images
    '.woff', '.woff2', '.ttf', '.eot', '.otf',  # fonts
    '.css', '.js', '.json', '.xml', '.map',  # web assets
    '.pdf', '.doc', '.docx', '.xls', '.xlsx',  # documents
    '.zip', '.gz', '.tar', '.rar',  # archives
    '.mp3', '.mp4', '.avi', '.mov', '.wav',  # media
)


def extract_emails_from_text(text: Optional[str]) -> List[str]:
    """
    Extract email addresses from text using regex.
    
    Filters out:
    - Common false positives (file extensions, web assets)
    - Duplicates
    
    Args:
        text: Text to extract emails from
    
    Returns:
        List of unique email addresses
    """
    if not text:
        return []
    
    # Find all email patterns
    emails = EMAIL_PATTERN.findall(text)
    
    # Filter out false positives (file extensions)
    emails = [
        email for email in emails
        if not email.lower().endswith(EXCLUDED_EXTENSIONS)
    ]
    
    # Remove duplicates while preserving order
    seen = set()
    unique_emails = []
    for email in emails:
        email_lower = email.lower()
        if email_lower not in seen:
            seen.add(email_lower)
            unique_emails.append(email)
    
    if len(unique_emails) < len(emails):
        logger.debug(f"Removed {len(emails) - len(unique_emails)} duplicate email(s)")
    
    return unique_emails


def is_valid_email_format(email: str) -> bool:
    """
    Check if an email has valid format (contains @).
    
    Args:
        email: Email address to validate
    
    Returns:
        True if email format is valid, False otherwise
    """
    if not email:
        return False
    
    return '@' in email and EMAIL_PATTERN.match(email) is not None
