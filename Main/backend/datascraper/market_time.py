"""
Market-aware time context for financial queries.

Provides rich temporal context including market hours, last trading day,
and explicit instructions for date handling. Used by both Thinking mode
(prompt_builder.py) and Research mode (openai_search.py).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz

logger = logging.getLogger(__name__)

# US market hours (Eastern Time)
_MARKET_OPEN_HOUR = 9
_MARKET_OPEN_MINUTE = 30
_MARKET_CLOSE_HOUR = 16
_MARKET_CLOSE_MINUTE = 0
_US_EASTERN = pytz.timezone("America/New_York")


def _get_exchange_calendar():
    """Lazy-load the NYSE exchange calendar."""
    try:
        import exchange_calendars as xcals
        return xcals.get_calendar("XNYS")
    except ImportError:
        logger.warning("exchange_calendars not installed; falling back to weekday-only logic")
        return None
    except Exception as e:
        logger.warning(f"Failed to load NYSE calendar: {e}")
        return None


def _is_market_open_now(et_now: datetime) -> bool:
    """Check if US markets are currently open (simple hours check)."""
    if et_now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = et_now.replace(hour=_MARKET_OPEN_HOUR, minute=_MARKET_OPEN_MINUTE, second=0)
    market_close = et_now.replace(hour=_MARKET_CLOSE_HOUR, minute=_MARKET_CLOSE_MINUTE, second=0)
    return market_open <= et_now <= market_close


def _get_last_trading_day(et_now: datetime) -> str:
    """
    Get the last completed trading day as YYYY-MM-DD string.
    Uses exchange_calendars if available, otherwise falls back to simple weekday logic.
    """
    cal = _get_exchange_calendar()
    today = et_now.date()

    if cal is not None:
        try:
            import pandas as pd
            # exchange_calendars requires:
            #   - is_session(): tz-naive pd.Timestamp (session parameter)
            #   - previous_close(): tz-aware pd.Timestamp (minute parameter)
            today_str = today.strftime("%Y-%m-%d")
            ts_naive = pd.Timestamp(today_str)
            ts_aware = pd.Timestamp(today_str).tz_localize("America/New_York")

            # If market is still open today, the last *completed* session is yesterday's
            if _is_market_open_now(et_now):
                prev = cal.previous_close(ts_aware)
                return prev.strftime("%Y-%m-%d")
            else:
                # Market is closed. If today was a trading day and market already closed, today is the last.
                if cal.is_session(ts_naive) and et_now.hour >= _MARKET_CLOSE_HOUR:
                    return today_str
                else:
                    prev = cal.previous_close(ts_aware)
                    return prev.strftime("%Y-%m-%d")
        except Exception as e:
            logger.warning(f"exchange_calendars lookup failed: {e}")

    # Fallback: simple weekday logic (no holiday awareness)
    if _is_market_open_now(et_now):
        # Today is in progress, go to previous business day
        check = today - timedelta(days=1)
    elif et_now.hour >= _MARKET_CLOSE_HOUR and et_now.weekday() < 5:
        return today.strftime("%Y-%m-%d")
    else:
        check = today - timedelta(days=1)

    while check.weekday() >= 5:
        check -= timedelta(days=1)
    return check.strftime("%Y-%m-%d")


def build_market_time_context(
    user_timezone: Optional[str] = None,
    user_time: Optional[str] = None,
) -> Optional[str]:
    """
    Build a rich time context string with market awareness.

    Returns a string like:
        [TIME CONTEXT]: User's timezone: America/New_York |
        Current local time: 2026-02-03 14:30:00 EST |
        US market status: OPEN |
        Last completed trading day: 2026-02-02 |
        IMPORTANT: When the user asks for "today's" data, use data from the last
        completed trading day (2026-02-02) if markets are closed ...
    """
    if not user_timezone and not user_time:
        return None

    info_parts = []

    # Determine user's local time
    local_time = None
    if user_timezone and user_time:
        try:
            utc_time = datetime.fromisoformat(user_time.replace("Z", "+00:00"))
            user_tz = pytz.timezone(user_timezone)
            local_time = utc_time.astimezone(user_tz)
            info_parts.append(f"User's timezone: {user_timezone}")
            info_parts.append(f"Current local time: {local_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        except Exception as e:
            logger.warning(f"Error formatting time: {e}")
            if user_timezone:
                info_parts.append(f"User's timezone: {user_timezone}")
    elif user_timezone:
        info_parts.append(f"User's timezone: {user_timezone}")

    # Calculate market-aware context (always in Eastern Time)
    try:
        if local_time:
            et_now = local_time.astimezone(_US_EASTERN)
        else:
            et_now = datetime.now(_US_EASTERN)

        market_open = _is_market_open_now(et_now)
        market_status = "OPEN" if market_open else "CLOSED"
        info_parts.append(f"US market status: {market_status}")

        last_trading_day = _get_last_trading_day(et_now)
        info_parts.append(f"Last completed trading day: {last_trading_day}")

        today_str = et_now.strftime("%Y-%m-%d")
        if market_open:
            info_parts.append(
                f"'Today's data' = live intraday {today_str} (last close: {last_trading_day}); verify retrieved dates."
            )
        else:
            info_parts.append(
                f"'Today's data' = data from {last_trading_day} (last completed session); do not substitute other dates and verify retrieved dates match."
            )
    except Exception as e:
        logger.warning(f"Error building market context: {e}")

    if info_parts:
        return f"[TIME CONTEXT]: {' | '.join(info_parts)}"
    return None
