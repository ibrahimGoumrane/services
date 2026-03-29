"""Data transformation and formatting utilities"""

import pandas as pd
from typing import Optional, Any

# Prefix for default values in mapping
DEFAULT_VALUE_PREFIX = "__default__:"


def is_default_value(mapping_value: Optional[str]) -> bool:
    """Check if a mapping value is a default value (starts with __default__:)"""
    if mapping_value is None:
        return False
    return mapping_value.startswith(DEFAULT_VALUE_PREFIX)


def extract_default_value(mapping_value: str) -> Any:
    """Extract the actual default value from a mapping value that starts with __default__:"""
    if not is_default_value(mapping_value):
        return None
    return mapping_value[len(DEFAULT_VALUE_PREFIX):]


def get_mapped_value(row: Any, mapping_value: Optional[str], fallback_default: Any = None) -> Any:
    """
    Get a value from a row based on mapping value.
    
    Supports two formats:
    - Column name: maps from CSV column
    - __default__:{value}: returns the default value for all rows
    
    Args:
        row: pandas row or dict-like object
        mapping_value: either a column name or "__default__:{value}"
        fallback_default: default value to use if the column is empty/NaN
    
    Returns:
        Value from row, default value, or fallback_default
    """
    if mapping_value is None or mapping_value == "":
        return fallback_default
    
    # Check if this is a default value
    if is_default_value(mapping_value):
        return extract_default_value(mapping_value)
    
    # Otherwise, treat as column name
    try:
        value = row.get(mapping_value) if hasattr(row, 'get') else row[mapping_value]
    except (KeyError, TypeError, IndexError):
        return fallback_default
    
    if pd.isna(value) or value == '' or value == 'None':
        return fallback_default
    
    return value


def safe_get(row: Any, column_name: Optional[str], default_value: Any = None) -> Any:
    """
    Safely get a value from a pandas row, converting NaN to None or default value.
    
    Args:
        row: pandas row or dict-like object
        column_name: name of the column to get (if None, returns default)
        default_value: value to use if field is None/empty
    
    Returns:
        Value from row, or default_value if None/empty
    """
    if column_name is None:
        return default_value
    
    try:
        value = row.get(column_name) if hasattr(row, 'get') else row[column_name]
    except (KeyError, TypeError):
        return default_value
    
    if pd.isna(value) or value == '' or value == 'None':
        return default_value
    
    return value


def format_fname(fname: Optional[str]) -> Optional[str]:
    """
    Format first name: first letter uppercase, remaining letters lowercase.
    
    Args:
        fname: First name string
    
    Returns:
        Formatted first name or None
    """
    if not fname or pd.isna(fname):
        return None
    
    fname = str(fname).strip()
    if not fname:
        return None
    
    return fname.capitalize()


def format_lname(lname: Optional[str]) -> Optional[str]:
    """
    Format last name: all uppercase.
    
    Args:
        lname: Last name string
    
    Returns:
        Formatted last name or None
    """
    if not lname or pd.isna(lname):
        return None
    
    lname = str(lname).strip()
    if not lname:
        return None
    
    return lname.upper()


def format_eta(seconds: float) -> str:
    """
    Format seconds into human-readable duration format (H:MM:SS or M:SS).
    
    Args:
        seconds: Number of seconds
    
    Returns:
        Formatted duration string or 'N/A' if unknown
    """
    if not seconds or seconds == float('inf'):
        return "N/A"
    
    try:
        seconds = int(seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        if hours:
            return f"{hours:d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:d}:{secs:02d}"
    except Exception:
        return "N/A"
