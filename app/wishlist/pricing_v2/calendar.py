"""Pure calendar and tier factors for pricing v2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

import holidays

from .config import ResolvedPricingConfig, Tier, _GERMAN_REGIONS
from .input import PricingValidationError


@dataclass(frozen=True)
class CalendarFactor:
    """A calendar adjustment; percent is a Decimal percentage, not price arithmetic."""

    code: str
    percent: Decimal
    source: str
    profile_version: int | str
    package_version: object | None
    description: str


def select_tier(value: int, tiers: Iterable[Tier]) -> Tier | None:
    """Return the unique inclusive tier containing value, if any."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PricingValidationError("invalid_input", "must be a nonnegative integer", "value")
    for tier in tiers:
        if value >= tier.lower_bound and (tier.upper_bound is None or value <= tier.upper_bound):
            return tier
    return None


def _factor(code: str, configured, percent: Decimal, description: str) -> CalendarFactor:
    return CalendarFactor(code, percent, configured.source, configured.profile_version,
                          configured.package_version, description)


def calendar_factors(event_date: date, requested_at: datetime, region: str | None,
                     config: ResolvedPricingConfig) -> tuple[CalendarFactor, ...]:
    """Resolve applicable calendar/lead-time factors without performing price math."""
    if isinstance(event_date, datetime) or not isinstance(event_date, date):
        raise PricingValidationError("invalid_input", "expected an event date", "event_date")
    if not isinstance(requested_at, datetime) or requested_at.tzinfo is None:
        raise PricingValidationError("invalid_input", "requested_at must be timezone-aware", "requested_at")

    chosen_region = region if region is not None else config.holiday_region.value
    if not isinstance(chosen_region, str) or chosen_region not in _GERMAN_REGIONS:
        raise PricingValidationError("invalid_input", "unknown German holiday subdivision", "region")
    try:
        holiday_dates = holidays.Germany(years=event_date.year, subdiv=chosen_region)
    except (KeyError, ValueError) as exc:
        raise PricingValidationError("invalid_input", "unknown German holiday subdivision", "region") from exc

    result: list[CalendarFactor] = []
    weekday = event_date.weekday()
    weekend = (("saturday", config.saturday_percent, 5), ("sunday", config.sunday_percent, 6))
    for code, configured, day in weekend:
        if weekday == day and configured.value is not None and configured.value != 0:
            result.append(_factor(code, configured, configured.value, configured.description))
    if event_date in holiday_dates and config.holiday_percent.value is not None and config.holiday_percent.value != 0:
        result.append(_factor("holiday", config.holiday_percent, config.holiday_percent.value,
                              holiday_dates.get(event_date, "Public holiday")))

    for season in (() if config.seasons.value is None else config.seasons.value):
        if season.includes(event_date.strftime("%m-%d")) and season.percent != 0:
            result.append(_factor(f"season:{season.identifier}", config.seasons, season.percent, season.label))

    if config.lead_time_tiers.value is not None:
        local_requested_date = requested_at.astimezone(ZoneInfo("Europe/Berlin")).date()
        lead_days = max(0, (event_date - local_requested_date).days)
        tier = select_tier(lead_days, config.lead_time_tiers.value)
        if tier is not None and tier.value != 0:
            result.append(_factor(f"lead_time:{tier.lower_bound}", config.lead_time_tiers, tier.value,
                                  f"{lead_days} days before event"))
    return tuple(result)
