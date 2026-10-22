"""Validate an ordered, caller-frozen pricing workflow without database access."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .input import PricingValidationError, _freeze_json


BLOCK_TYPES = ("package", "items", "duration", "setup_teardown", "travel", "guest_tiers",
               "calendar_factors", "rules", "formula", "discount", "minimum_price", "rounding")
DEFAULT_BLOCKS = tuple({"type": name} for name in BLOCK_TYPES)
_REFERENCE_KEYS = {"package": ("package_id", "packages"), "items": ("item_ids", "items"),
                   "rules": ("rule_ids", "rules"), "formula": ("formula_id", "formulas")}


def _invalid(message, field="workflow"):
    raise PricingValidationError("invalid_input", message, field)


def _reference(message):
    raise PricingValidationError("reference_error", message, "workflow")


@dataclass(frozen=True)
class ValidatedWorkflow:
    blocks: tuple[Mapping, ...]
    id: str | int | None = None
    version: str | int | None = None


def validate_workflow(blocks, references) -> ValidatedWorkflow:
    """Validate order, shapes and explicitly selected catalog identities."""
    if isinstance(blocks, ValidatedWorkflow):
        blocks = blocks.blocks
    if isinstance(blocks, (str, bytes)) or not isinstance(blocks, Sequence):
        _invalid("blocks must be a sequence")
    if not isinstance(references, Mapping):
        _reference("references must be a mapping")
    for name in ("packages", "items", "rules", "formulas"):
        if name in references and not isinstance(references[name], Mapping):
            _reference(f"{name} must be a mapping")
    result, seen = [], set()
    for raw in blocks:
        if not isinstance(raw, Mapping):
            _invalid("each block must be a mapping")
        name = raw.get("type")
        if not isinstance(name, str) or name not in BLOCK_TYPES:
            _invalid("unknown block type")
        if name in seen:
            _invalid("duplicate singleton block")
        seen.add(name)
        permitted = {"type", "config", "id", "version"}
        if name in _REFERENCE_KEYS:
            permitted.add(_REFERENCE_KEYS[name][0])
        if set(raw) - permitted or ("config" in raw and not isinstance(raw["config"], Mapping)):
            _invalid("invalid block configuration")
        for key in ("id", "version"):
            if key in raw and (type(raw[key]) not in (str, int) or not raw[key]):
                _invalid(f"invalid block {key}")
        if name not in ("package", "items") and not seen.intersection(("package", "items")):
            _invalid("price-building block must precede adjustments")
        if name == "items" and "package" not in seen:
            _invalid("package must precede items")
        if name == "rounding" and len(result) != len(blocks) - 1:
            _invalid("rounding must be last")
        if name in _REFERENCE_KEYS:
            key, collection = _REFERENCE_KEYS[name]
            if key in raw:
                ids = raw[key]
                if name in ("items", "rules"):
                    if not isinstance(ids, (list, tuple)) or any(type(v) not in (str, int) or not v for v in ids):
                        _invalid(f"{key} must be an identifier sequence")
                elif type(ids) not in (str, int) or not ids:
                    _invalid(f"{key} must be an identifier")
                selected = ids if name in ("items", "rules") else (ids,)
                for identity in selected:
                    if identity not in references.get(collection, {}):
                        _reference(f"missing {collection} reference: {identity}")
        result.append(_freeze_json(raw, "workflow"))
    if not seen.intersection(("package", "items")):
        _invalid("workflow needs a price-building block")
    return ValidatedWorkflow(tuple(result))
