"""Single source of truth for "what time is it" across the app.

Railway runs the app's clock in UTC. Every "today"/"this week" decision in
this project — which week to schedule, which date's menu to check, when a
reminder event should land — has to be computed in India time, or a job
running late in the UTC day would think it's already tomorrow in Kolkata.
Every caller goes through this module instead of a bare
`date.today()`/`datetime.now()`.
"""
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def week_start_ist(on: Optional[date] = None) -> date:
    """Monday of the week containing `on` (default: today, in IST)."""
    day = on or today_ist()
    return day - timedelta(days=day.weekday())
