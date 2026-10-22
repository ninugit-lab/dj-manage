"""Pure, immutable resolution of pricing profile and package configuration."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re
from typing import Any

from .input import PricingValidationError, _decimal, _nonnegative_int


_SCALAR_DECIMALS = (
    "included_hours", "extra_hour_rate", "setup_teardown_hour_rate", "included_km",
    "km_rate", "saturday_percent", "sunday_percent", "holiday_percent", "minimum_price",
    "rounding_step",
)
_FIELDS = _SCALAR_DECIMALS + (
    "setup_minutes", "teardown_minutes", "holiday_region", "seasons", "lead_time_tiers",
    "guest_tiers", "rounding_mode",
)
_GERMAN_REGIONS = frozenset({"BW", "BY", "BE", "BB", "HB", "HH", "HE", "MV", "NI", "NW", "RP", "SL", "SN", "ST", "SH", "TH"})


def _invalid(field: str, message: str) -> None:
    raise PricingValidationError("invalid_input", message, field)


@dataclass(frozen=True)
class Season:
    identifier: str
    label: str
    start: str
    end: str
    percent: Decimal

    def includes(self, month_day: str) -> bool:
        point = _month_day(month_day, "seasons")
        start, end = _month_day(self.start, "seasons"), _month_day(self.end, "seasons")
        return start <= point <= end if start <= end else point >= start or point <= end


@dataclass(frozen=True)
class Tier:
    lower_bound: int
    upper_bound: int | None
    value: Decimal


@dataclass(frozen=True)
class Factor:
    value: Any
    source: str
    profile_version: Any
    package_version: Any | None
    description: str


@dataclass(frozen=True)
class NormalizedProfileConfig:
    included_hours: Decimal
    extra_hour_rate: Decimal
    setup_minutes: int
    teardown_minutes: int
    setup_teardown_hour_rate: Decimal
    included_km: Decimal
    km_rate: Decimal
    saturday_percent: Decimal
    sunday_percent: Decimal
    holiday_percent: Decimal
    holiday_region: str
    seasons: tuple[Season, ...]
    lead_time_tiers: tuple[Tier, ...]
    guest_tiers: tuple[Tier, ...]
    minimum_price: Decimal
    rounding_mode: str
    rounding_step: Decimal


@dataclass(frozen=True)
class ResolvedPricingConfig:
    normalized: NormalizedProfileConfig
    included_hours: Factor
    extra_hour_rate: Factor
    setup_minutes: Factor
    teardown_minutes: Factor
    setup_teardown_hour_rate: Factor
    included_km: Factor
    km_rate: Factor
    saturday_percent: Factor
    sunday_percent: Factor
    holiday_percent: Factor
    holiday_region: Factor
    seasons: Factor
    lead_time_tiers: Factor
    guest_tiers: Factor
    minimum_price: Factor
    rounding_mode: Factor
    rounding_step: Factor


def _month_day(value: Any, field: str) -> tuple[int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}-\d{2}", value):
        _invalid(field, "expected ISO month-day")
    try:
        parsed = date.fromisoformat(f"2000-{value}")
    except ValueError:
        _invalid(field, "expected valid month-day")
    return parsed.month, parsed.day


def _seasons(value: Any) -> tuple[Season, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _invalid("seasons", "must be a sequence of mappings")
    result, identifiers, occupied = [], set(), set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"identifier", "label", "start", "end", "percent"}:
            _invalid("seasons", "each season needs identifier, label, start, end, and percent")
        identifier, label = item["identifier"], item["label"]
        if not all(isinstance(v, str) and v.strip() for v in (identifier, label)) or identifier in identifiers:
            _invalid("seasons", "identifiers and labels must be stable, nonempty, and unique")
        start, end = _month_day(item["start"], "seasons"), _month_day(item["end"], "seasons")
        start_day = date(2000, *start).timetuple().tm_yday
        end_day = date(2000, *end).timetuple().tm_yday
        days = range(start_day, end_day + 1) if start_day <= end_day else tuple(range(start_day, 367)) + tuple(range(1, end_day + 1))
        if occupied.intersection(days):
            _invalid("seasons", "inclusive season ranges must not overlap")
        occupied.update(days)
        identifiers.add(identifier)
        result.append(Season(identifier, label, item["start"], item["end"], _decimal(item["percent"], "seasons")))
    return tuple(result)


def _tiers(value: Any, field: str) -> tuple[Tier, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        _invalid(field, "must be a sequence of mappings")
    result = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"lower_bound", "upper_bound", "value"}:
            _invalid(field, "each tier needs lower_bound, upper_bound, and value")
        low = _nonnegative_int(item["lower_bound"], field)
        high = item["upper_bound"]
        if high is not None:
            high = _nonnegative_int(high, field)
            if high < low:
                _invalid(field, "upper bound must not be below lower bound")
        result.append(Tier(low, high, _decimal(item["value"], field)))
    ordered = sorted(result, key=lambda tier: tier.lower_bound)
    for previous, current in zip(ordered, ordered[1:]):
        if previous.upper_bound is None or current.lower_bound <= previous.upper_bound:
            _invalid(field, "inclusive tier ranges must not overlap")
    return tuple(ordered)


def _normalize(field: str, value: Any) -> Any:
    if field in _SCALAR_DECIMALS:
        parsed = _decimal(value, field)
        if field == "rounding_step" and not parsed:
            _invalid(field, "must be strictly positive")
        return parsed
    if field in ("setup_minutes", "teardown_minutes"):
        return _nonnegative_int(value, field)
    if field == "holiday_region":
        if value is None:
            return "NW"
        if not isinstance(value, str) or value not in _GERMAN_REGIONS:
            _invalid(field, "must be an uppercase German subdivision code")
        return value
    if field == "rounding_mode":
        if value not in {"nearest", "up", "down"}:
            _invalid(field, "must be nearest, up, or down")
        return value
    if field == "seasons":
        return _seasons(value)
    return _tiers(value, field)


def _reference(value: Any, role: str, json_name: str) -> Mapping:
    if value is None or getattr(value, "id", None) is None or getattr(value, "version", None) is None:
        raise PricingValidationError("reference_error", f"invalid {role} reference")
    payload = getattr(value, json_name, None)
    if not isinstance(payload, Mapping):
        raise PricingValidationError("reference_error", f"{role} {json_name} must be a mapping")
    return payload


def resolve_pricing_config(profile: Any, package: Any = None) -> ResolvedPricingConfig:
    config = _reference(profile, "profile", "config_json")
    if set(config) != set(_FIELDS):
        _invalid("config_json", "profile config has missing or unknown keys")
    normalized = NormalizedProfileConfig(**{field: _normalize(field, config[field]) for field in _FIELDS})
    overrides = {} if package is None else _reference(package, "package", "overrides_json")
    if not set(overrides).issubset(_FIELDS):
        _invalid("overrides_json", "package overrides contain unknown factors")
    factors = {}
    for field in _FIELDS:
        global_value = getattr(normalized, field)
        entry = overrides.get(field)
        if entry is None:
            factors[field] = Factor(global_value, "global", profile.version, None, "global profile configuration")
            continue
        if not isinstance(entry, Mapping) or set(entry) - {"mode", "value"} or "mode" not in entry:
            _invalid("overrides_json", "override must be a mode/value mapping")
        mode = entry["mode"]
        has_value = "value" in entry
        if mode == "inherit" and not has_value:
            factors[field] = Factor(global_value, "global", profile.version, None, "global profile configuration")
        elif mode == "override" and has_value:
            factors[field] = Factor(_normalize(field, entry["value"]), "package_override", profile.version, package.version, "package override")
        elif mode == "disabled" and not has_value:
            factors[field] = Factor(None, "package_disabled", profile.version, package.version, "package disabled")
        else:
            _invalid("overrides_json", "invalid override mode or value")
    return ResolvedPricingConfig(normalized=normalized, **factors)
