"""Email validation and filtering utilities"""

import logging
from typing import List, Optional, Set

from .email_classifiers import classify_email

logger = logging.getLogger(__name__)


class EmailValidator:
    """Email validator that filters against allowlists and excludes bad email patterns"""

    def __init__(
        self,
        generic_domains: Optional[List[str]] = None,
        generic_users: Optional[List[str]] = None,
        site_builder_domains: Optional[List[str]] = None,
        excluded_domains: Optional[List[str]] = None,
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

        logger.info(
            f"EmailValidator initialized with {len(self.generic_domains)} generic domains, "
            f"{len(self.generic_users)} generic users, {len(self.site_builder_domains)} site builders"
        )

    def filter_emails(self, emails: List[str]) -> Optional[str]:
        """
        Filter emails based on generic domains, users, and site builder domains.

        Args:
            emails: List of email addresses to filter

        Returns:
            First valid email after filtering, or None if no valid email found
        """
        if not emails or len(emails) == 0:
            return None

        for email in emails:
            email_lower = email.lower().strip()

            # Check email format
            if "@" not in email_lower:
                logger.debug(f"Skipping invalid email format: {email}")
                continue

            _, domain_part = email_lower.rsplit("@", 1)

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

            logger.debug(f"Email passed validation: {email}")         
            return email.strip().lower()

        return emails[0]
