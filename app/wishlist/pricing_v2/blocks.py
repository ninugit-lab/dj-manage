"""Pure standard pricing blocks.

All functions have the stable dispatcher signature ``(request, config,
subtotal, catalog, ...)`` and return a new :class:`BlockResult`; ``catalog``
is an immutable caller-supplied snapshot and is never queried or mutated.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from collections.abc import Mapping

from .calendar import CalendarFactor, select_tier
from .input import PricingInput, PricingValidationError
from .result import PricingLine


@dataclass(frozen=True)
class BlockResult:
    lines: tuple[PricingLine, ...]
    subtotal: Decimal


def _error(code, message, field=None):
    raise PricingValidationError(code, message, field)


def _decimal(value, field, *, allow_negative=False):
    if isinstance(value, (bool, float)):
        _error("invalid_input", f"{field} must be a Decimal", field)
    try:
        value = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception:
        _error("invalid_input", f"{field} must be a decimal", field)
    if not value.is_finite() or (not allow_negative and value < 0):
        _error("invalid_input", f"{field} must be nonnegative and finite", field)
    return value


def _subtotal(value):
    return _decimal(value, "subtotal")


def _catalog(catalog):
    if not isinstance(catalog, Mapping):
        _error("reference_error", "catalog must be a mapping", "catalog")
    packages, items = catalog.get("packages"), catalog.get("items")
    if not isinstance(packages, Mapping) or not isinstance(items, Mapping):
        _error("reference_error", "catalog requires packages and items mappings", "catalog")
    return packages, items


def _line(code, label, basis, quantity, unit, unit_price, amount, source):
    return PricingLine(code, label, basis, quantity, unit, unit_price, amount, source)


def _package(request, catalog):
    packages, _ = _catalog(catalog)
    package = packages.get(request.package_id)
    if not isinstance(package, Mapping):
        _error("reference_error", "package reference was not found", "package_id")
    return package


def _item(item_id, items):
    item = items.get(item_id)
    if not isinstance(item, Mapping):
        _error("reference_error", f"item reference was not found: {item_id}", "selected_item_ids")
    if not isinstance(item.get("label"), str) or not item["label"]:
        _error("invalid_input", "catalog item needs a label", "catalog")
    amount = _decimal(item.get("price", item.get("amount")), "catalog")
    return item, amount


def _minutes(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _error("invalid_input", f"{field} must be a nonnegative integer", field)
    return value


def package_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping) -> BlockResult:
    package = _package(request, catalog)
    amount = _decimal(package.get("base_price", package.get("price")), "catalog")
    label = package.get("label")
    if not isinstance(label, str) or not label:
        _error("invalid_input", "catalog package needs a label", "catalog")
    if amount == 0:
        return BlockResult((), _subtotal(subtotal))
    line = _line(f"package:{request.package_id}", label, "package base", Decimal("1"), "package", amount, amount,
                 str(package.get("source", "catalog")))
    return BlockResult((line,), _subtotal(subtotal) + amount)


def items_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping) -> BlockResult:
    package = _package(request, catalog)
    _, items = _catalog(catalog)
    included = package.get("included_item_ids", ())
    required = package.get("required_item_ids", ())
    if not isinstance(included, (tuple, list)) or not isinstance(required, (tuple, list)):
        _error("invalid_input", "package item ids must be sequences", "catalog")
    included, required = tuple(included), tuple(required)
    if any(not isinstance(value, str) or not value for value in included + required):
        _error("invalid_input", "package item ids must be strings", "catalog")
    for item_id in dict.fromkeys(included + required):
        _item(item_id, items)
    if not isinstance(request.selected_item_ids, tuple) or any(not isinstance(v, str) or not v for v in request.selected_item_ids):
        _error("invalid_input", "selected item ids must be nonempty strings", "selected_item_ids")
    chosen = tuple(dict.fromkeys(request.selected_item_ids + required))
    lines = []
    for item_id in chosen:
        item, amount = _item(item_id, items)
        if item_id in included:
            continue
        if not item.get("active", False) and item_id not in required:
            _error("reference_error", f"selected item is inactive: {item_id}", "selected_item_ids")
        if amount == 0:
            continue
        lines.append(_line(f"item:{item_id}", item["label"], str(item.get("category", "item")), Decimal("1"),
                           "item", amount, amount, str(item.get("source", "catalog"))))
    custom_codes = set()
    for custom in request.custom_items:
        if not isinstance(custom, Mapping) or not isinstance(custom.get("code"), str) or not isinstance(custom.get("label"), str):
            _error("invalid_input", "custom item needs code and label", "custom_items")
        if not custom["code"] or not custom["label"] or custom["code"] in custom_codes:
            _error("invalid_input", "custom item code and label must be nonempty and codes unique", "custom_items")
        custom_codes.add(custom["code"])
        amount = _decimal(custom.get("amount", custom.get("price")), "custom_items")
        if amount == 0:
            continue
        lines.append(_line(f"custom:{custom['code']}", custom["label"], "custom item", Decimal("1"), "item", amount,
                           amount, str(custom.get("source", "admin"))))
    return BlockResult(tuple(lines), _subtotal(subtotal) + sum((line.amount for line in lines), Decimal("0")))


def duration_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping) -> BlockResult:
    if request.start_time == request.end_time:
        _error("invalid_input", "start and end times must differ", "end_time")
    start = request.start_time.hour * 3600 + request.start_time.minute * 60 + request.start_time.second
    end = request.end_time.hour * 3600 + request.end_time.minute * 60 + request.end_time.second
    if end <= start:
        end += 86400
    hours = Decimal(end - start) / Decimal(3600)
    included = config.included_hours.value
    rate = config.extra_hour_rate.value
    if included is not None:
        included = _decimal(included, "included_hours")
    if rate is not None:
        rate = _decimal(rate, "extra_hour_rate")
    if included is None or rate is None or rate == 0:
        return BlockResult((), _subtotal(subtotal))
    extra = hours - included
    if extra <= 0:
        return BlockResult((), _subtotal(subtotal))
    quantity = extra.to_integral_value(rounding=ROUND_CEILING)
    amount = quantity * rate
    line = _line("duration:extra", "Additional hours", "duration beyond included hours", quantity, "hour", rate, amount, config.extra_hour_rate.source)
    return BlockResult((line,), _subtotal(subtotal) + amount)


def setup_teardown_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping, *, setup_minutes=None, teardown_minutes=None) -> BlockResult:
    rate = config.setup_teardown_hour_rate.value
    if rate is not None:
        rate = _decimal(rate, "setup_teardown_hour_rate")
    if rate is None or rate == 0:
        return BlockResult((), _subtotal(subtotal))
    setup = config.setup_minutes.value if request.setup_minutes is None else request.setup_minutes
    teardown = config.teardown_minutes.value if request.teardown_minutes is None else request.teardown_minutes
    setup = setup if setup_minutes is None else setup_minutes
    teardown = teardown if teardown_minutes is None else teardown_minutes
    if setup is None and teardown is None:
        return BlockResult((), _subtotal(subtotal))
    setup = 0 if setup is None else _minutes(setup, "setup_minutes")
    teardown = 0 if teardown is None else _minutes(teardown, "teardown_minutes")
    minutes = setup + teardown
    if not minutes:
        return BlockResult((), _subtotal(subtotal))
    quantity = Decimal(minutes) / Decimal(60)
    amount = quantity * rate
    line = _line("setup_teardown", "Setup and teardown", "setup and teardown time", quantity, "hour", rate, amount, config.setup_teardown_hour_rate.source)
    return BlockResult((line,), _subtotal(subtotal) + amount)


def travel_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping, *, km_rate=None) -> BlockResult:
    rate = config.km_rate.value if km_rate is None else km_rate
    included = config.included_km.value
    if rate is None:
        return BlockResult((), _subtotal(subtotal))
    rate = _decimal(rate, "km_rate")
    if rate == 0:
        return BlockResult((), _subtotal(subtotal))
    if included is None:
        _error("calculation_error", "included km is required when travel is charged", "included_km")
    if request.distance_km is None:
        _error("missing_input", "distance is required when travel is charged", "distance_km")
    distance = _decimal(request.distance_km, "distance_km")
    included = _decimal(included, "included_km")
    quantity = distance - included
    if quantity <= 0:
        return BlockResult((), _subtotal(subtotal))
    amount = quantity * rate
    line = _line("travel", "Travel", "one-way distance beyond included km", quantity, "km", rate, amount,
                 "override" if km_rate is not None else config.km_rate.source)
    return BlockResult((line,), _subtotal(subtotal) + amount)


def guest_tiers_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping) -> BlockResult:
    tiers = config.guest_tiers.value
    if tiers is None:
        return BlockResult((), _subtotal(subtotal))
    tier = select_tier(request.guest_count, tiers)
    if tier is None:
        _error("calculation_error", "no guest tier matches guest count", "guest_count")
    tier_value = _decimal(tier.value, "guest_tier.value", allow_negative=True)
    if tier_value == 0:
        return BlockResult((), _subtotal(subtotal))
    base = _subtotal(subtotal)
    amount = base * tier_value / Decimal(100)
    line = _line(f"guest_tier:{tier.lower_bound}", "Guest tier adjustment", "percentage of subtotal", Decimal("1"), "%", tier_value, amount, config.guest_tiers.source)
    return BlockResult((line,), base + amount)


def calendar_factors_block(request: PricingInput, config, subtotal: Decimal, factors: tuple[CalendarFactor, ...]) -> BlockResult:
    base = _subtotal(subtotal)
    lines = []
    for factor in factors:
        if not isinstance(factor, CalendarFactor):
            _error("invalid_input", "calendar factors must be CalendarFactor values", "factors")
        percent = _decimal(factor.percent, "factor.percent", allow_negative=True)
        if not all(isinstance(value, str) and value for value in (factor.code, factor.description, factor.source)):
            _error("invalid_input", "calendar factor metadata must be nonempty strings", "factors")
        amount = base * percent / Decimal(100)
        if amount:
            lines.append(_line(f"calendar:{factor.code}", factor.description, "percentage of subtotal", Decimal("1"), "%", percent, amount, factor.source))
            base += amount
    return BlockResult(tuple(lines), base)


def discount_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping) -> BlockResult:
    base = _subtotal(subtotal)
    percent = request.discount_percent
    absolute = request.discount_amount
    if percent is not None:
        percent = _decimal(percent, "discount_percent")
        if percent > 100:
            _error("invalid_input", "discount percent cannot exceed 100", "discount_percent")
    if absolute is not None:
        absolute = _decimal(absolute, "discount_amount")
    discount = Decimal("0")
    lines = []
    if percent:
        amount = min(base, base * percent / Decimal(100))
        if amount:
            discount += amount
            lines.append(_line("discount:percent", "Discount", "percentage discount", Decimal("1"), "%", percent, -amount, "request"))
    if absolute:
        amount = min(base - discount, absolute)
        if amount:
            discount += amount
            lines.append(_line("discount:absolute", "Discount", "absolute discount", Decimal("1"), "EUR", amount, -amount, "request"))
    return BlockResult(tuple(lines), base - discount)


def minimum_price_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping) -> BlockResult:
    base, minimum = _subtotal(subtotal), config.minimum_price.value
    if minimum is not None:
        minimum = _decimal(minimum, "minimum_price")
    if minimum is None or base >= minimum:
        return BlockResult((), base)
    amount = minimum - base
    line = _line("minimum_price", "Minimum price", "minimum price adjustment", Decimal("1"), "EUR", amount, amount, config.minimum_price.source)
    return BlockResult((line,), minimum)


def rounding_block(request: PricingInput, config, subtotal: Decimal, catalog: Mapping) -> BlockResult:
    base, step, mode = _subtotal(subtotal), config.rounding_step.value, config.rounding_mode.value
    if step is None or mode is None:
        return BlockResult((), base)
    if not isinstance(mode, str):
        _error("calculation_error", "unknown rounding mode", "rounding_mode")
    rounding = {"nearest": ROUND_HALF_UP, "up": ROUND_CEILING, "down": ROUND_FLOOR}.get(mode)
    if rounding is None:
        _error("calculation_error", "unknown rounding mode", "rounding_mode")
    step = _decimal(step, "rounding_step")
    if step == 0:
        _error("calculation_error", "rounding step must be positive", "rounding_step")
    total = (base / step).to_integral_value(rounding=rounding) * step
    difference = total - base
    if difference == 0:
        return BlockResult((), base)
    line = _line("rounding", "Rounding", f"{mode} to {step}", Decimal("1"), "EUR", difference, difference, config.rounding_step.source)
    return BlockResult((line,), total)
