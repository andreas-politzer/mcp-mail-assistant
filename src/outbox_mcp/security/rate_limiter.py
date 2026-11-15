"""Limits for preparing and sending. The counting itself happens inside the database
transaction (see storage/database.py) so that parallel calls cannot get around it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

HOUR = 3600
DAY = 86400

# Hard ceilings. They are part of the code: no setting, no database value and no admin request can
# lift a send limit above them. Whatever is stored is cut down to these values when it is used.
HARD_MAX_SENDS_PER_HOUR = 100
HARD_MAX_SENDS_PER_DAY = 1000


@dataclass(frozen=True)
class RateLimits:
    max_prepares_per_hour: int
    max_sends_per_hour: int
    max_sends_per_day: int


def prepare_limit_reached(prepared_last_hour: int, limits: RateLimits) -> bool:
    return prepared_last_hour >= limits.max_prepares_per_hour


def send_limit_reason(sends_last_hour: int, sends_last_day: int, limits: RateLimits) -> Optional[str]:
    """Return a human-readable reason if no further send is allowed, otherwise None."""
    if sends_last_hour >= limits.max_sends_per_hour:
        return f"The limit of {limits.max_sends_per_hour} sends per hour has been reached."
    if sends_last_day >= limits.max_sends_per_day:
        return f"The limit of {limits.max_sends_per_day} sends per day has been reached."
    return None


def clamp(limits: RateLimits) -> RateLimits:
    """The same limits, cut down to the hard ceilings (and never below zero)."""
    return RateLimits(
        max_prepares_per_hour=limits.max_prepares_per_hour,
        max_sends_per_hour=max(0, min(limits.max_sends_per_hour, HARD_MAX_SENDS_PER_HOUR)),
        max_sends_per_day=max(0, min(limits.max_sends_per_day, HARD_MAX_SENDS_PER_DAY)),
    )
