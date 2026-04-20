"""Address and geolocation extraction helpers.

Flow:
1. Use provided HTML when available; otherwise fetch via requests.
2. Clean non-content tags.
3. Try structured extraction first.
4. Fallback to postcode-driven regex candidates.
5. Normalize and parse with lightweight heuristics.
"""

import logging
import re
from functools import lru_cache
from typing import Optional

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

_STRUCTURED_SELECTORS = [
    "address",
    "[itemprop='streetAddress']",
    "[itemprop='addressLocality']",
    "[itemprop='postalCode']",
    "[itemprop='addressRegion']",
    "[itemprop='addressCountry']",
    "[itemtype='http://schema.org/PostalAddress']",
    "[itemtype='https://schema.org/PostalAddress']",
    ".address",
    ".location",
    ".contact",
    ".coordonnees",
    "#address",
    "#location",
    "#contact",
    "#coordonnees",
]

_STRUCTURED_CITY_SELECTORS = [
    "[itemprop='addressLocality']",
    "[itemprop='addressRegion']",
    ".city",
]

_STRUCTURED_COUNTRY_SELECTORS = [
    "[itemprop='addressCountry']",
    ".country",
]

_POSTCODE_PATTERNS = [
    re.compile(r"\b\d{5}\b"),
    re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b", re.IGNORECASE),
    re.compile(r"\b\d{5}(?:-\d{4})?\b"),
    re.compile(r"\b(?:GIR\s?0AA|[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.IGNORECASE),
]

_CITY_POSTCODE_PATTERNS = [
    re.compile(r"\b\d{5}\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-\s]{1,60})"),
    re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-\s]{1,60})", re.IGNORECASE),
    re.compile(r"\b\d{5}(?:-\d{4})?\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\-\s]{1,60})"),
]

_COUNTRY_ALIASES = {
    "france": "France",
    "germany": "Germany",
    "deutschland": "Germany",
    "morocco": "Morocco",
    "maroc": "Morocco",
    "canada": "Canada",
    "united states": "United States",
    "usa": "United States",
    "uk": "United Kingdom",
    "united kingdom": "United Kingdom",
    "england": "United Kingdom",
    "spain": "Spain",
    "italy": "Italy",
    "netherlands": "Netherlands",
    "belgium": "Belgium",
    "portugal": "Portugal",
    "switzerland": "Switzerland",
}

_CITY_STOPWORDS = {
    "website",
    "details",
    "google",
    "privacy",
    "policy",
    "cookie",
    "contact",
    "terms",
    "service",
}


def _clean_soup(soup: BeautifulSoup) -> None:
    for node in soup(["script", "style", "nav", "footer", "header"]):
        node.decompose()


def _to_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _join_lines_window(lines: list[str], index: int, radius: int = 2) -> str:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return " ".join(lines[start:end]).strip()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _title_case_words(value: str) -> str:
    return " ".join(word[:1].upper() + word[1:].lower() for word in value.split() if word)


def _extract_structured_city_country(soup: BeautifulSoup) -> tuple[Optional[str], Optional[str]]:
    city: Optional[str] = None
    country: Optional[str] = None

    for selector in _STRUCTURED_CITY_SELECTORS:
        node = soup.select_one(selector)
        if node:
            value = _sanitize_city(node.get_text(" ", strip=True))
            if value:
                city = value
                break

    for selector in _STRUCTURED_COUNTRY_SELECTORS:
        node = soup.select_one(selector)
        if node:
            raw = _normalize_text(node.get_text(" ", strip=True))
            parsed = _extract_country(raw)
            if parsed:
                country = parsed
                break

    return city, country


def _extract_structured_candidates(soup: BeautifulSoup) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    for selector in _STRUCTURED_SELECTORS:
        for node in soup.select(selector):
            text = _normalize_text(node.get_text(" ", strip=True))
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            candidates.append(text)

    return candidates


def _extract_regex_candidates(clean_text: str) -> list[str]:
    lines = _to_lines(clean_text)
    if not lines:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    for idx, line in enumerate(lines):
        for pattern in _POSTCODE_PATTERNS:
            if not pattern.search(line):
                continue
            window = _normalize_text(_join_lines_window(lines, idx, radius=2))
            key = window.lower()
            if window and key not in seen:
                seen.add(key)
                candidates.append(window)

    return candidates


def _extract_country(candidate: str) -> Optional[str]:
    normalized = _normalize_text(candidate).lower()
    if not normalized:
        return None

    for token, country in _COUNTRY_ALIASES.items():
        if re.search(rf"\b{re.escape(token)}\b", normalized):
            return country

    return None


def _sanitize_city(value: str) -> Optional[str]:
    cleaned = re.sub(r"\b\d{4,6}(?:-\d{4})?\b", " ", _normalize_text(value))
    cleaned = re.sub(r"[^A-Za-zÀ-ÿ'\-\s]", " ", cleaned)
    cleaned = _normalize_text(cleaned)
    if not cleaned or len(cleaned) < 2:
        return None

    words = cleaned.lower().split()
    if len(words) > 5:
        return None
    if any(word in _CITY_STOPWORDS for word in words):
        return None

    return _title_case_words(cleaned)


def _extract_city(candidate: str) -> Optional[str]:
    normalized = _normalize_text(candidate)
    if not normalized:
        return None

    for pattern in _CITY_POSTCODE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        city = _sanitize_city(match.group(1))
        if city:
            return city

    parts = [part.strip() for part in normalized.split(",") if part.strip()]
    if len(parts) >= 2:
        city = _sanitize_city(parts[-2])
        if city:
            return city

    return None


@lru_cache(maxsize=1024)
def _parse_candidate(candidate: str) -> tuple[Optional[str], Optional[str]]:
    text = _normalize_text(candidate)
    if not text:
        return None, None

    city = _extract_city(text)
    country = _extract_country(text)

    return city, country


def _fetch_html(url: str, timeout_seconds: int = 8) -> str:
    try:
        response = requests.get(
            url,
            timeout=timeout_seconds,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )
        if response.status_code >= 400:
            return ""
        return response.text or ""
    except Exception:
        return ""


def extract_location_city_country(
    page_url: str,
    html: Optional[str] = None,
    timeout_seconds: int = 8,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract (location, city, country) from a page using structured + regex + libpostal."""

    source_html = (html or "").strip()
    if not source_html and page_url:
        source_html = _fetch_html(page_url, timeout_seconds=timeout_seconds)

    if not source_html:
        return None, None, None

    try:
        soup = BeautifulSoup(source_html, "html.parser")
        _clean_soup(soup)

        structured_city, structured_country = _extract_structured_city_country(soup)
        if structured_city or structured_country:
            structured_location = structured_city or structured_country
            return structured_location, structured_city, structured_country

        structured_candidates = _extract_structured_candidates(soup)
        if structured_candidates:
            for candidate in structured_candidates:
                try:
                    city, country = _parse_candidate(candidate)
                except Exception:
                    continue
                if city or country:
                    location = city or country
                    return location, city, country

        clean_text = soup.get_text("\n", strip=True)
        regex_candidates = _extract_regex_candidates(clean_text)
        for candidate in regex_candidates:
            try:
                city, country = _parse_candidate(candidate)
            except Exception:
                continue
            if city or country:
                location = city or country
                return location, city, country

        return None, None, None
    except Exception as exc:
        logger.debug(f"Geo extraction failed for '{page_url}': {exc}")
        return None, None, None
