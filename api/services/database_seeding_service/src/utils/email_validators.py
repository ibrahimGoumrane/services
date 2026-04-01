"""Email validation and filtering utilities"""

from typing import List, Set, Optional
import logging

from .email_classifiers import classify_email


logger = logging.getLogger(__name__)


class EmailValidator:
    """Email validator that filters against allowlists and excludes bad email patterns"""
    
    def __init__(
        self,
        generic_domains: Optional[List[str]] = None,
        generic_users: Optional[List[str]] = None,
        site_builder_domains: Optional[List[str]] = None,
        excluded_domains: Optional[List[str]] = None
    ):
        """
        Initialize email validator with filter lists.
        
        Args:
            generic_domains: List of generic email domains (e.g., gmail.com, yahoo.com)
            generic_users: List of generic email user patterns (e.g., contact@, admin@)
            site_builder_domains: List of site builder domains (e.g., wix.com, shopify.com)
            excluded_domains: List of excluded domains to skip
        """
        self.generic_domains: Set[str] = set(generic_domains or [])
        self.generic_users: Set[str] = set(generic_users or [])
        self.site_builder_domains: Set[str] = set(site_builder_domains or [])
        self.excluded_domains: Set[str] = set(excluded_domains or [])
        
        logger.info(f"EmailValidator initialized with {len(self.generic_domains)} generic domains, "
                   f"{len(self.generic_users)} generic users, {len(self.site_builder_domains)} site builders")
    
    def filter_emails(self, emails: List[str]) -> List[str]:
        """
        Filter emails based on generic domains, users, and site builder domains.
        
        Args:
            emails: List of email addresses to filter
        
        Returns:
            List of valid emails after filtering
        """
        if not emails:
            return []
        
        filtered = []
        
        for email in emails:
            email_lower = email.lower().strip()
            
            # Check email format
            if '@' not in email_lower:
                logger.debug(f"Skipping invalid email format: {email}")
                continue
            
            user_part, domain_part = email_lower.rsplit('@', 1)

            is_generic, _ = classify_email(
                email=email_lower,
                generic_domains=self.generic_domains,
                generic_users=self.generic_users,
                generic_mx=set(),
                site_builder_domains=self.site_builder_domains,
            )
            
            # Skip if email matches any generic criteria.
            if is_generic:
                logger.debug(f"Skipping generic email: {email}")
                continue
            
            # Skip if domain is excluded
            if domain_part in self.excluded_domains:
                logger.debug(f"Skipping email with excluded domain: {email}")
                continue
            
            filtered.append(email)
        
        if emails and not filtered:
            logger.info(f"All {len(emails)} email(s) were filtered out")
        elif filtered:
            logger.info(f"Kept {len(filtered)} valid email(s) out of {len(emails)} found")
        
        return filtered
    
    def is_generic_email(self, email: str) -> bool:
        """
        Check if an email is generic (from generic domain or user pattern).
        
        Args:
            email: Email address to check
        
        Returns:
            True if email is generic, False otherwise
        """
        if not email or '@' not in email:
            return False

        is_generic, _ = classify_email(
            email=email,
            generic_domains=self.generic_domains,
            generic_users=self.generic_users,
            generic_mx=set(),
            site_builder_domains=self.site_builder_domains,
        )
        return bool(is_generic)
