from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def _serialize(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_serialize(item) for item in value]
    return value


@dataclass(frozen=True)
class PricingLine:
    code: str
    label: str
    basis: str
    quantity: Decimal | int
    unit: str
    unit_price: Decimal
    amount: Decimal
    source: str


@dataclass(frozen=True)
class PricingResult:
    engine_version: str
    profile_version: int | str | None = None
    package_version: int | str | None = None
    workflow_version: int | str | None = None
    inputs: Any = None
    lines: tuple[PricingLine, ...] = ()
    gross_total: Decimal = Decimal("0")
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    status: str = "ok"
    mode: str = "estimate"
    block_subtotals: tuple[Any, ...] = ()
    discount_total: Decimal = Decimal("0")
    rounding_difference: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)
