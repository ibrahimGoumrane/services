"""Email classification utilities"""

from typing import Tuple, Optional
import logging


logger = logging.getLogger(__name__)


def classify_email(
    email: str,
    generic_domains: set,
    generic_users: set,
    generic_mx: set,
    site_builder_domains: set,
) -> Tuple[Optional[bool], Optional[bool]]:
    """
    Classify an email as generic or non-generic based on multiple criteria.
    
    Args:
        email: Email address to classify
        generic_domains: Set of generic domain names
        generic_users: Set of generic user patterns
        generic_mx: Set of generic domains to match for this email context
        site_builder_domains: Set of site builder domains
    
    Returns:
        Tuple of (is_generic_email, is_user_generic)
    """
    if not email or '@' not in email:
        return None, None
    
    email_lower = email.lower()
    user_part, domain_part = email_lower.rsplit('@', 1)
    
    # Check each criteria
    domain_is_generic = domain_part in generic_domains
    user_is_generic = user_part + '@' in generic_users
    mx_is_generic = domain_part in generic_mx
    domain_is_site_builder = domain_part in site_builder_domains
    
    # Email is generic only when the domain, MX, or site-builder rules match.
    is_generic_email = domain_is_generic or mx_is_generic or domain_is_site_builder
    
    if is_generic_email:
        logger.debug(f"Generic email detected: {email}")
    else:
        logger.debug(f"Non-generic email: {email}")
    
    return is_generic_email, user_is_generic
