"""Deterministic, ORM-free V2 pricing dispatcher (gross estimate; never adds VAT)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields
from decimal import Decimal, DecimalException, ROUND_HALF_UP

from .advanced import apply_formula, apply_rules
from .blocks import (BlockResult, calendar_factors_block, discount_block, duration_block,
                     guest_tiers_block, items_block, minimum_price_block, package_block,
                     rounding_block, setup_teardown_block, travel_block)
from .calendar import calendar_factors
from .config import ResolvedPricingConfig
from .input import PricingInput, PricingValidationError
from .result import PricingLine, PricingResult, _serialize
from .workflow import DEFAULT_BLOCKS, ValidatedWorkflow, validate_workflow


_BLOCKS = {"package": package_block, "items": items_block, "duration": duration_block,
           "setup_teardown": setup_teardown_block, "travel": travel_block,
           "guest_tiers": guest_tiers_block, "discount": discount_block,
           "minimum_price": minimum_price_block, "rounding": rounding_block}
_MONEY = Decimal("0.01")


def _invalid(message, field):
    raise PricingValidationError("invalid_input", message, field)


def _reference(message, field="catalog"):
    raise PricingValidationError("reference_error", message, field)


def _snapshot(request, config, workflow, catalog, region):
    package_factor = config.included_hours
    selected_rules = set()
    selected_formulas = set()
    selections = []
    for block in workflow.blocks:
        if block["type"] == "rules":
            identities = block.get("rule_ids", tuple(identity for identity, rule in catalog.get("rules", {}).items()
                                                     if isinstance(rule, Mapping) and rule.get("is_active", True)))
            selected_rules.update(identities)
            selections.append({"type": "rules", "selection": "explicit" if "rule_ids" in block else "active_catalog",
                               "selected_ids": list(identities)})
        if block["type"] == "formula":
            identity = block.get("formula_id")
            if identity is not None:
                selected_formulas.add(identity)
            selections.append({"type": "formula", "selection": "explicit" if identity is not None else "no_active_formula",
                               "selected_id": identity})
    package = catalog["packages"][request.package_id]
    item_ids = set(request.selected_item_ids) | set(package.get("included_item_ids", ())) | set(package.get("required_item_ids", ()))
    snapshot = {"packages": {str(request.package_id): package},
                "items": {str(key): value for key, value in catalog["items"].items() if key in item_ids},
                "rules": {str(key): value for key, value in catalog.get("rules", {}).items() if key in selected_rules},
                "formulas": {str(key): value for key, value in catalog.get("formulas", {}).items() if key in selected_formulas}}
    return {**{field.name: region if field.name == "pricing_region" else getattr(request, field.name) for field in fields(request)},
             "profile_id": catalog.get("profile_id"), "package_snapshot_id": request.package_id,
             "catalog_snapshot": snapshot,
             "advanced_selections": selections,
            "workflow_id": workflow.id, "workflow_version": workflow.version,
            "profile_version": package_factor.profile_version,
            "package_version": catalog["packages"][request.package_id].get("version", package_factor.package_version),
            "workflow_blocks": workflow.blocks,
            "resolved_config": {field.name: {"value": _serialize(getattr(config, field.name).value),
                                             "source": getattr(config, field.name).source}
                                for field in fields(config) if field.name != "normalized"}}


class PricingEngineV2:
    VERSION = "2"

    @staticmethod
    def calculate(request: PricingInput, config: ResolvedPricingConfig,
                  workflow: ValidatedWorkflow, catalog: Mapping) -> PricingResult:
        if not isinstance(request, PricingInput):
            _invalid("expected normalized pricing input", "request")
        if not isinstance(config, ResolvedPricingConfig):
            _invalid("expected resolved pricing configuration", "config")
        if not isinstance(catalog, Mapping):
            _reference("catalog must be a mapping")
        for key in ("profile_id", "workflow_id", "workflow_version"):
            if key in catalog and catalog[key] is not None and (type(catalog[key]) not in (str, int) or not catalog[key]):
                _invalid(f"invalid {key}", "catalog")
        if workflow is None:
            workflow = validate_workflow(DEFAULT_BLOCKS, catalog)
        elif not isinstance(workflow, ValidatedWorkflow):
            workflow = validate_workflow(workflow, catalog)
        else:
            workflow = ValidatedWorkflow(validate_workflow(workflow.blocks, catalog).blocks, workflow.id, workflow.version)
        for field in ("id", "version"):
            value = getattr(workflow, field)
            if value is not None and (type(value) not in (str, int) or not value):
                _invalid(f"invalid workflow {field}", "workflow")
        for key in ("packages", "items"):
            if not isinstance(catalog.get(key), Mapping):
                _reference(f"{key} must be a mapping")
        package = catalog["packages"].get(request.package_id)
        if not isinstance(package, Mapping):
            _reference("package not found", "package_id")
        if "version" in package and (type(package["version"]) not in (str, int) or not package["version"]):
            _invalid("invalid package version", "catalog")
        # Validate package-owned item lists before inspecting workflow selections or unpacking IDs.
        for key in ("included_item_ids", "required_item_ids"):
            ids = package.get(key, ())
            if not isinstance(ids, (tuple, list)) or any(not isinstance(i, str) or not i for i in ids):
                _invalid(f"invalid {key}", "catalog")
        for block in workflow.blocks:
            if block["type"] == "package" and "package_id" in block and block["package_id"] != request.package_id:
                _reference("workflow package does not match request", "package_id")
            if block["type"] == "items":
                # Explicit item_ids are a frozen copy of the user selection, not a
                # substitute for it. Required items are always supplied by the package.
                if "item_ids" in block and (len(block["item_ids"]) != len(set(block["item_ids"]))
                                            or set(block["item_ids"]) != set(request.selected_item_ids)):
                    _reference("workflow item selection differs from request", "selected_item_ids")
        for identity in (*request.selected_item_ids, *package.get("included_item_ids", ()), *package.get("required_item_ids", ())):
            if not isinstance(catalog["items"].get(identity), Mapping):
                _reference(f"item not found: {identity}", "selected_item_ids")
        if request.requested_at is None:
            _invalid("requested_at must be timezone-aware", "requested_at")
        if request.start_time == request.end_time:
            _invalid("start and end times must differ", "end_time")
        region = request.pricing_region if request.pricing_region_explicit else config.holiday_region.value
        factors = calendar_factors(request.event_date, request.requested_at, region, config)
        current = Decimal("0")
        lines = ()
        subtotals = []
        discount = Decimal("0")
        rounded = Decimal("0")
        for block in workflow.blocks:
            name = block["type"]
            before = current
            if name in _BLOCKS:
                result = _BLOCKS[name](request, config, current, catalog)
            elif name == "calendar_factors":
                result = calendar_factors_block(request, config, current, factors)
            elif name == "rules":
                rules = catalog.get("rules", {})
                if not isinstance(rules, Mapping):
                    _reference("rules must be a mapping")
                rule_ids = block.get("rule_ids", tuple(identity for identity, rule in rules.items()
                                                      if isinstance(rule, Mapping) and rule.get("is_active", True)))
                selected = []
                for identity in rule_ids:
                    rule = rules.get(identity)
                    if not isinstance(rule, Mapping):
                        _reference(f"rule not found: {identity}")
                    if type(rule.get("sort_order", 0)) is not int:
                        _invalid("rule sort order must be an integer", "rules")
                    selected.append(rule)
                result = apply_rules(request, current, lines, tuple(selected), event_type=request.event_type)
            elif name == "formula":
                identity = block.get("formula_id")
                formulas = catalog.get("formulas", {})
                if not isinstance(formulas, Mapping):
                    _reference("formulas must be a mapping")
                if identity is None:
                    result = BlockResult((), current)
                else:
                    formula = formulas.get(identity)
                    if not isinstance(formula, Mapping):
                        _reference(f"formula not found: {identity}")
                    result = apply_formula(request, current, lines, formula)
            else:
                _invalid("unknown workflow block", "workflow")
            current = result.subtotal
            lines += result.lines
            if name == "discount":
                discount += before - current
            if name == "rounding":
                rounded += current - before
            subtotals.append({"type": name, "subtotal": current})
        try:
            gross = current.quantize(_MONEY, rounding=ROUND_HALF_UP)
        except DecimalException as exc:
            raise PricingValidationError("invalid_input", "invalid price precision", "gross_total") from exc
        difference = gross - current
        if difference:
            lines += (PricingLine("rounding:cent", "Cent rounding", "final cent quantization",
                                  Decimal("1"), "EUR", difference, difference, "engine"),)
            rounded += difference
            subtotals[-1]["subtotal"] = gross
        return PricingResult(engine_version=PricingEngineV2.VERSION,
                             profile_version=config.included_hours.profile_version,
                             package_version=package.get("version", config.included_hours.package_version),
                              workflow_version=workflow.version, inputs=_snapshot(request, config, workflow, catalog, region),
                             lines=lines, gross_total=gross, block_subtotals=tuple(subtotals),
                             discount_total=discount, rounding_difference=rounded)
