"""Email extraction from text utilities"""

from api.services.utils.log_socket import get_seeding_logger
import re
from typing import List, Optional

logger = get_seeding_logger()


# Email regex pattern
EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

# File extensions that should not be matched as emails
EXCLUDED_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".bmp",
    ".ico",
    ".tiff",
    ".tif",
    ".avif",  # images
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",  # fonts
    ".css",
    ".js",
    ".json",
    ".xml",
    ".map",  # web assets
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",  # documents
    ".zip",
    ".gz",
    ".tar",
    ".rar",  # archives
    ".mp3",
    ".mp4",
    ".avi",
    ".mov",
    ".wav",  # media
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
        email for email in emails if not email.lower().endswith(EXCLUDED_EXTENSIONS)
    ]

    # Remove duplicates while preserving order
    seen = set()
    unique_emails = []
    for email in emails:
        email_lower = email.lower()
        if email_lower not in seen:
            seen.add(email_lower)
            unique_emails.append(email)

    return unique_emails
