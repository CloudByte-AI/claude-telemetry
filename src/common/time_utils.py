"""
Time Utilities Module

Provides functions for handling timezones and converting UTC timestamps to IST.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

# Indian Standard Time (IST) is UTC+5:30
IST_OFFSET = timezone(timedelta(hours=5, minutes=30))


def get_now_ist() -> datetime:
    """
    Get the current time in Indian Standard Time (IST).
    
    Returns:
        datetime: Current IST datetime object
    """
    return datetime.now(IST_OFFSET)


def get_now_ist_iso() -> str:
    """
    Get the current time in ISO format (IST).
    
    Returns:
        str: ISO formatted IST timestamp
    """
    return get_now_ist().isoformat()


def to_ist(timestamp: Any) -> str:
    """
    Convert a UTC timestamp (string, int, or datetime) to IST ISO string.
    
    Args:
        timestamp: Timestamp to convert. Can be:
            - ISO format string (e.g., "2026-05-07T07:00:18.123Z")
            - Milliseconds timestamp (int)
            - datetime object
            - None (returns current IST time)
            
    Returns:
        str: ISO formatted IST timestamp
    """
    if timestamp is None:
        return get_now_ist_iso()

    try:
        # Handle datetime object
        if isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                # Assume UTC if no timezone provided
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return timestamp.astimezone(IST_OFFSET).isoformat()

        # Handle milliseconds timestamp
        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
            return dt.astimezone(IST_OFFSET).isoformat()

        # Handle string format
        if isinstance(timestamp, str):
            if not timestamp.strip():
                return get_now_ist_iso()
                
            # Replace 'Z' with UTC offset for fromisoformat compatibility
            clean_ts = timestamp.replace('Z', '+00:00')
            try:
                dt = datetime.fromisoformat(clean_ts)
            except ValueError:
                # Handle cases like "2026-05-07 07:00:18" (no T)
                try:
                    dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    # Fallback for other formats if needed
                    return get_now_ist_iso()
                    
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
                
            return dt.astimezone(IST_OFFSET).isoformat()

    except Exception:
        # Final fallback to current time if conversion fails
        return get_now_ist_iso()

    return get_now_ist_iso()
