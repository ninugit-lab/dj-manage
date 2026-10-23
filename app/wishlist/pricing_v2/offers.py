from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from numbers import Real
from copy import deepcopy

from django.db import IntegrityError, transaction

from wishlist.models import Event, ManualPriceAdjustment, PriceCalculationRevision
from .input import PricingValidationError
from .result import PricingLine
from .services import calculate_with_mode, save_revision, update_released_event_total


def _money(value, field):
    if isinstance(value, (bool, float)):
        raise PricingValidationError("invalid_input", "amount must be an exact Decimal with at most two decimals", field)
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingValidationError("invalid_input", "invalid amount", field) from exc
    if not amount.is_finite() or amount == 0 or amount.as_tuple().exponent < -2 or abs(amount) >= Decimal("10000000000"):
        raise PricingValidationError("invalid_input", "amount must be finite and nonzero", field)
    return amount.quantize(Decimal("0.01"))


def _v2_result(event, actor):
    from wishlist.models import AppConfig
    if AppConfig.objects.filter(pk=1).values_list('pricing_engine_mode', flat=True).first() != 'v2':
        raise PricingValidationError('invalid_input', 'offers require V2 pricing mode', 'pricing_engine_mode')
    result = calculate_with_mode(event=event, actor=actor).visible_result
    if result is None or any(line.source == 'legacy' for line in result.lines):
        raise PricingValidationError('invalid_input', 'offers require a V2 pricing result', 'pricing_engine_mode')
    return result


def add_adjustment(event, label, amount, reason, actor):
    label, reason = str(label or "").strip(), str(reason or "").strip()
    if not label:
        raise PricingValidationError("invalid_input", "label is required", "label")
    if not reason:
        raise PricingValidationError("invalid_input", "reason is required", "reason")
    amount = _money(amount, "amount")
    base = _v2_result(event, actor)
    total = base.gross_total + sum((a.amount for a in event.price_adjustments.all()), Decimal("0")) + amount
    if total < 0:
        raise PricingValidationError("invalid_input", "adjustments cannot produce a negative gross total", "amount")
    return ManualPriceAdjustment.objects.create(event=event, label=label, amount=amount,
                                                reason=reason, created_by=actor)


def _result_with_adjustments(event, actor):
    result = _v2_result(event, actor)
    adjustments = list(event.price_adjustments.order_by("pk"))
    lines = list(result.lines)
    inputs = dict(result.inputs or {})
    snapshot = []
    total = result.gross_total
    for adjustment in adjustments:
        total += adjustment.amount
        lines.append(PricingLine(code=f"admin-adjustment:{adjustment.pk}", label=adjustment.label,
                                 basis=adjustment.reason, quantity=1, unit="flat",
                                 unit_price=adjustment.amount, amount=adjustment.amount, source="admin"))
        snapshot.append({"id": adjustment.pk, "label": adjustment.label,
                         "amount": str(adjustment.amount), "reason": adjustment.reason})
    inputs["admin_adjustments"] = snapshot
    return replace(result, inputs=inputs, lines=tuple(lines), gross_total=total)


def build_draft(event, actor):
    result = _result_with_adjustments(event, actor)
    revision = save_revision(event, result, kind=PriceCalculationRevision.Kind.DRAFT, actor=actor)
    if revision.result_json.get("admin_adjustments") != revision.input_json.get("admin_adjustments"):
        result_json = dict(revision.result_json)
        result_json["admin_adjustments"] = revision.input_json.get("admin_adjustments", [])
        PriceCalculationRevision.objects.filter(pk=revision.pk).update(result_json=result_json)
        revision.result_json = result_json
    return revision


def release_offer(event, actor):
    _v2_result(event, actor)
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        current = PriceCalculationRevision.objects.filter(event=locked, kind=PriceCalculationRevision.Kind.DRAFT,
                                                           status=PriceCalculationRevision.Status.DRAFT).order_by("-revision_number").first()
        latest_offer = PriceCalculationRevision.objects.filter(
            event=locked,
            kind=PriceCalculationRevision.Kind.OFFER,
            status__in=(PriceCalculationRevision.Status.RELEASED,
                        PriceCalculationRevision.Status.SENT,
                        PriceCalculationRevision.Status.SEND_FAILED),
        ).order_by("-offer_number").first()
        # save_revision may leave an identical draft created by a harmless
        # post-release recalculation. It is older than the consumed offer,
        # so it cannot become a second offer on a retry.
        def identity(revision):
            data = {'input': deepcopy(revision.input_json), 'result': deepcopy(revision.result_json)}
            data['input'].pop('requested_at', None)
            data['result'].get('inputs', {}).pop('requested_at', None)
            return data

        if current and latest_offer and (current.revision_number <= latest_offer.revision_number
                                         or identity(current) == identity(latest_offer)):
            return latest_offer
        if current is None:
            # Repeating the same POST after the draft was consumed is a safe
            # retry.  Do not manufacture a new offer, and do not use this
            # fallback when a newer draft exists (the branch above handles
            # that draft and compares its frozen snapshot).
            if latest_offer:
                return latest_offer
            raise PricingValidationError("invalid_input", "a current draft is required", "draft")
        released = PriceCalculationRevision.objects.filter(event=locked, status=PriceCalculationRevision.Status.RELEASED,
                                                           input_json=current.input_json, result_json=current.result_json).first()
        if released:
            return released
        offer_number = (PriceCalculationRevision.objects.filter(event=locked, offer_number__isnull=False)
                         .order_by("-offer_number").values_list("offer_number", flat=True).first() or 0) + 1
        try:
            current.status = PriceCalculationRevision.Status.RELEASED
            current.kind = PriceCalculationRevision.Kind.OFFER
            current.offer_number = offer_number
            current.created_by = actor
            current.save(update_fields=["status", "kind", "offer_number", "created_by"])
            update_released_event_total(locked, current.gross_total)
        except IntegrityError:
            return PriceCalculationRevision.objects.get(event=locked, offer_number=offer_number)
        return current
