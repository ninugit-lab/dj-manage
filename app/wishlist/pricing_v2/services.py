"""ORM boundary for V2 pricing; the engine only sees frozen scalar catalogs."""
from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from wishlist.models import (Event, EventPriceCalculation, PriceCalculationRevision, PriceItem,
                             PricingFormula, PricingPackage, PricingPackageOverride,
                             PricingProfile, PricingRule, PricingWorkflow)

from .config import resolve_pricing_config
from .engine import PricingEngineV2
from .input import PricingInput, PricingValidationError
from .workflow import DEFAULT_BLOCKS, ValidatedWorkflow, validate_workflow


def _reference(name, field=None):
    raise PricingValidationError("reference_error", f"missing or inactive {name}", field)


def _identifier(value, field):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit() or int(value) < 1:
        _reference(field, field)
    return str(int(value))


def _legacy_blocks(raw, package_id, catalog):
    """Translate V1 blocks without activating adjustments absent in the source."""
    if not isinstance(raw, list):
        raise PricingValidationError("invalid_input", "workflow must be a list", "workflow")
    declared = {block.get("type") for block in raw if isinstance(block, Mapping)}
    # The canonical V2 minimum pipeline includes items, even if the old
    # package-only workflow omitted that block. Other blocks are opt-in.
    blocks = [dict(block) for block in DEFAULT_BLOCKS
              if block["type"] in ("package", "items", "rules") or block["type"] in declared]
    if "rules" not in declared:
        next(block for block in blocks if block["type"] == "rules")["rule_ids"] = []
    positions = {block["type"]: block for block in blocks}
    seen = set()
    for block in raw:
        if not isinstance(block, Mapping) or not isinstance(block.get("type"), str):
            raise PricingValidationError("invalid_input", "invalid workflow block", "workflow")
        name = block["type"]
        if name not in positions or name in seen or set(block) - {"type", "label", "config", "id", "version"}:
            raise PricingValidationError("invalid_input", "unsupported workflow block", "workflow")
        seen.add(name)
        config = block.get("config", {})
        if not isinstance(config, Mapping):
            raise PricingValidationError("invalid_input", "invalid workflow config", "workflow")
        references = {"package": "package_id", "items": "item_ids", "rules": "rule_ids", "formula": "formula_id"}
        allowed = {references[name]} if name in references else set()
        if set(config) - allowed:
            raise PricingValidationError("invalid_input", "unsupported workflow config", "workflow")
        target = positions[name]
        for key in ("id", "version"):
            if key in block:
                target[key] = block[key]
        if name == "package" and "package_id" in config:
            identity = _identifier(config["package_id"], "workflow")
            if identity != package_id or identity not in catalog["packages"]:
                _reference("workflow package", "workflow")
            target["package_id"] = identity
        elif name in ("items", "rules") and allowed.intersection(config):
            key = references[name]
            if not isinstance(config[key], (list, tuple)):
                raise PricingValidationError("invalid_input", "invalid workflow references", "workflow")
            target[key] = [_identifier(value, "workflow") for value in config[key]]
        elif name == "formula" and "formula_id" in config:
            target["formula_id"] = _identifier(config["formula_id"], "workflow")
    return blocks


def _calculate(payload, *, public, actor=None):
    # Strip browser-calculated values; never copy a caller-supplied price into the catalog.
    if public and payload.get("custom_items"):
        raise PricingValidationError("invalid_input", "custom items require an administrator", "custom_items")
    request = PricingInput.from_mapping(payload, requested_at=timezone.now())
    package_id = _identifier(request.package_id, "package_id")
    profiles = list(PricingProfile.objects.filter(status=PricingProfile.Status.ACTIVE)[:2])
    if len(profiles) != 1:
        _reference("active profile", "profile")
    profile = profiles[0]
    package = PricingPackage.objects.filter(pk=package_id, is_active=True, **({"is_public": True} if public else {})).first()
    if package is None:
        _reference("package", "package_id")
    workflows = list(PricingWorkflow.objects.filter(is_active=True, is_default=True)[:2])
    if len(workflows) != 1:
        _reference("default workflow", "workflow")
    workflow = workflows[0]
    override = PricingPackageOverride.objects.filter(profile=profile, package=package).first()
    resolved = resolve_pricing_config(profile, SimpleNamespace(
        id=package.pk, version=package.version,
        overrides_json=override.overrides_json if override else {}))
    selected = tuple(_identifier(identity, "selected_item_ids") for identity in request.selected_item_ids)
    included = tuple(str(item.pk) for item in package.included_items.all())
    required = tuple(str(item.pk) for item in PriceItem.objects.filter(is_active=True, is_required=True,
                      **({"is_public": True} if public else {})))
    ids = set(selected + included + required)
    available = PriceItem.objects.filter(pk__in=ids, is_active=True,
                                         **({"is_public": True} if public else {}))
    items = {str(item.pk): {"label": item.name, "price": str(item.price), "active": item.is_active,
                           "is_public": item.is_public, "is_required": item.is_required,
                           "category": item.category, "description": item.description,
                           "sort_order": item.sort_order} for item in available}
    if ids - set(items):
        _reference("item", "selected_item_ids")
    rules = {str(rule.pk): {"id": str(rule.pk), "name": rule.name, "label": rule.name,
                          "description": rule.description, "condition_json": rule.condition_json,
                          "effect_type": rule.effect_type, "effect_value": str(rule.effect_value),
                          "applies_to": rule.applies_to, "sort_order": rule.sort_order, "is_active": True}
             for rule in PricingRule.objects.filter(is_active=True)}
    formulas = {str(formula.pk): {"id": str(formula.pk), "name": formula.name,
                                 "description": formula.description, "expression": formula.expression,
                                 "mode": formula.mode, "is_active": True}
                for formula in PricingFormula.objects.filter(is_active=True)}
    catalog = {"profile_id": profile.pk, "packages": {package_id: {
        "label": package.name, "base_price": str(package.base_price), "version": package.version,
        "description": package.description, "included_item_ids": included,
        "required_item_ids": required, "is_active": package.is_active, "is_public": package.is_public}},
        "items": items, "rules": rules, "formulas": formulas}
    blocks = _legacy_blocks(workflow.workflow_json, package_id, catalog)
    # V1 configuration cannot silently pick a different item selection.
    for block in blocks:
        if block["type"] == "items" and "item_ids" in block and set(block["item_ids"]) != set(selected):
            _reference("workflow item selection", "workflow")
    validated = validate_workflow(blocks, catalog)
    normalized = PricingInput.from_mapping({**payload, "package_id": package_id,
                                            "selected_item_ids": list(selected)},
                                           requested_at=request.requested_at)
    return PricingEngineV2.calculate(normalized, resolved,
        ValidatedWorkflow(validated.blocks, workflow.pk, workflow.version), catalog)


def calculate_payload(payload, *, public, actor=None):
    """Calculate a request from authoritative active catalog data."""
    if not isinstance(payload, Mapping):
        raise PricingValidationError("invalid_input", "expected a mapping", "payload")
    return _calculate(payload, public=public, actor=actor)


def calculate_event(event, *, actor=None):
    """Use an event's raw fields and its saved admin selections."""
    try:
        calc = EventPriceCalculation.objects.select_related("package").get(event=event)
    except EventPriceCalculation.DoesNotExist:
        _reference("event calculation", "event")
    if calc.package_id is None:
        _reference("event package", "package_id")
    custom = []
    for index, item in enumerate(calc.custom_items_json or []):
        if not isinstance(item, Mapping):
            raise PricingValidationError("invalid_input", "invalid custom item", "custom_items")
        custom.append({"code": item.get("code", f"event-{index + 1}"),
                       "label": item.get("label", item.get("name")),
                       "amount": item.get("amount", item.get("price"))})
    data = {"event_date": event.date, "start_time": event.time_start, "end_time": event.time_end,
            "package_id": str(calc.package_id), "selected_item_ids": [str(item.pk) for item in calc.selected_items.all()],
            "guest_count": event.guest_count or 0, "distance_km": event.distance_km,
            "setup_minutes": event.setup_minutes, "teardown_minutes": event.teardown_minutes,
            "discount_percent": calc.discount_percent, "custom_items": custom,
            "event_type": event.event_type}
    # The model's default NW is not a user-selected override of the profile.
    if event.pricing_region and event.pricing_region != Event._meta.get_field("pricing_region").default:
        data["pricing_region"] = event.pricing_region
    return calculate_payload(data, public=False, actor=actor)


def save_revision(event, result, *, kind, actor=None, legacy_total=None, update_event_total=False):
    """Append a frozen calculation; only the V2-mode caller opts into total writes."""
    if kind not in PriceCalculationRevision.Kind.values:
        raise PricingValidationError("invalid_input", "invalid revision kind", "kind")
    serialized = result.to_dict()
    inputs = serialized["inputs"]
    try:
        comparison = None if legacy_total is None else Decimal(str(legacy_total))
        if comparison is not None and not comparison.is_finite():
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingValidationError("invalid_input", "invalid legacy total", "legacy_total") from exc
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        previous = PriceCalculationRevision.objects.filter(event=locked).order_by("-revision_number").first()
        existing = PriceCalculationRevision.objects.filter(
            event=locked, kind=kind, status=PriceCalculationRevision.Status.DRAFT,
            result_json=serialized, created_by=actor, legacy_total=comparison).first()
        if existing is not None:
            return existing
        revision = PriceCalculationRevision.objects.create(
            event=locked, kind=kind, status=PriceCalculationRevision.Status.DRAFT,
            revision_number=1 if previous is None else previous.revision_number + 1,
            previous_revision=previous, engine_version=str(result.engine_version),
            profile_id_snapshot=inputs.get("profile_id"), profile_version=result.profile_version,
            package_id_snapshot=int(inputs["package_snapshot_id"]), package_version=result.package_version,
            workflow_id_snapshot=inputs.get("workflow_id"), workflow_version=result.workflow_version,
            input_json=inputs, result_json=serialized, gross_total=result.gross_total,
            legacy_total=comparison,
            difference=None if comparison is None else result.gross_total - comparison,
            created_by=actor)
        if update_event_total and kind in (PriceCalculationRevision.Kind.ESTIMATE, PriceCalculationRevision.Kind.DRAFT):
            released = PriceCalculationRevision.objects.filter(
                event=locked, status__in=(PriceCalculationRevision.Status.RELEASED,
                                         PriceCalculationRevision.Status.SENT)).exists()
            if not released:
                Event.objects.filter(pk=locked.pk).update(total_price=result.gross_total)
                event.total_price = result.gross_total
        return revision
