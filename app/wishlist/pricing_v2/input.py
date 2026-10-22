from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
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
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _error(field, "expected an ISO datetime")
    else:
        _error(field, "expected an ISO datetime")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decimal(value, field):
    if isinstance(value, bool):
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


@dataclass(frozen=True)
class PricingInput:
    event_date: date
    start_time: time
    end_time: time
    package_id: str
    selected_item_ids: tuple[str, ...] = ()
    guest_count: int = 0
    distance_km: Decimal | None = None
    setup_minutes: int = 0
    teardown_minutes: int = 0
    pricing_region: str = "NW"
    requested_at: datetime | None = None
    discount_percent: Decimal | None = None
    discount_amount: Decimal | None = None
    custom_items: tuple[Any, ...] = ()

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
        region = data.get("pricing_region", "NW")
        if region is None or not isinstance(region, str):
            _error("pricing_region", "must be a region code")
        region = region.strip().upper() or "NW"
        return cls(
            event_date=_date(data["event_date"], "event_date"), start_time=_time(data["start_time"], "start_time"),
            end_time=_time(data["end_time"], "end_time"), package_id=package.strip(), selected_item_ids=tuple(ids),
            guest_count=_nonnegative_int(data.get("guest_count", 0), "guest_count"),
            distance_km=None if data.get("distance_km") is None else _decimal(data["distance_km"], "distance_km"),
            setup_minutes=_nonnegative_int(data.get("setup_minutes", 0), "setup_minutes"),
            teardown_minutes=_nonnegative_int(data.get("teardown_minutes", 0), "teardown_minutes"), pricing_region=region,
            requested_at=_datetime(requested_at, "requested_at"),
            discount_percent=None if data.get("discount_percent") is None else _decimal(data["discount_percent"], "discount_percent"),
            discount_amount=None if data.get("discount_amount") is None else _decimal(data["discount_amount"], "discount_amount"),
            custom_items=tuple(data.get("custom_items", ())),
        )
