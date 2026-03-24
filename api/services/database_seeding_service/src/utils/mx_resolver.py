"""MX record resolution and caching utilities"""

from typing import Tuple, Optional, Dict, List
import dns.resolver
import tldextract
import logging

from .contact_repository import get_mxrecord_by_domain


logger = logging.getLogger(__name__)


def resolve_mx_record(
    domain: str,
    mx_cache: Dict[str, Tuple[Optional[str], Optional[str]]],
    new_mx_records: List[Tuple[str, str, str]]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Get MX record from cache or resolve it from DNS.
    
    Args:
        domain: Email domain to resolve
        mx_cache: Dictionary for caching results (domain -> (mx_host, root_domain))
        new_mx_records: List to collect new MX records for batch insert
    
    Returns:
        Tuple of (mx_host, root_domain) or (None, None) if not found/invalid
    """
    # Check cache first
    if domain in mx_cache:
        return mx_cache[domain]
    
    try:
        # Check if MX record already exists in database
        logger.debug(f"Checking MX record for domain: {domain}")
        existing_mx = get_mxrecord_by_domain(domain)
        
        if existing_mx:
            mx_host = existing_mx[0]
            root_domain = existing_mx[1]
            mx_cache[domain] = (mx_host, root_domain)
            logger.debug(f"Found existing MX record for {domain}: {mx_host} -> {root_domain}")
            return (mx_host, root_domain)
        
        # Resolve MX record from DNS
        logger.debug(f"Resolving MX record for domain: {domain}")
        answers = dns.resolver.resolve(domain, "MX")
        
        for rdata in answers:
            mx_host = str(rdata.exchange).rstrip(".").lower()
            extracted = tldextract.extract(mx_host)
            mx_root_domain = f"{extracted.domain}.{extracted.suffix}"
            
            logger.info(f"MX Host: {mx_host} -> Root Domain: {mx_root_domain} for {domain}")
            
            # Add to batch list for later insertion
            new_mx_records.append((mx_host, mx_root_domain, domain))
            mx_cache[domain] = (mx_host, mx_root_domain)
            
            return (mx_host, mx_root_domain)
    
    except dns.resolver.NoAnswer:
        logger.warning(f"Domain {domain} has no MX records")
        mx_cache[domain] = (None, None)
        return (None, None)
    except dns.resolver.NXDOMAIN:
        logger.warning(f"Domain {domain} does not exist")
        mx_cache[domain] = (None, None)
        return (None, None)
    except Exception as e:
        logger.warning(f"Failed to resolve MX record for {domain}: {str(e)}")
        mx_cache[domain] = (None, None)
        return (None, None)
    
    return (None, None)
