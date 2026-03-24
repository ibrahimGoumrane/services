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
    mx_root: Optional[str] = None
) -> Tuple[Optional[bool], Optional[bool]]:
    """
    Classify an email as generic or non-generic based on multiple criteria.
    
    Args:
        email: Email address to classify
        generic_domains: Set of generic domain names
        generic_users: Set of generic user patterns
        generic_mx: Set of generic MX root domains
        site_builder_domains: Set of site builder domains
        mx_root: Root domain of the email's MX server (optional)
    
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
    mx_is_generic = mx_root in generic_mx if mx_root else False
    domain_is_site_builder = domain_part in site_builder_domains
    mx_is_site_builder = mx_root in site_builder_domains if mx_root else False
    
    # Email is generic if ANY criteria is true
    is_generic_email = domain_is_generic or user_is_generic or mx_is_generic or \
                       domain_is_site_builder or mx_is_site_builder
    
    if is_generic_email:
        logger.debug(f"Generic email detected: {email}")
    else:
        logger.debug(f"Non-generic email: {email}")
    
    return is_generic_email, user_is_generic
