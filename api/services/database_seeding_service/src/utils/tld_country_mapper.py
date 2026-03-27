"""Country lookup from email domain ccTLD using pycountry (ISO 3166-1)."""

from typing import Optional

import pycountry
import tldextract


# Common non-ISO or special-case ccTLD aliases.
TLD_ALPHA2_OVERRIDES = {
    "uk": "GB",
}


def get_country_from_email_domain(email: str) -> Optional[str]:
    """
    Extract country from email domain using TLD mapping.
    
    Args:
        email: Email address to extract country from
    
    Returns:
        Country name or None if TLD not recognized
    
    Example:
        get_country_from_email_domain("contact@example.fr") -> "France"
        get_country_from_email_domain("info@company.co.uk") -> "United Kingdom"
        get_country_from_email_domain("support@domain.com") -> None
    """
    if not email or "@" not in email:
        return None

    try:
        domain = email.split("@", 1)[1].strip().lower()
        extracted = tldextract.extract(domain)

        if not extracted.suffix:
            return None

        # For multi-part suffixes (e.g. co.uk), ccTLD is the last label (uk).
        last_label = extracted.suffix.split(".")[-1].lower()

        # ccTLD must be 2 letters; skip generic TLDs like com/org/net.
        if len(last_label) != 2 or not last_label.isalpha():
            return None

        alpha2 = TLD_ALPHA2_OVERRIDES.get(last_label, last_label.upper())
        country = pycountry.countries.get(alpha_2=alpha2)
        if country is None:
            return None

        return country.name

    except Exception:
        return None
