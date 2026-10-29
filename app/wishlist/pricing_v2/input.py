from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from collections.abc import Mapping as ABCMapping, Sequence
import re
from typing import Any, Mapping


class PricingValidationError(ValueError):
    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code, self.message, self.field = code, message, field


def _error(field, message):
    raise PricingValidationError("invalid_input", message, field)


def _date(value, field):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    _error(field, "expected an ISO date")


def _time(value, field):
    if isinstance(value, time):
        if value.tzinfo is not None:
            _error(field, "timezone-aware times are not supported")
        return value.replace(microsecond=0)
    if isinstance(value, str):
        try:
            parsed = time.fromisoformat(value)
            if parsed.tzinfo is not None:
                _error(field, "timezone-aware times are not supported")
            return parsed.replace(microsecond=0)
        except ValueError:
            pass
    _error(field, "expected an ISO time")


def _datetime(value, field):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        if re.search(r"[+-]\d{2}(?::?\d{2})?$", value) and not re.search(r"[+-]\d{2}:\d{2}$", value):
            _error(field, "timezone offset must use ±HH:MM")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _error(field, "expected an ISO datetime")
    else:
        _error(field, "expected an ISO datetime")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    try:
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        _error(field, "expected a valid timezone")


def _decimal(value, field):
    if isinstance(value, (bool, float)):
        _error(field, "boolean is not a decimal")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        _error(field, "expected a decimal")
    if not parsed.is_finite() or parsed < 0:
        _error(field, "must be a nonnegative finite decimal")
    return parsed


def _nonnegative_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _error(field, "must be a nonnegative integer")
    return value


class FrozenMapping(ABCMapping):
    """Small immutable, deterministically ordered JSON mapping."""

    __slots__ = ("_items",)

    def __init__(self, items):
        self._items = tuple(items)

    def __getitem__(self, key):
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self):
        return (key for key, _ in self._items)

    def __len__(self):
        return len(self._items)

    def items(self):
        return self._items


def _freeze_json(value, field):
    if value is None:
        return None
    if isinstance(value, (str, int, bool, Decimal, date, time, datetime)):
        if isinstance(value, bool) or isinstance(value, int) or isinstance(value, str):
            return value
        if isinstance(value, (date, time, datetime, Decimal)):
            return value
    if isinstance(value, float):
        _error(field, "floats are not supported in contract payloads")
    if isinstance(value, ABCMapping):
        pairs = []
        for key, item in value.items():
            if not isinstance(key, str):
                _error(field, "mapping keys must be strings")
            pairs.append((key, _freeze_json(item, field)))
        return FrozenMapping(sorted(pairs, key=lambda pair: pair[0]))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item, field) for item in value)
    _error(field, f"unsupported contract value: {type(value).__name__}")


def _custom_items(value):
    if value is None or isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        _error("custom_items", "must be a sequence of mappings")
    frozen = []
    for item in value:
        if not isinstance(item, ABCMapping):
            _error("custom_items", "each item must be a mapping")
        frozen.append(_freeze_json(item, "custom_items"))
    return tuple(frozen)


@dataclass(frozen=True)
class PricingInput:
    event_date: date
    start_time: time
    end_time: time
    package_id: str
    selected_item_ids: tuple[str, ...] = ()
    guest_count: int = 0
    distance_km: Decimal | None = None
    setup_minutes: int | None = None
    teardown_minutes: int | None = None
    pricing_region: str | None = None
    requested_at: datetime | None = None
    discount_percent: Decimal | None = None
    discount_amount: Decimal | None = None
    custom_items: tuple[Any, ...] = ()
    event_type: str = ""
    with_sound: bool = True
    pricing_region_explicit: bool = field(default=False, init=False)

    def __post_init__(self):
        if type(self.with_sound) is not bool:
            _error('with_sound', 'must be a boolean')
        if not isinstance(self.event_type, str):
            _error("event_type", "must be a string")
        object.__setattr__(self, "event_type", self.event_type.strip().lower())
        object.__setattr__(self, "pricing_region_explicit", self.pricing_region is not None)
        if self.pricing_region is None:
            object.__setattr__(self, "pricing_region", "NW")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, requested_at: datetime | str) -> "PricingInput":
        if not isinstance(data, Mapping):
            raise PricingValidationError("invalid_input", "expected a mapping")
        required = ("event_date", "start_time", "end_time", "package_id")
        for field in required:
            if field not in data or data[field] is None or (isinstance(data[field], str) and not data[field].strip()):
                raise PricingValidationError("missing_input", f"missing required input: {field}", field)
        package = data["package_id"]
        if not isinstance(package, str) or not package.strip():
            _error("package_id", "must be a nonempty identifier")
        ids = data.get("selected_item_ids", ())
        if isinstance(ids, (str, bytes)) or not isinstance(ids, (list, tuple)):
            _error("selected_item_ids", "must be a sequence of identifiers")
        if any(not isinstance(item, str) or not item.strip() for item in ids):
            _error("selected_item_ids", "identifiers must be nonempty strings")
        region = data.get("pricing_region")
        if region is not None and (not isinstance(region, str) or not region.strip()):
            _error("pricing_region", "must be a region code")
        region = region.strip().upper() if region is not None else None
        event_type = data.get("event_type", "")
        if not isinstance(event_type, str):
            _error("event_type", "must be a string")
        return cls(
            event_date=_date(data["event_date"], "event_date"), start_time=_time(data["start_time"], "start_time"),
            end_time=_time(data["end_time"], "end_time"), package_id=package.strip(), selected_item_ids=tuple(ids),
            guest_count=_nonnegative_int(data.get("guest_count", 0), "guest_count"),
            distance_km=None if data.get("distance_km") is None else _decimal(data["distance_km"], "distance_km"),
            setup_minutes=None if data.get("setup_minutes") is None else _nonnegative_int(data["setup_minutes"], "setup_minutes"),
            teardown_minutes=None if data.get("teardown_minutes") is None else _nonnegative_int(data["teardown_minutes"], "teardown_minutes"), pricing_region=region,
            requested_at=_datetime(requested_at, "requested_at"),
            discount_percent=None if data.get("discount_percent") is None else _decimal(data["discount_percent"], "discount_percent"),
            discount_amount=None if data.get("discount_amount") is None else _decimal(data["discount_amount"], "discount_amount"),
            custom_items=_custom_items(data.get("custom_items", ())),
            event_type=event_type.strip().lower(),
            with_sound=data.get('with_sound', True),
        )
