"""Configuration models for database seeding"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ProcessingConfig:
    """Configuration object for CSV processing and database seeding"""
    
    csv_file_path: str
    """Path to the CSV file to process"""
    
    csv_mapping: Dict[str, str]
    """Mapping from CSV column names to database field names"""
    
    csv_separator: str = ','
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
    
    def __post_init__(self):
        """Validate configuration after initialization"""
        if not self.csv_file_path:
            raise ValueError("csv_file_path is required")
        if not self.csv_mapping:
            raise ValueError("csv_mapping is required and cannot be empty")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")
