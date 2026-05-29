"""Phone number extraction from text utilities using the phonenumbers library."""

from api.services.utils.log_socket import get_seeding_logger
import os
from typing import List, Optional

import phonenumbers
from bs4 import BeautifulSoup

from dotenv import load_dotenv
load_dotenv()

logger = get_seeding_logger()

_FALLBACK_REGIONS = "US,GB,FR,DE,CA,AU,NL,BE,CH,SA,AE,QA,KW,BH,OM,EG,JO,LB,MA,TN,DZ"
DEFAULT_REGIONS = [
    r.strip()
    for r in os.getenv("PHONE_EXTRACTION_REGIONS", _FALLBACK_REGIONS).split(",")
    if r.strip()
]


_ARABIC_TO_WESTERN = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _normalize_numerals(text: str) -> str:
    """Convert Arabic (Eastern) numerals to Western numerals for phone parsing."""
    if not text:
        return text
    return text.translate(_ARABIC_TO_WESTERN)


def _visible_text(html: str) -> str:
    """Strip non-visible nodes, convert arabic numerals, and return plain text."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    raw = soup.get_text(" ", strip=True)
    return _normalize_numerals(raw)


def extract_phones_from_text(html: Optional[str]) -> List[str]:
    """
    Extract phone numbers from raw text or HTML and return them in
    **international format** (e.g. ``+1 234 567 8901``).

    Args:
        html: HTML to search.

    Returns:
        List of deduplicated, internationally-formatted phone numbers.
    """
    if not html or not isinstance(html, str):
        return []

    # If it looks like HTML, convert to visible text first
    if "<" in html:
        text = _visible_text(html)
    else:
        text = html
    results: set = set()

    for region in DEFAULT_REGIONS:
        for match in phonenumbers.PhoneNumberMatcher(text, region):
            phone = match.number

            if not phonenumbers.is_valid_number(phone):
                continue
            
            try:
                fmt = phonenumbers.format_number(
                    phone, phonenumbers.PhoneNumberFormat.E164
                )
            except phonenumbers.NumberParseException:
                continue
            results.add(fmt)

    return list(results)
