"""Safe, read-mostly V1/V2 pricing comparisons for rollout migrations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
from unittest.mock import patch

from django.db import transaction
from django.utils import timezone

from wishlist.models import (
    Event, EventStatus, PriceCalculationRevision, PriceItem, PricingFormula, PricingPackage,
    PricingPackageOverride, PricingProfile, PricingRule, PricingWorkflow,
)
from wishlist.price_engine import PriceEngine

from .input import PricingValidationError
from .result import PricingResult
from .services import calculate_event, calculate_payload, save_revision


FROZEN_REQUESTED_AT = datetime(2000, 1, 1, tzinfo=dt_timezone.utc)


def eligible_for_reprice(event, today):
    """Return whether an event may receive a controlled V2 draft repricing."""
    if event.status in (EventStatus.PAST, EventStatus.CANCELLED, "past", "cancelled"):
        return False
    if event.status in (EventStatus.INQUIRY, "inquiry"):
        return True
    if event.status in (EventStatus.CONFIRMED, "confirmed"):
        return event.date >= today
    return False


def reprice_event(event, *, today=None, actor=None, apply=False):
    """Calculate and optionally append one immutable, unreleased V2 draft."""
    today = timezone.localdate() if today is None else today
    if not eligible_for_reprice(event, today):
        return {
            "event_id": event.pk, "status": "skipped", "result": "ineligible",
            "errors": [], "old_gross": _money(event.total_price),
            "new_gross": None, "difference": None,
        }

    old = event.total_price
    try:
        result = calculate_event(event, actor=actor)
        new = result.gross_total
        revision = None
        if apply:
            revision = save_revision(event, result, kind=PriceCalculationRevision.Kind.DRAFT,
                                     actor=actor, legacy_total=old)
        inputs = result.inputs
        return {
            "event_id": event.pk, "status": "applied" if apply else "proposed",
            "result": "ok", "old_gross": _money(old), "new_gross": _money(new),
            "difference": _money(new - old) if old is not None else None,
            "source": {
                "profile_id": inputs.get("profile_id"),
                "profile_version": result.profile_version,
                "workflow_id": inputs.get("workflow_id"),
                "workflow_version": result.workflow_version,
                "engine_version": str(result.engine_version),
            },
            "revision_id": revision.pk if revision else None, "errors": [],
        }
    except Exception as exc:
        return {
            "event_id": event.pk, "status": "error", "result": "error",
            "old_gross": _money(old), "new_gross": None, "difference": None,
            "errors": [{"code": exc.code if isinstance(exc, PricingValidationError) else "internal_error"}],
        }


def _money(value):
    return None if value is None else f"{Decimal(str(value)):.2f}"


@dataclass(frozen=True)
class Comparison:
    event_id: int
    v1_gross: Decimal | None
    v2_gross: Decimal | None
    difference: Decimal | None
    profile_id: int | None
    profile_version: int | None
    workflow_id: int | None
    workflow_version: int | None
    status: str
    diagnostics: dict[str, Any]
    comparison_key: str
    revision: PriceCalculationRevision | None = None

    def to_dict(self) -> dict[str, Any]:
        def money(value):
            return None if value is None else f"{value:.2f}"

        return {
            "event_id": self.event_id,
            "v1_gross": money(self.v1_gross),
            "v2_gross": money(self.v2_gross),
            "difference": money(self.difference),
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "status": self.status,
            "diagnostics": self.diagnostics,
            "comparison_key": self.comparison_key,
        }


def _payload(event, workflow):
    calc = event.price_calculation
    payload = {
        "event_date": event.date,
        "start_time": event.time_start,
        "end_time": event.time_end,
        "package_id": str(calc.package_id) if calc.package_id else None,
        "selected_item_ids": [str(pk) for pk in calc.selected_items.values_list("pk", flat=True)],
        "guest_count": event.guest_count or 0,
        "distance_km": event.distance_km,
        "setup_minutes": event.setup_minutes,
        "teardown_minutes": event.teardown_minutes,
        "discount_percent": calc.discount_percent,
        "custom_items": calc.custom_items_json or [],
        "event_type": event.event_type,
        "workflow_id": str(workflow.pk),
    }
    if event.pricing_region and event.pricing_region != Event._meta.get_field("pricing_region").default:
        payload["pricing_region"] = event.pricing_region
    return payload


def _key(event, v1_input, v2_input, profile, workflow):
    frozen_inputs = v2_input.get("inputs", {})
    frozen_config = frozen_inputs.get("resolved_config", {})
    frozen_catalog = frozen_inputs.get("catalog_snapshot", {})
    frozen = {"event_id": event.pk, "v1": v1_input, "v2": v2_input,
              "profile": [profile.pk, profile.version], "workflow": [workflow.pk, workflow.version],
              "frozen_config_hash": _content_hash(frozen_config),
              "frozen_catalog_hash": _content_hash(frozen_catalog)}
    encoded = json.dumps(frozen, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _content_hash(value):
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _frozen_context(event, profile, workflow, payload):
    calc = event.price_calculation
    package = PricingPackage.objects.filter(pk=calc.package_id).first() if calc.package_id else None
    selected_item_ids = set(calc.selected_items.values_list("pk", flat=True))
    included_item_ids = set()
    if package:
        included_item_ids = set(package.included_items.values_list("pk", flat=True))
    required_item_ids = set(PriceItem.objects.filter(
        is_active=True, is_required=True).values_list("pk", flat=True))
    item_ids = selected_item_ids | included_item_ids | required_item_ids
    items = list(PriceItem.objects.filter(pk__in=item_ids).order_by("pk").values(
        "id", "name", "category", "description", "price", "is_default", "is_required",
        "sort_order", "is_active", "is_public"))
    rules = list(PricingRule.objects.filter(is_active=True).order_by("pk").values(
        "id", "name", "description", "condition_json", "effect_type", "effect_value",
        "applies_to", "sort_order", "is_active"))
    formulas = list(PricingFormula.objects.filter(is_active=True).order_by("pk").values(
        "id", "name", "expression", "mode", "description", "is_active"))
    package_snapshot = None if package is None else {
        "id": package.pk, "version": package.version, "name": package.name,
        "description": package.description, "base_price": package.base_price,
        "badge_label": package.badge_label, "badge_color": package.badge_color,
        "is_active": package.is_active, "is_public": package.is_public,
        "included_item_ids": sorted(included_item_ids),
        "selected_item_ids": sorted(selected_item_ids),
        "required_item_ids": sorted(required_item_ids),
    }
    override = PricingPackageOverride.objects.filter(
        profile=profile, package=package).values_list("overrides_json", flat=True).first() if package else None
    context = {
        "event": {"event_id": event.pk, "payload": payload},
        "profile_config": profile.config_json,
        "workflow": workflow.workflow_json,
        "package": package_snapshot,
        "package_override": override,
        "items": items, "rules": rules, "formulas": formulas,
    }
    return json.loads(json.dumps(context, default=str, sort_keys=True))


def compare_event(event, profile: PricingProfile, workflow: PricingWorkflow, *, actor=None,
                  persist=False) -> Comparison:
    """Compare one saved event without mutating it or its released offer.

    ``persist`` is intentionally opt-in; callers such as the management command use
    it only for an explicitly requested ephemeral-DB apply run.
    """
    event = Event.objects.select_related("price_calculation").get(pk=event.pk)
    v1 = None
    v2 = None
    diagnostics: dict[str, Any] = {}
    payload = None
    context = None
    key = None
    try:
        current_profile = PricingProfile.objects.filter(
            pk=profile.pk, status=PricingProfile.Status.ACTIVE).first()
        current_workflow = PricingWorkflow.objects.filter(
            pk=workflow.pk, is_active=True, is_default=True).first()
        if (current_profile is None or current_profile.version != profile.version or
                current_workflow is None or current_workflow.version != workflow.version):
            raise PricingValidationError("stale_reference", "supplied pricing references are not current", "pricing_reference")
        calc = event.price_calculation
        payload = _payload(event, workflow)
        context = _frozen_context(event, profile, workflow, payload)
        key_payload = {name: value for name, value in payload.items() if name != "requested_at"}
        key = _key(event, {"workflow": workflow.pk, "calc": key_payload},
                   {"inputs": context}, profile, workflow)
        v1_result = PriceEngine.calculate_workflow(
            event, workflow_id=workflow.pk,
            package_id=calc.package_id,
            selected_item_ids=list(calc.selected_items.values_list("pk", flat=True)),
            formula_id=calc.formula_id, custom_items=calc.custom_items_json,
            discount_percent=calc.discount_percent,
        )
        v1 = Decimal(str(v1_result["grand_total"]))
        # Requested-at is fixed in the payload contract so retries cannot drift.
        payload["requested_at"] = FROZEN_REQUESTED_AT
        # The engine normally stamps request time internally. Freeze that boundary
        # as well as the payload field so calendar/lead-time pricing is retry-stable.
        with patch("wishlist.pricing_v2.services.timezone.now", return_value=FROZEN_REQUESTED_AT):
            v2_result: PricingResult = calculate_payload(payload, public=False, actor=actor)
        v2 = v2_result.gross_total
        v2_dict = v2_result.to_dict()
        v2_dict["inputs"].pop("requested_at", None)
        key = _key(event, {"workflow": workflow.pk, "calc": key_payload}, v2_dict, profile, workflow)
        status = "ok"
    except Exception as exc:
        if isinstance(exc, PricingValidationError):
            diagnostics = {"code": exc.code, "field": exc.field}
        else:
            diagnostics = {"code": "internal_error"}
        if key is None:
            payload = payload or _payload(event, workflow)
            context = context or _frozen_context(event, profile, workflow, payload)
            key_payload = {name: value for name, value in payload.items() if name != "requested_at"}
            key = _key(event, {"workflow": workflow.pk, "calc": key_payload},
                       {"inputs": context}, profile, workflow)
        status = "error"

    difference = None if v1 is None or v2 is None else v2 - v1
    revision = None
    if persist:
        with transaction.atomic():
            locked = Event.objects.select_for_update().get(pk=event.pk)
            existing = PriceCalculationRevision.objects.filter(
                event=locked, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                comparison_key=key,
            ).first()
            if existing:
                revision = existing
            elif status == "ok":
                serialized = v2_result.to_dict()
                inputs = {**serialized["inputs"]}
                inputs.pop("requested_at", None)
                inputs["comparison_key"] = key
                serialized["inputs"] = inputs
                previous = PriceCalculationRevision.objects.filter(event=locked).order_by("-revision_number").first()
                revision = PriceCalculationRevision.objects.create(
                    event=locked, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                    status=PriceCalculationRevision.Status.DRAFT,
                    revision_number=previous.revision_number + 1 if previous else 1,
                    previous_revision=previous, engine_version=str(v2_result.engine_version),
                    profile_id_snapshot=inputs.get("profile_id"), profile_version=v2_result.profile_version,
                    package_id_snapshot=None if inputs["package_snapshot_id"] == "individual-items" else int(inputs["package_snapshot_id"]),
                    package_version=None if inputs["package_snapshot_id"] == "individual-items" else v2_result.package_version,
                    workflow_id_snapshot=inputs.get("workflow_id"), workflow_version=v2_result.workflow_version,
                    comparison_key=key, input_json=inputs, result_json=serialized,
                    gross_total=v2_result.gross_total, legacy_total=v1,
                    difference=v2_result.gross_total - v1, created_by=actor,
                )
            else:
                package = event.price_calculation.package
                previous = PriceCalculationRevision.objects.filter(event=locked).order_by("-revision_number").first()
                revision = PriceCalculationRevision.objects.create(
                    event=locked, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                    status=PriceCalculationRevision.Status.DRAFT,
                    revision_number=previous.revision_number + 1 if previous else 1,
                    previous_revision=previous,
                    engine_version="2", comparison_key=key,
                    profile_id_snapshot=profile.pk, profile_version=profile.version,
                    package_id_snapshot=package.pk if package else None,
                    package_version=package.version if package else None,
                    workflow_id_snapshot=workflow.pk, workflow_version=workflow.version,
                    input_json={**(context or {}), "comparison_key": key, "source": "migration"},
                    result_json={"diagnostic": diagnostics}, gross_total=v1 or Decimal("0"),
                    legacy_total=v1, difference=None, created_by=actor,
                )
    return Comparison(event.pk, v1, v2, difference, profile.pk, profile.version,
                      workflow.pk, workflow.version, status, diagnostics, key, revision)
