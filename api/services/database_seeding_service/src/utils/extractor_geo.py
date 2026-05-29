"""Geolocation extraction using locationtagger NER."""
import logging
import re
from typing import Optional
from collections import Counter
from bs4 import BeautifulSoup

import nltk
from api.services.utils.log_socket import get_seeding_logger
from geopy.geocoders import Nominatim
import locationtagger

# Essential entity model downloads
nltk.downloader.download('maxent_ne_chunker')
nltk.downloader.download('words')
nltk.downloader.download('treebank')
nltk.downloader.download('maxent_treebank_pos_tagger')
nltk.downloader.download('punkt')
nltk.download('averaged_perceptron_tagger')

logger = get_seeding_logger()

# ---------------------------------------------------------------------------
# Address prefix regex fallback patterns
# ---------------------------------------------------------------------------
_ADDRESS_PREFIXES = [
    # EN — unambiguous
    r"\bblvd\b", r"\bhwy\b", r"\bave\.\b", r"\brd\.\b", r"\bst\.\b",
    r"\bPO\s+Box\b", r"\bP\.O\.\b", r"\bapt\.?\b", r"\bste\.\b",
    r"\d+(?:st|nd|rd|th)\s+(?:floor|street|ave)\b",

    # FR — unambiguous  
    r"\brue\b", r"\ballée\b", r"\bimpasse\b", r"\brond-point\b",
    r"\bbâtiment\b", r"\bimmeuble\b", r"\blotissement\b",
    r"\bBP\s+\d", r"\bcedex\b", r"\bcode postal\b",
]

# No need for \b in the outer pattern anymore since each prefix has its own
_ADDRESS_RE = re.compile(
    "|".join(_ADDRESS_PREFIXES),
    re.IGNORECASE,
)

_TEXT_TAGS = {
    # Semantic address containers (high confidence)
    "address",
    # Table cells (common in contact/profile pages)
    "td", "th",
    # Labeled fields (forms, profile cards)
    "label", "dt", "dd",
    # Generic text blocks (only ones likely to contain full addresses)
    "p", "li", "span",
}

def _strip_html(html: str) -> str:
    """Parse HTML and return clean text for NER processing. Remove headers, scripts, styles, and excessive whitespace."""
    bs = BeautifulSoup(html, "html.parser")
    # Keep only the body content if available, otherwise use the whole text
    if bs.body:
        bs = bs.body
    
    # Remove script and style elements
    for node in bs(["script", "style", "noscript", "svg" , "head"]):
        node.decompose()
    return bs.get_text(" ", strip=True)

def _match_exact(name: str, text: str) -> bool:
    return bool(re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE))

def _filter_locations(entities, text) -> tuple[list, list]:
    """Filter using only countries and cities for now if at least one city is saved it will be prioritized over country-only matches. Exact word boundary matches are required to reduce false positives (e.g., "York" in "New York")."""
    countries = [c for c in entities.countries if _match_exact(c, text)]
    cities    = [c for c in entities.cities    if _match_exact(c, text) and len(c) > 3]
    return countries, cities

def _score_locations(countries, cities, country_cities) -> tuple[Optional[str], Optional[str]]:
    """
    Score and rank location candidates:
        - Cities are always prioritized over countries when both are present (more specific).
        - If no city found, fall back to best country, then try to find a paired city from country_cities.
    """
    city_counts    = Counter(cities)
    country_counts = Counter(countries)

    # Boost paired mentions
    for country, paired_cities in country_cities.items():
        if country in country_counts:       
            country_counts[country] += 2
        for city in paired_cities:
            if city in city_counts:
                city_counts[city] += 2


    best_city    = city_counts.most_common(1)[0][0]    if city_counts    else None
    best_country = country_counts.most_common(1)[0][0] if country_counts else None

    # If no city found, try to find one paired with the best country
    if not best_city and best_country:
        paired = country_cities.get(best_country, [])
        best_city = paired[0] if paired else None

    return best_country, best_city


def _extract_with_locationtagger(
    text: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract (country, city, zip_code) using locationtagger.
    """
    if not text:
        return None, None, None
    # --- country ---
    first_country = None
    first_city = None

    try:
        
        entities = locationtagger.find_locations(text=text)

        # Log raw extraction for debugging confidence
        logger.debug(f"Raw countries: {entities.countries}")
        logger.debug(f"Raw cities: {entities.cities}")
        logger.debug(f"Country-city pairs: {entities.country_cities}")
        logger.debug(f"Other regions: {entities.other}")  # ambiguous/low-confidence hits

        # Apply filtering and scoring to handle multiple candidates and boost paired mentions
        countries, cities = _filter_locations(entities, text)
        first_country, first_city = _score_locations(countries, cities, entities.country_cities)
        logger.debug(
            f"locationtagger — country={first_country!r} city={first_city!r}"
        )

        # --- zip , country , city fallbacks via geopy ---
        zip_code = None
        if first_city or first_country:
            geolocator = Nominatim(user_agent="formafast_geo")
            location = geolocator.geocode(", ".join(filter(None, [first_city, first_country])), addressdetails=True)
            if location:
                # Step 2: reverse geocode using coordinates for richer address data
                reversed_location = geolocator.reverse(
                    (location.latitude, location.longitude),
                    addressdetails=True,
                    language="en"
                )
                if reversed_location and reversed_location.raw.get("address"):
                    addr = reversed_location.raw["address"]
                    zip_code = addr.get("postcode")
                    if not first_country:
                        first_country = addr.get("country")
                    if not first_city:
                        first_city = addr.get("city") or addr.get("town") or addr.get("village")

        return first_country, first_city, zip_code

    except Exception as exc:
        logger.debug(f"locationtagger extraction failed: {exc}", exc_info=True)
        return None, None, None


def _regex_extract_address(
    html: Optional[str],
) -> tuple[Optional[str]]:
    """Fallback extraction using address prefix regex patterns.
    Searches HTML elements for address-like text and returns it as the
    location field. Does not attempt to decompose into city/country/zip.
    """
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_TEXT_TAGS):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if _ADDRESS_RE.search(text):
            logger.debug(f"Address regex match in <{tag.name}>: {text[:120]}...")
            return text
    return None


def extract_location_city_country(
    html: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract (location, city, country, zip_code) from an HTML page.
    """
    if not html or not html.strip():
        return None, None, None, None

    plain_text = _strip_html(html)  # ← strip HTML before NER
    country, city, zip_code = _extract_with_locationtagger(text=plain_text)
    address = _regex_extract_address(html)

    return address, city, country, zip_code
