from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from .input import FrozenMapping, _freeze_json


def _serialize(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: _serialize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, FrozenMapping):
        return {key: _serialize(item) for key, item in value.items()}
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

    def __post_init__(self):
        if not all(isinstance(value, str) for value in (self.code, self.label, self.basis, self.unit, self.source)):
            raise TypeError("pricing line scalar fields must be strings")
        for field_name in ("quantity", "unit_price", "amount"):
            value = getattr(self, field_name)
            if isinstance(value, float) or isinstance(value, bool) or not isinstance(value, (int, Decimal)):
                raise TypeError(f"{field_name} must be an integer or Decimal")


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

    def __post_init__(self):
        object.__setattr__(self, "inputs", None if self.inputs is None else _freeze_json(self.inputs, "inputs"))
        object.__setattr__(self, "block_subtotals", _freeze_json(self.block_subtotals, "block_subtotals"))
        object.__setattr__(self, "lines", tuple(self.lines))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "errors", tuple(self.errors))
        for field_name in ("gross_total", "discount_total", "rounding_difference"):
            value = getattr(self, field_name)
            if isinstance(value, float) or isinstance(value, bool) or not isinstance(value, Decimal):
                raise TypeError(f"{field_name} must be Decimal")

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)
