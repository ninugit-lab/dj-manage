"""Safe, read-mostly V1/V2 pricing comparisons for rollout migrations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
from unittest.mock import patch

from django.db import transaction

from wishlist.models import Event, PriceCalculationRevision, PricingProfile, PricingWorkflow
from wishlist.price_engine import PriceEngine

from .input import PricingValidationError
from .result import PricingResult
from .services import calculate_payload, save_revision


FROZEN_REQUESTED_AT = datetime(2000, 1, 1, tzinfo=timezone.utc)


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
    frozen = {"event_id": event.pk, "v1": v1_input, "v2": v2_input,
              "profile": [profile.pk, profile.version], "workflow": [workflow.pk, workflow.version]}
    encoded = json.dumps(frozen, sort_keys=True, default=str, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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
    try:
        calc = event.price_calculation
        v1_result = PriceEngine.calculate_workflow(
            event, workflow_id=workflow.pk,
            package_id=calc.package_id,
            selected_item_ids=list(calc.selected_items.values_list("pk", flat=True)),
            formula_id=calc.formula_id, custom_items=calc.custom_items_json,
            discount_percent=calc.discount_percent,
        )
        v1 = Decimal(str(v1_result["grand_total"]))
        payload = _payload(event, workflow)
        # Requested-at is fixed in the payload contract so retries cannot drift.
        payload["requested_at"] = FROZEN_REQUESTED_AT
        # The engine normally stamps request time internally. Freeze that boundary
        # as well as the payload field so calendar/lead-time pricing is retry-stable.
        with patch("wishlist.pricing_v2.services.timezone.now", return_value=FROZEN_REQUESTED_AT):
            v2_result: PricingResult = calculate_payload(payload, public=False, actor=actor)
        v2 = v2_result.gross_total
        v2_dict = v2_result.to_dict()
        v2_dict["inputs"].pop("requested_at", None)
        key = _key(event, {"workflow": workflow.pk, "calc": payload}, v2_dict, profile, workflow)
        status = "ok"
    except Exception as exc:
        if isinstance(exc, PricingValidationError):
            diagnostics = {"code": exc.code, "field": exc.field, "message": exc.message}
        else:
            diagnostics = {"code": "internal_error", "message": str(exc)}
        key = hashlib.sha256(f"{event.pk}:{profile.pk}:{profile.version}:{workflow.pk}:{workflow.version}".encode()).hexdigest()
        status = "error"

    difference = None if v1 is None or v2 is None else v2 - v1
    revision = None
    if persist:
        with transaction.atomic():
            existing = PriceCalculationRevision.objects.filter(
                event=event, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                input_json__comparison_key=key,
            ).first()
            if existing:
                revision = existing
            elif status == "ok":
                revision = save_revision(event, v2_result, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                                         actor=actor, legacy_total=v1)
                revision.input_json = {**revision.input_json, "comparison_key": key, "source": "migration"}
                revision.save(update_fields=["input_json"])
            else:
                revision = PriceCalculationRevision.objects.create(
                    event=event, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
                    status=PriceCalculationRevision.Status.DRAFT,
                    revision_number=(PriceCalculationRevision.objects.filter(event=event).order_by("-revision_number").first().revision_number + 1
                                     if PriceCalculationRevision.objects.filter(event=event).exists() else 1),
                    engine_version="2", input_json={"comparison_key": key, "source": "migration"},
                    result_json={"diagnostic": diagnostics}, gross_total=v1 or Decimal("0"),
                    legacy_total=v1, difference=None, created_by=actor,
                )
    return Comparison(event.pk, v1, v2, difference, profile.pk, profile.version,
                      workflow.pk, workflow.version, status, diagnostics, key, revision)
