"""Configuration models for database seeding"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from attrs import field
from pydantic import BaseModel, model_validator

from api.models import CsvMapping


class ProcessingConfig(BaseModel):
    """Configuration object for CSV processing and database seeding"""

    csv_file_path: str
    """Path to the CSV file to process"""

    csv_mapping: CsvMapping
    """Mapping from CSV column names to database field names"""

    csv_separator: str = ","
    """CSV separator character (default: comma)"""

    batch_size: int = 100
    """Number of records to insert per batch (default: 100)"""

    enable_web_scraping: bool = True
    """Enable NoDriver-based web scraping to find emails/websites (default: True)"""

    skip_google_search: bool = False
    """Skip Google search for missing websites (default: False)"""

    default_values: Optional[Dict[str, Any]] = None
    """Default values for null/empty fields"""

    sourcefile: Optional[str] = None
    """Original uploaded filename used for contact provenance"""

    @model_validator(mode="after")
    def validate_config(self) -> "ProcessingConfig":
        """Validate configuration after initialization"""
        if not self.csv_file_path:
            raise ValueError("csv_file_path is required")
        if not self.csv_mapping.has_mappings:
            raise ValueError("csv_mapping is required and cannot be empty")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
        return self


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class CsvRow:
    """All values extracted from a single CSV row at the start of processing."""
    fullname: str = ""
    fname: str = ""
    lname: str = ""
    name: str = ""
    email: str = ""
    website: str = ""
    phone: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    linkedin: Optional[str] = None
    position: Optional[str] = None
    address: Optional[str] = None
    city: str = ""
    zip_code: Optional[str] = None
    country: str = ""
    location: str = ""
    contact_form_url: Optional[str] = None
    image: Optional[str] = None
    sourcefile: Optional[str] = None
    ca: Optional[str] = None
    activite: str = ""


@dataclass
class PersonContactData:
    """Enriched contact data for a person (fname + lname / fullname)."""
    email: str = ""
    fullname: Optional[str] = None
    fname: Optional[str] = None
    lname: Optional[str] = None
    website: Optional[str] = None
    position: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    fax: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    contact_form_url: Optional[str] = None
    linkedin: Optional[str] = None
    image: Optional[str] = None
    mx_host: Optional[str] = None
    is_generic_email: bool = False
    is_user_generic: bool = False
    status: str = "valid"
    sourcefile: Optional[str] = None
    ca: Optional[str] = None
    activite: str = ""

    def to_tuple(self) -> tuple:
        return (
            self.email, self.fullname, self.fname, self.lname, self.website,
            self.position, self.phone, self.mobile, self.fax, self.name,
            self.address, self.city, self.zip_code, self.country, self.contact_form_url,
            self.linkedin, self.image, self.mx_host, self.is_generic_email, self.is_user_generic,
            self.status, self.sourcefile, self.ca, self.activite,
       )

@dataclass
class CompanyContactData:
    """Enriched contact data for a company (company name / website)."""
    email: str = ""
    website: Optional[str] = None
    phone: Optional[str] = None
    name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    contact_form_url: Optional[str] = None
    linkedin: Optional[str] = None
    position: Optional[str] = None
    image: Optional[str] = None
    mx_host: Optional[str] = None
    is_generic_email: bool = False
    is_user_generic: bool = False
    status: str = "valid"
    sourcefile: Optional[str] = None
    ca: Optional[str] = None
    activite: str = ""
    
    def to_tuple(self) -> tuple:
        """Return a tuple of all fields in a consistent order for database insertion."""
        return (
        self.email, None, None, None, self.website,
        self.position, self.phone, None, None, self.name,
        self.address, self.city, self.zip_code, self.country, self.contact_form_url,
        self.linkedin, self.image, self.mx_host, self.is_generic_email, self.is_user_generic,
        self.status, self.sourcefile, self.ca, self.activite,
    )

@dataclass
class RowStats:
    contact_form_found: bool = False
    synthetic_email_used: bool = False


@dataclass
class ScrapedWebData:
    """Raw data returned from website scraping."""
    emails: List[str] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)
    contact_page: Optional[str] = None
    location: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None