"""Input validation for Yahoo Finance MCP server."""

import re
from datetime import datetime
from typing import Optional


class ValidationError(Exception):
    """Raised when input validation fails."""
    pass


# Constants for validation
VALID_PERIODS = {'1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'ytd', 'max'}
VALID_INTERVALS = {'1m', '2m', '5m', '15m', '30m', '60m', '90m', '1h', '1d', '5d', '1wk', '1mo', '3mo'}


def validate_ticker(ticker: Optional[str]) -> str:
    """Validate and sanitize ticker symbol.

    Args:
        ticker: The ticker symbol to validate

    Returns:
        The validated and uppercase ticker symbol

    Raises:
        ValidationError: If ticker is invalid
    """
    if not ticker:
        raise ValidationError("Ticker symbol is required")

    ticker = ticker.upper().strip()

    # Allow basic ticker patterns: letters, numbers, hyphens, dots
    # Most tickers are 1-5 chars but some (like BRK-B) can be longer
    # Index symbols use ^ prefix (^GSPC, ^DJI), some use . prefix (.INX)
    # Futures use =F suffix (GC=F), forex uses =X suffix (EURUSD=X)
    if not re.match(r'^[\^.]?[A-Z][A-Z0-9.\-=]{0,9}$', ticker):
        raise ValidationError(f"Invalid ticker format: {ticker}")

    return ticker


def validate_period(period: str) -> str:
    """Validate period parameter.

    Args:
        period: The period string to validate

    Returns:
        The validated period string

    Raises:
        ValidationError: If period is invalid
    """
    if period not in VALID_PERIODS:
        raise ValidationError(
            f"Invalid period '{period}'. Must be one of: {', '.join(sorted(VALID_PERIODS))}"
        )
    return period


def validate_date(date_str: Optional[str]) -> Optional[str]:
    """Validate a date string in YYYY-MM-DD format.

    Args:
        date_str: The date string to validate, or None

    Returns:
        The validated date string, or None if input is None

    Raises:
        ValidationError: If date format is invalid
    """
    if not date_str:
        return None
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise ValidationError(
            f"Invalid date '{date_str}'. Must be in YYYY-MM-DD format (e.g., '2025-09-01')"
        )
    return date_str


def validate_interval(interval: str) -> str:
    """Validate interval parameter.

    Args:
        interval: The interval string to validate

    Returns:
        The validated interval string

    Raises:
        ValidationError: If interval is invalid
    """
    if interval not in VALID_INTERVALS:
        raise ValidationError(
            f"Invalid interval '{interval}'. Must be one of: {', '.join(sorted(VALID_INTERVALS))}"
        )
    return interval
