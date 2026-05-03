"""Geolocation extraction using locationtagger NER."""
import logging
import re
from typing import Optional
from collections import Counter
from bs4 import BeautifulSoup

import nltk
from geopy.geocoders import Nominatim
import locationtagger

# Essential entity model downloads
nltk.downloader.download('maxent_ne_chunker')
nltk.downloader.download('words')
nltk.downloader.download('treebank')
nltk.downloader.download('maxent_treebank_pos_tagger')
nltk.downloader.download('punkt')
nltk.download('averaged_perceptron_tagger')

logger = logging.getLogger(__name__)

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

def _score_locations(entities) -> tuple[Optional[str], Optional[str]]:
    """
    Score extracted locations by frequency and position (earlier = more relevant).
    Returns (best_country, best_city).
    """
    # Count raw mentions across all location lists
    country_counts = Counter(entities.countries)
    city_counts = Counter(entities.cities)

    # Boost cities that are explicitly paired with a country
    for country, cities in entities.country_cities.items():
        for city in cities:
            city_counts[city] += 2        # paired mention = stronger signal
            country_counts[country] += 2

    # Boost cities found in country_regions too
    for country, regions in entities.country_regions.items():
        country_counts[country] += 1

    best_country = country_counts.most_common(1)[0][0] if country_counts else None
    best_city = city_counts.most_common(1)[0][0] if city_counts else None

    return best_country, best_city , country_counts, city_counts


def _extract_with_locationtagger(
    text: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract (address, country, city, zip_code) using locationtagger.
    """
    if not text:
        return None, None, None, None
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

        first_country, first_city , country_counts, city_counts = _score_locations(entities)

        if len(entities.countries) > 3 and country_counts.most_common(1)[0][1] == 1:
            logger.debug("Low confidence country — too many candidates, no clear winner")
            first_country = None

        if len(entities.cities) > 5 and city_counts.most_common(1)[0][1] == 1:
            logger.debug("Low confidence city — too many candidates, no clear winner")
            first_city = None

        if entities.country_cities:
            # Pick the first country that has associated cities
            for country, cities in entities.country_cities.items():
                if country and cities:
                    first_country = country
                    first_city = cities[0]
                    break

        # Fallback: use standalone countries / cities lists
        if not first_country and entities.countries:
            first_country = entities.countries[0]

        if not first_city and entities.cities:
            first_city = entities.cities[0]

        # --- address ---
        address = ", ".join(filter(None, [first_city, first_country]))

        logger.debug(
            f"locationtagger — country={first_country!r} city={first_city!r}"
        )

        # --- zip code via geopy ---
        zip_code = None
        if first_country or first_city:
            query = ", ".join(filter(None, [first_city, first_country]))
            geolocator = Nominatim(user_agent="formafast_geo")
            location = geolocator.geocode(query, addressdetails=True)
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

        return address, first_country, first_city, zip_code

    except Exception as exc:
        logger.debug(f"locationtagger extraction failed: {exc}", exc_info=True)
        return None, None, None, None


def extract_location_city_country(
    html: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract (location, city, country, zip_code) from an HTML page.
    """
    if not html or not html.strip():
        return None, None, None, None

    plain_text = _strip_html(html)  # ← strip HTML before NER
    address, country, city, zip_code = _extract_with_locationtagger(text=plain_text)
    return address, city, country, zip_code
