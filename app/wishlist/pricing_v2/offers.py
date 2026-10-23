from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from numbers import Real
from copy import deepcopy
from dataclasses import dataclass
from string import Formatter
from django.utils import timezone

from django.db import IntegrityError, transaction

from wishlist.models import AppConfig, EmailLog, Event, ManualPriceAdjustment, PriceCalculationRevision
from .input import PricingValidationError
from .result import PricingLine
from .services import calculate_with_mode, save_revision, update_released_event_total

_MAX_GROSS = Decimal("9999999999.99")
_OFFER_FIELDS = frozenset(("client_name", "event_name", "event_date", "price_breakdown", "gross_total", "dj_name"))


@dataclass(frozen=True)
class SendResult:
    sent: bool
    revision: PriceCalculationRevision
    error: str = ""
    email_log: EmailLog | None = None


class OfferTemplateError(ValueError):
    """A configured offer template cannot be safely rendered."""


def _render_offer_template(template, context):
    try:
        fields = []
        for _, field, format_spec, conversion in Formatter().parse(template):
            if field is not None:
                if conversion or format_spec or field not in _OFFER_FIELDS:
                    raise OfferTemplateError("Ungültiger Angebots-Platzhalter")
                fields.append(field)
        return template.format_map(context)
    except (ValueError, KeyError, IndexError, AttributeError) as exc:
        raise OfferTemplateError("Ungültiger Angebots-Platzhalter") from exc


def _offer_context(revision, config):
    event = revision.event
    lines = revision.result_json.get("lines", []) if isinstance(revision.result_json, dict) else []
    breakdown = []
    for line in lines:
        label = str(line.get("label", ""))
        amount = line.get("amount", "0")
        breakdown.append(f"{label}: {amount} €")
    return {
        "client_name": event.client_name or "Kunde",
        "event_name": event.name,
        "event_date": event.date.strftime("%d.%m.%Y"),
        "price_breakdown": "\n".join(breakdown),
        "gross_total": str(revision.gross_total),
        "dj_name": config.dj_name,
    }


def send_offer(revision, *, retry=False):
    """Send exactly the frozen released offer; retries never create a revision."""
    if revision.kind != PriceCalculationRevision.Kind.OFFER or revision.status not in (
        PriceCalculationRevision.Status.RELEASED, PriceCalculationRevision.Status.SENT,
        PriceCalculationRevision.Status.SEND_FAILED):
        raise ValueError("Angebot muss zuerst freigegeben werden")
    if revision.status == PriceCalculationRevision.Status.SENT and not retry:
        raise ValueError("Erneuter Versand erfordert retry=True")
    event = revision.event
    if not event.client_email:
        raise ValueError("Kunden-E-Mail fehlt")
    config = AppConfig.load()
    context = _offer_context(revision, config)
    subject = _render_offer_template(config.offer_email_subject, context)
    body = _render_offer_template(config.offer_email_body, context)
    error = ""
    success = False
    try:
        from wishlist.google_services import GoogleService
        response = GoogleService.send_email(event.client_email, subject, body, sender_name=config.dj_name)
        success, message = response if isinstance(response, tuple) else (bool(response), "Gesendet" if response else "Versand fehlgeschlagen")
        if not success:
            error = str(message or "Versand fehlgeschlagen")
    except Exception as exc:  # Gmail/network errors are delivery failures, not 500s.
        error = str(exc).replace("\n", " ")[:500]
    log = EmailLog.objects.create(event=event, revision=revision, recipient=event.client_email,
                                  subject=subject, body=body, success=success, error_message=error)
    if success:
        revision.status = PriceCalculationRevision.Status.SENT
        revision.sent_at = timezone.now()
        revision.save(update_fields=["status", "sent_at"])
    else:
        revision.status = PriceCalculationRevision.Status.SEND_FAILED
        revision.save(update_fields=["status"])
    return SendResult(success, revision, error, log)


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


def _gross_total(value):
    try:
        total = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PricingValidationError("invalid_input", "gross total must be a finite cent amount", "gross_total") from exc
    if not total.is_finite() or total < 0 or total > _MAX_GROSS:
        raise PricingValidationError("invalid_input", "gross total is outside the Decimal(12,2) range", "gross_total")
    return total


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
    return replace(result, inputs=inputs, lines=tuple(lines), gross_total=_gross_total(total))


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
        # Only a genuinely new draft reaches the V2 gate/calculation. A retry
        # of a persisted offer must remain available even if current settings
        # or live event inputs have since become invalid.
        _v2_result(locked, actor)
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
