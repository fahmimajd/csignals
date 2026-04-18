"""Utility helper functions."""

import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, TypeVar, Callable
from functools import wraps
import time


T = TypeVar('T')


def retry_async(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator for async function retry with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exceptions to catch and retry
        
    Returns:
        Decorated async function
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_retries - 1:
                        break
                    
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
            
            raise last_exception
        
        return wrapper
    return decorator


def rate_limit(calls_per_second: int):
    """
    Decorator to rate limit async function calls.
    
    Args:
        calls_per_second: Maximum calls per second
        
    Returns:
        Decorated async function
    """
    min_interval = 1.0 / calls_per_second
    last_call = 0.0
    lock = asyncio.Lock()
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal last_call
            
            async with lock:
                now = time.monotonic()
                elapsed = now - last_call
                
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)
                
                last_call = time.monotonic()
            
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


def format_number(value: float, decimals: int = 2) -> str:
    """
    Format number with thousands separator and fixed decimals.
    
    Args:
        value: Number to format
        decimals: Number of decimal places
        
    Returns:
        Formatted string
    """
    if value >= 1_000_000_000:
        return f"{value/1_000_000_000:,.{decimals}f}B"
    elif value >= 1_000_000:
        return f"{value/1_000_000:,.{decimals}f}M"
    elif value >= 1_000:
        return f"{value/1_000:,.{decimals}f}K"
    else:
        return f"{value:,.{decimals}f}"


def format_price(price: float, symbol: str = "") -> str:
    """
    Format price based on typical precision for the symbol.
    
    Args:
        price: Price value
        symbol: Trading symbol (optional, for precision hints)
        
    Returns:
        Formatted price string
    """
    # High-value symbols (BTC, ETH) use 2 decimals
    if symbol.upper().startswith(("BTC", "ETH")):
        return f"${price:,.2f}"
    
    # Low-value symbols use more decimals
    if price < 1:
        return f"${price:,.6f}"
    elif price < 10:
        return f"${price:,.4f}"
    else:
        return f"${price:,.2f}"


def format_percentage(value: float, decimals: int = 2) -> str:
    """
    Format percentage value.
    
    Args:
        value: Percentage value
        decimals: Number of decimal places
        
    Returns:
        Formatted percentage string
    """
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def calculate_time_remaining(deadline: datetime) -> Optional[str]:
    """
    Calculate human-readable time remaining until deadline.
    
    Args:
        deadline: Target datetime
        
    Returns:
        Human-readable string (e.g., "2h 30m") or None if expired
    """
    now = datetime.utcnow()
    
    if deadline <= now:
        return None
    
    delta = deadline - now
    total_seconds = int(delta.total_seconds())
    
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def chunk_list(lst: List[T], chunk_size: int) -> List[List[T]]:
    """
    Split list into chunks of specified size.
    
    Args:
        lst: List to split
        chunk_size: Size of each chunk
        
    Returns:
        List of chunks
    """
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safely divide two numbers, returning default on division by zero.
    
    Args:
        numerator: Dividend
        denominator: Divisor
        default: Value to return if denominator is zero
        
    Returns:
        Division result or default
    """
    if denominator == 0:
        return default
    return numerator / denominator


def clamp(value: float, min_val: float, max_val: float) -> float:
    """
    Clamp value between min and max.
    
    Args:
        value: Value to clamp
        min_val: Minimum value
        max_val: Maximum value
        
    Returns:
        Clamped value
    """
    return max(min_val, min(max_val, value))


def parse_timeframe(timeframe: str) -> timedelta:
    """
    Parse timeframe string to timedelta.
    
    Args:
        timeframe: Timeframe string (e.g., "1m", "5m", "1h", "4h", "1d")
        
    Returns:
        Timedelta object
    """
    unit = timeframe[-1].lower()
    value = int(timeframe[:-1])
    
    if unit == 'm':
        return timedelta(minutes=value)
    elif unit == 'h':
        return timedelta(hours=value)
    elif unit == 'd':
        return timedelta(days=value)
    else:
        raise ValueError(f"Unknown timeframe unit: {unit}")


def merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries.
    
    Args:
        base: Base dictionary
        override: Dictionary with values to override
        
    Returns:
        Merged dictionary
    """
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    
    return result
