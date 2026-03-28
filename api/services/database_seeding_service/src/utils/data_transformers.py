"""Data transformation and formatting utilities"""

import pandas as pd
from typing import Optional, Any


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
