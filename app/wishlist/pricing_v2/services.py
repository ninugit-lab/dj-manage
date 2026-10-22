"""ORM boundary for V2 pricing; the engine only sees frozen scalar catalogs."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from copy import copy, deepcopy
from datetime import date, time
from decimal import Decimal, InvalidOperation
import logging
from types import SimpleNamespace

from django.db import transaction
from django.utils import timezone

from wishlist.models import (AppConfig, Event, EventPriceCalculation, PriceCalculationRevision, PriceItem,
                              PricingFormula, PricingPackage, PricingPackageOverride,
                              PricingProfile, PricingRule, PricingWorkflow)
from wishlist.price_engine import PriceEngine

from .config import resolve_pricing_config
from .engine import PricingEngineV2
from .input import PricingInput, PricingValidationError
from .result import PricingLine, PricingResult
from .workflow import DEFAULT_BLOCKS, ValidatedWorkflow, validate_workflow


logger = logging.getLogger(__name__)
_PUBLIC_INDIVIDUAL_PACKAGE = "individual-items"


@dataclass(frozen=True)
class PricingDecision:
    visible_result: PricingResult
    v2_result: PricingResult | None = None
    comparison_revision: PriceCalculationRevision | None = None


def _legacy_result(raw):
    """Keep V1's arithmetic untouched; adapt its gross amounts for the common response."""
    lines = []
    for category, entries in (("package", [raw["package"]] if raw.get("package") else []),
                              ("item", raw.get("items", [])), ("custom", raw.get("custom_items", []))):
        for index, entry in enumerate(entries):
            amount = Decimal(str(entry["price"]))
            lines.append(PricingLine(f"{category}:{index}", entry["name"], "V1 gross", 1,
                                     "EUR", amount, amount, "legacy"))
    for index, rule in enumerate(raw.get("rules_applied", [])):
        amount = Decimal(str(rule["amount"]))
        lines.append(PricingLine(f"rule:{index}", rule["name"], "V1 gross", 1, "EUR", amount, amount, "legacy"))
    if raw.get("offer_breakdown"):
        amount = Decimal(str(raw["offer_breakdown"]["grand_total"]))
        lines.append(PricingLine("offer", "Offer", "V1 gross", 1, "EUR", amount, amount, "legacy"))
    if raw.get("discount_amount"):
        amount = -Decimal(str(raw["discount_amount"]))
        lines.append(PricingLine("discount", "Discount", "V1 gross", 1, "EUR", amount, amount, "legacy"))
    return PricingResult(engine_version="1", gross_total=Decimal(str(raw["grand_total"])),
                         lines=tuple(lines), inputs={"source": "legacy"})


def _legacy_calculate(payload, event, *, public=False):
    if event is None:
        def parsed(value, kind):
            return kind.fromisoformat(value) if isinstance(value, str) else value
        event = Event(date=parsed(payload.get("event_date"), date),
                       time_start=parsed(payload.get("start_time"), time),
                       time_end=parsed(payload.get("end_time"), time), guest_count=payload.get("guest_count"),
                       distance_km=payload.get("distance_km"), event_type=payload.get("event_type", ""))
    elif payload is not None:
        # Preview an unsaved form against the saved event's offer/selections without
        # allowing its stale fields to override the current form economics.
        event = copy(event)
        for field, payload_key, kind in (("date", "event_date", date),
                                         ("time_start", "start_time", time),
                                         ("time_end", "end_time", time)):
            if payload_key in payload:
                value = payload[payload_key]
                setattr(event, field, kind.fromisoformat(value) if isinstance(value, str) else value)
        for field in ("guest_count", "distance_km", "event_type"):
            if field in payload:
                setattr(event, field, payload[field])
    if payload is None:
        calc = EventPriceCalculation.objects.filter(event=event).first()
        params = dict(package_id=calc.package_id if calc else None,
                      selected_item_ids=list(calc.selected_items.values_list("pk", flat=True)) if calc else None,
                      formula_id=calc.formula_id if calc else None,
                      custom_items=calc.custom_items_json if calc else None,
                      discount_percent=calc.discount_percent if calc else 0)
        workflow_id = calc.workflow_id if calc and calc.workflow_id else None
    else:
        params = dict(package_id=payload.get("package_id"),
                      selected_item_ids=payload.get("selected_item_ids", payload.get("selected_items")),
                      formula_id=payload.get("formula_id"), custom_items=payload.get("custom_items"),
                      discount_percent=payload.get("discount_percent", 0),
                      offer_data=payload.get("offer_data"), context_override=payload.get("context_override"))
        workflow_id = payload.get("workflow_id")
    if workflow_id is None:
        workflow_id = PricingWorkflow.objects.filter(is_default=True, is_active=True).values_list("pk", flat=True).first()
    # A public individual-items request must not inherit a workflow's configured
    # package: V1 uses that package as a fallback even when package_id is None.
    if public and payload is not None and payload.get("package_id") is None:
        workflow_id = None
    if workflow_id:
        raw = PriceEngine.calculate_workflow(event, workflow_id=workflow_id, **params)
    else:
        raw = PriceEngine.calculate(event, **params)
    return _legacy_result(raw)


def _save_failed_comparison(event, legacy, diagnostic, actor):
    """Persist a typed internal diagnostic without fabricating a V2 price or difference."""
    serialized = {"engine_version": "2", "diagnostic": diagnostic}
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        existing = PriceCalculationRevision.objects.filter(
            event=locked, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
            status=PriceCalculationRevision.Status.DRAFT, result_json=serialized,
            legacy_total=legacy.gross_total).first()
        if existing:
            return existing
        previous = PriceCalculationRevision.objects.filter(event=locked).order_by("-revision_number").first()
        return PriceCalculationRevision.objects.create(
            event=locked, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
            status=PriceCalculationRevision.Status.DRAFT,
            revision_number=previous.revision_number + 1 if previous else 1,
            previous_revision=previous, engine_version="2", input_json={"source": "shadow"},
            result_json=serialized, gross_total=legacy.gross_total,
            legacy_total=legacy.gross_total, created_by=actor)


def _comparison_identity(result):
    snapshot = deepcopy(result)
    snapshot.get("inputs", {}).pop("requested_at", None)
    if snapshot.get("inputs", {}).get("source") == "submission":
        snapshot["inputs"].pop("source")
    return snapshot


def _save_comparison(event, v2, legacy, actor):
    # The V2 service timestamps each request. Retries with identical economics
    # must not create new comparisons solely because the wall clock advanced.
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        identity = _comparison_identity(v2.to_dict())
        for previous in PriceCalculationRevision.objects.filter(
                event=locked, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                status=PriceCalculationRevision.Status.DRAFT,
                legacy_total=legacy.gross_total):
            if previous.input_json.get("source") == "submission":
                continue
            if _comparison_identity(previous.result_json) == identity:
                return previous
        return save_revision(event, v2, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                             actor=actor, legacy_total=legacy.gross_total)


def calculate_with_mode(payload=None, *, event=None, public=False, actor=None,
                        persist_comparison=False, comparison_scope=None):
    """Unified entry for Tasks 3–5: pass payload for previews/submits, or event for saved admin.

    The persisted AppConfig selects the engine. Only shadow with explicit opt-in and
    a saved Event writes a comparison; callers must handle their own V1 event writes.
    """
    mode = AppConfig.objects.filter(pk=1).values_list("pricing_engine_mode", flat=True).first() or "legacy"
    if mode not in AppConfig.PricingEngineMode.values:
        raise PricingValidationError("invalid_input", "unknown pricing engine mode", "pricing_engine_mode")
    if payload is None and (event is None or isinstance(event, Event) and event.pk is None):
        raise PricingValidationError("missing_input", "payload or event required", "payload")
    if payload is not None and not isinstance(payload, Mapping):
        raise PricingValidationError("invalid_input", "expected a mapping", "payload")
    if payload is not None and not payload:
        raise PricingValidationError("missing_input", "nonempty payload required", "payload")
    if event is not None and not isinstance(event, Event):
        raise PricingValidationError("invalid_input", "expected an event", "event")
    if comparison_scope not in (None, "submission"):
        raise PricingValidationError("invalid_input", "unknown comparison scope", "comparison_scope")
    if public and payload is not None:
        selected = payload.get("selected_item_ids", ())
        if not isinstance(selected, (list, tuple)) or any(
                isinstance(identity, bool) or not isinstance(identity, (str, int))
                or not str(identity).isdigit() or int(identity) < 1 for identity in selected):
            raise PricingValidationError("invalid_input", "invalid public item selection", "selected_item_ids")
        ids = {int(identity) for identity in selected}
        if ids != set(PriceItem.objects.filter(pk__in=ids, is_active=True, is_public=True)
                      .values_list("pk", flat=True)):
            _reference("item", "selected_item_ids")
        required = PriceItem.objects.filter(is_active=True, is_public=True, is_required=True).values_list("pk", flat=True)
        payload = {**payload, "selected_item_ids": [str(identity) for identity in dict.fromkeys(
            [*(int(identity) for identity in selected), *required])]}
        if payload.get("package_id") in (None, ""):
            payload["package_id"] = None
        elif not PricingPackage.objects.filter(pk=_identifier(payload["package_id"], "package_id"),
                                                is_active=True, is_public=True).exists():
            _reference("package", "package_id")
    if mode == "v2":
        result = calculate_payload(payload, public=public, actor=actor) if payload is not None else calculate_event(event, actor=actor)
        return PricingDecision(result, result)
    legacy = _legacy_calculate(payload, event, public=public)
    if mode == "legacy":
        return PricingDecision(legacy)
    try:
        v2 = calculate_payload(payload, public=public, actor=actor) if payload is not None else calculate_event(event, actor=actor)
    except Exception as exc:
        logger.exception("Shadow V2 calculation failed")
        diagnostic = {"code": exc.code if isinstance(exc, PricingValidationError) else "internal_error"}
        revision = None
        if persist_comparison and event is not None and event.pk:
            try:
                revision = (_save_submission_comparison(event, legacy, actor, diagnostic=diagnostic)
                            if comparison_scope == "submission" else _save_failed_comparison(event, legacy, diagnostic, actor))
            except Exception:
                logger.exception("Shadow diagnostic comparison could not be persisted")
        return PricingDecision(legacy, comparison_revision=revision)
    revision = None
    if persist_comparison and event is not None and event.pk:
        try:
            revision = (_save_submission_comparison(event, legacy, actor, v2=v2)
                        if comparison_scope == "submission" else _save_comparison(event, v2, legacy, actor))
        except Exception:
            logger.exception("Shadow comparison could not be persisted")
    return PricingDecision(legacy, v2, revision)


def _save_submission_comparison(event, legacy, actor, *, v2=None, diagnostic=None):
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        prior = PriceCalculationRevision.objects.filter(
            event=locked, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
            status=PriceCalculationRevision.Status.DRAFT, input_json__source="submission").first()
        if prior:
            return prior
        if v2 is not None:
            revision = save_revision(event, v2, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                                     actor=actor, legacy_total=legacy.gross_total)
            revision.input_json = {**revision.input_json, "source": "submission"}
            revision.result_json = {**revision.result_json, "inputs": revision.input_json}
            revision.save(update_fields=["input_json", "result_json"])
            return revision
        previous = PriceCalculationRevision.objects.filter(event=locked).order_by("-revision_number").first()
        return PriceCalculationRevision.objects.create(
            event=locked, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
            status=PriceCalculationRevision.Status.DRAFT,
            revision_number=previous.revision_number + 1 if previous else 1,
            previous_revision=previous, engine_version="2", input_json={"source": "submission"},
            result_json={"engine_version": "2", "diagnostic": diagnostic}, gross_total=legacy.gross_total,
            legacy_total=legacy.gross_total, created_by=actor)


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
    # Admin drafts can intentionally select individual items without a package,
    # using the same synthetic zero-base catalog entry as public estimates.
    individual = payload.get("package_id") in (None, "")
    package_id = _PUBLIC_INDIVIDUAL_PACKAGE if individual else _identifier(payload.get("package_id"), "package_id")
    request = PricingInput.from_mapping({**payload, "package_id": package_id}, requested_at=timezone.now())
    profiles = list(PricingProfile.objects.filter(status=PricingProfile.Status.ACTIVE)[:2])
    if len(profiles) != 1:
        _reference("active profile", "profile")
    profile = profiles[0]
    package = None if individual else PricingPackage.objects.filter(
        pk=package_id, is_active=True, **({"is_public": True} if public else {})).first()
    if package is None and not individual:
        _reference("package", "package_id")
    workflows = list(PricingWorkflow.objects.filter(is_active=True, is_default=True)[:2])
    if len(workflows) != 1:
        _reference("default workflow", "workflow")
    workflow = workflows[0]
    override = PricingPackageOverride.objects.filter(profile=profile, package=package).first() if package else None
    resolved = resolve_pricing_config(profile, None if individual else SimpleNamespace(
        id=package.pk, version=package.version,
        overrides_json=override.overrides_json if override else {}))
    selected = tuple(_identifier(identity, "selected_item_ids") for identity in request.selected_item_ids)
    included = () if individual else tuple(str(item.pk) for item in package.included_items.all())
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
        "label": "Einzelne Posten" if individual else package.name,
        "base_price": "0.00" if individual else str(package.base_price),
        "version": "individual" if individual else package.version,
        "description": "" if individual else package.description, "included_item_ids": included,
        "required_item_ids": required, "is_active": True if individual else package.is_active,
        "is_public": True if individual else package.is_public}},
        "items": items, "rules": rules, "formulas": formulas}
    # The default workflow still selects all price-building and adjustment blocks,
    # but a package-specific reference cannot select a package the user declined.
    workflow_blocks = workflow.workflow_json
    if individual:
        workflow_blocks = [dict(block, config={key: value for key, value in block.get("config", {}).items()
                                               if key != "package_id"}) if block.get("type") == "package" else block
                           for block in workflow.workflow_json]
    blocks = _legacy_blocks(workflow_blocks, package_id, catalog)
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
    custom = []
    for index, item in enumerate(calc.custom_items_json or []):
        if not isinstance(item, Mapping):
            raise PricingValidationError("invalid_input", "invalid custom item", "custom_items")
        custom.append({"code": item.get("code", f"event-{index + 1}"),
                       "label": item.get("label", item.get("name")),
                       "amount": item.get("amount", item.get("price"))})
    data = {"event_date": event.date, "start_time": event.time_start, "end_time": event.time_end,
             "package_id": str(calc.package_id) if calc.package_id else None,
             "selected_item_ids": [str(item.pk) for item in calc.selected_items.all()],
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
        for existing in PriceCalculationRevision.objects.filter(
                event=locked, kind=kind, status=PriceCalculationRevision.Status.DRAFT,
                created_by=actor, legacy_total=comparison):
            if (kind == PriceCalculationRevision.Kind.MIGRATION_COMPARE and
                    existing.input_json.get("source") == "submission"):
                continue
            if (kind in (PriceCalculationRevision.Kind.ESTIMATE, PriceCalculationRevision.Kind.DRAFT) and
                     _comparison_identity(existing.result_json) == _comparison_identity(serialized)
                     or kind not in (PriceCalculationRevision.Kind.ESTIMATE, PriceCalculationRevision.Kind.DRAFT)
                     and existing.result_json == serialized):
                if update_event_total and kind in (PriceCalculationRevision.Kind.ESTIMATE,
                                                   PriceCalculationRevision.Kind.DRAFT):
                    released = PriceCalculationRevision.objects.filter(
                        event=locked, status__in=(PriceCalculationRevision.Status.RELEASED,
                                                 PriceCalculationRevision.Status.SENT)).exists()
                    if not released:
                        Event.objects.filter(pk=locked.pk).update(total_price=existing.gross_total)
                        event.total_price = existing.gross_total
                return existing
        revision = PriceCalculationRevision.objects.create(
            event=locked, kind=kind, status=PriceCalculationRevision.Status.DRAFT,
            revision_number=1 if previous is None else previous.revision_number + 1,
            previous_revision=previous, engine_version=str(result.engine_version),
            profile_id_snapshot=inputs.get("profile_id"), profile_version=result.profile_version,
            package_id_snapshot=(None if inputs["package_snapshot_id"] == _PUBLIC_INDIVIDUAL_PACKAGE
                                 else int(inputs["package_snapshot_id"])),
            package_version=(None if inputs["package_snapshot_id"] == _PUBLIC_INDIVIDUAL_PACKAGE
                             else result.package_version),
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
