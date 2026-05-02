"""Social-media and communication link extraction from raw HTML."""

import logging
import re
from typing import Dict, List, Optional, Set

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL patterns per platform
# ---------------------------------------------------------------------------
_SOCIAL_PATTERNS = {
    "whatsapp": [
        r"https?://(?:www\.)?wa\.me/[\w+]+",
        r"https?://(?:www\.)?api\.whatsapp\.com/send\?phone=[\w+&=]+",
        r"https?://(?:www\.)?whatsapp\.com/[\w/?=&.-]+",
    ],
    "facebook": [
        r"https?://(?:www\.|m\.)?facebook\.com/[\w./?=&@-]+",
        r"https?://(?:www\.)?fb\.com/[\w./?=&-]+",
        r"https?://(?:www\.)?fb\.me/[\w./?=&-]+",
    ],
    "instagram": [
        r"https?://(?:www\.)?instagram\.com/[\w./?=&-]+",
        r"https?://(?:www\.)?instagr\.am/[\w./?=&-]+",
    ],
    "tiktok": [
        r"https?://(?:www\.)?tiktok\.com/@[\w.]+",
        r"https?://(?:www\.)?tiktok\.com/[\w./?=&-]+",
        r"https?://vm\.tiktok\.com/[\w]+",
    ],
    "linkedin": [
        r"https?://(?:www\.)?linkedin\.com/in/[\w-]+",
        r"https?://(?:www\.)?linkedin\.com/company/[\w-]+",
        r"https?://(?:www\.)?linkedin\.com/pub/[\w/-]+",
    ],
    "youtube": [
        r"https?://(?:www\.)?youtube\.com/(?:channel|c|user|@)[\w/?=&-]+",
        r"https?://(?:www\.)?youtube\.com/watch\?v=[\w-]+",
        r"https?://youtu\.be/[\w-]+",
    ],
    "telegram": [
        r"https?://(?:www\.)?t\.me/[\w+]+",
        r"https?://(?:www\.)?telegram\.me/[\w+]+",
        r"https?://(?:www\.)?telegram\.dog/[\w+]+",
    ],
    "calendly": [
        r"https?://(?:www\.)?calendly\.com/[\w/-]+",
    ],
}

_COMPILED_PATTERNS: Dict[str, List[re.Pattern]] = {
    platform: [re.compile(p, re.IGNORECASE) for p in patterns]
    for platform, patterns in _SOCIAL_PATTERNS.items()
}

# Attributes that commonly contain external URLs.
_URL_ATTRS = {
    "href",
    "src",
    "data-href",
    "data-url",
    "data-link",
    "data-action",
    "action",
    "content",
}

# Characters that may trail a URL when it lives inside HTML markup.
_TRAILING_JUNK = '"\'>;) '


def _scan_text(text: str) -> Dict[str, Set[str]]:
    """Run all compiled regexes against a plain string."""
    matches: Dict[str, Set[str]] = {p: set() for p in _COMPILED_PATTERNS}
    for platform, patterns in _COMPILED_PATTERNS.items():
        for pat in patterns:
            for m in pat.finditer(text):
                url = m.group(0).rstrip(_TRAILING_JUNK)
                if url:
                    matches[platform].add(url)
    return matches


def extract_social_links(html: Optional[str]) -> Dict[str, List[str]]:
    """
    Extract social-media and communication links from raw HTML.

    The function searches:

    1. The **raw HTML string** – catches URLs inside JSON blobs, inline
       JavaScript, CSS, or text nodes.
    2. Parsed **tag attributes** (``href``, ``src``, ``data-url``, etc.) –
       handles HTML-entities (``&amp;`` → ``&``) automatically.

    Args:
        html: Raw HTML string.

    Returns:
        Mapping ``{platform: [unique_url, ...]}``. Platforms with no
        matches are omitted from the result dict.
    """
    if not html or not isinstance(html, str):
        return {}

    # ------------------------------------------------------------------
    # 1. Raw HTML scan
    # ------------------------------------------------------------------
    result = _scan_text(html)

    # ------------------------------------------------------------------
    # 2. Parsed-attribute scan (BeautifulSoup)
    # ------------------------------------------------------------------
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(True):  # iterate over every tag
            for attr in _URL_ATTRS:
                value = tag.get(attr)
                if not value or not isinstance(value, str):
                    continue
                partial = _scan_text(value)
                for platform, urls in partial.items():
                    result[platform].update(urls)
    except Exception as exc:
        logger.warning(f"BeautifulSoup parsing failed during social extraction: {exc}")

    return result
