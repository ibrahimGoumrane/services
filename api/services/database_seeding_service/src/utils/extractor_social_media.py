"""Social-media and communication link extraction from raw HTML."""

from api.services.utils.log_socket import get_seeding_logger
import re
from typing import Dict, List, Optional, Set

import html as html_lib

logger = get_seeding_logger()

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
        r"https?://(?:[\w-]+\.)*linkedin\.com/in/[\w-]+",
        r"https?://(?:[\w-]+\.)*linkedin\.com/company/[\w-]+",
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


def extract_social_links(html: Optional[str]) -> Dict[str, Set[str]]:
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

    decoded = html_lib.unescape(html)
    result = _scan_text(decoded)

    return {k: v for k, v in result.items() if len(v) > 0}


def extract_social_links_from_urls(urls: List[str]) -> Dict[str, Set[str]]:
    """
    Extract social-media URLs from a list of absolute URLs.

    This is useful when Google organic results contain social links that
    would otherwise be discarded by domain exclusion filters.

    Args:
        urls: List of URLs (e.g. from a Google search result).

    Returns:
        Same shape as ``extract_social_links`` — ``{platform: {url, ...}}``.
    """
    if not urls:
        return {}
    text = " ".join(str(u) for u in urls if u)
    result = _scan_text(text)
    # Remove empty platforms
    return {k: v for k, v in result.items() if len(v) > 0}

