"""Geolocation extraction using locationtagger NER."""

import logging
from typing import Optional
import nltk
import spacy
import locationtagger

# essential entity models downloads
nltk.downloader.download('maxent_ne_chunker')
nltk.downloader.download('words')
nltk.downloader.download('treebank')
nltk.downloader.download('maxent_treebank_pos_tagger')
nltk.downloader.download('punkt')
nltk.download('averaged_perceptron_tagger')


logger = logging.getLogger(__name__)


def _extract_with_locationtagger(
    text: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract the most likely (country, city) using locationtagger.

    Accepts raw *text* and returns the most frequently-mentioned country and city.
    """
    if not text:
        return None, None, None

    try:
        entities = locationtagger.find_locations(text=text)

        # Pick the most frequently mentioned country
        first_country, first_cities = next(iter(entities.country_cities.items()))
        if not first_country or not first_cities:
            first_cities = entities.cities
        if not first_cities:
            first_cities = [None]
        address  = entities.address_strings[0] if len(entities.address_strings) else first_cities[0]
        logger.debug(f"locationtagger result — country={first_country!r} city={first_cities[0]!r}")
        return address , first_country, first_cities[0]
    except Exception as exc:
        logger.debug(f"locationtagger extraction failed: {exc}")
        return None, None, None


def extract_location_city_country(
    html: Optional[str] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract (location, city, country) from a page using locationtagger.

    """
    if not html or not html.strip():
        return None, None, None

    address, country, city = _extract_with_locationtagger(text=html.strip())

    return address, city, country