from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from numbers import Real
from copy import deepcopy
from dataclasses import dataclass
from string import Formatter
from datetime import timedelta
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


def _snapshot_offer_email(revision):
    config = AppConfig.load()
    context = _offer_context(revision, config)
    subject = _render_offer_template(config.offer_email_subject, context)
    body = _render_offer_template(config.offer_email_body, context)
    return context, subject, body


def send_offer(revision, *, retry=False):
    """Send exactly the frozen released offer; retries never create a revision."""
    caller_revision = revision
    with transaction.atomic():
        claimed = PriceCalculationRevision.objects.select_for_update().select_related("event").get(pk=revision.pk)
        if claimed.kind != PriceCalculationRevision.Kind.OFFER or claimed.status not in (
            PriceCalculationRevision.Status.RELEASED, PriceCalculationRevision.Status.SENT,
            PriceCalculationRevision.Status.SEND_FAILED, PriceCalculationRevision.Status.SENDING):
            raise ValueError("Angebot muss zuerst freigegeben werden")
        if claimed.status == PriceCalculationRevision.Status.SENDING:
            raise ValueError("Versand läuft; stale claims müssen explizit recovered werden")
        if claimed.status == PriceCalculationRevision.Status.SENT:
            raise ValueError("Angebot wurde bereits gesendet; erneuter Versand ist nicht zulässig")
        if not claimed.email_recipient:
            raise OfferTemplateError("Angebot erneut freigeben: keine eingefrorene Empfängeradresse")
        context = claimed.email_context
        subject = claimed.email_subject
        body = claimed.email_body
        if not subject or not body or not context:
            raise OfferTemplateError("Angebot erneut freigeben: historische Angebots-Mail ist nicht versandfähig")
        now = timezone.now()
        updated = PriceCalculationRevision.objects.filter(
            pk=claimed.pk,
            status__in=(PriceCalculationRevision.Status.RELEASED,
                        PriceCalculationRevision.Status.SENT,
                        PriceCalculationRevision.Status.SEND_FAILED),
        ).update(status=PriceCalculationRevision.Status.SENDING, send_claimed_at=now)
        if updated != 1:
            raise ValueError("Versand läuft; stale claims müssen explizit recovered werden")
        claimed.status = PriceCalculationRevision.Status.SENDING
        claimed.send_claimed_at = now
    revision = claimed
    event = claimed.event
    error = ""
    success = False
    try:
        from wishlist.google_services import GoogleService
        response = GoogleService.send_email(claimed.email_recipient, subject, body,
                                            sender_name=context.get("dj_name", "DJ"))
        success, message = response if isinstance(response, tuple) else (bool(response), "Gesendet" if response else "Versand fehlgeschlagen")
        if not success:
            error = "EMAIL_DELIVERY_FAILED"
    except Exception as exc:  # Gmail/network errors are delivery failures, not 500s.
        import logging
        logging.getLogger(__name__).warning("offer email delivery failed (details intentionally omitted)")
        error = "EMAIL_DELIVERY_FAILED"
    with transaction.atomic():
        current = PriceCalculationRevision.objects.select_for_update().get(pk=claimed.pk)
        log = EmailLog.objects.create(event=event, revision=current, recipient=current.email_recipient,
                                      subject=subject, body=body, success=success, error_message=error)
        if success:
            current.status = PriceCalculationRevision.Status.SENT
            current.sent_at = timezone.now()
        else:
            current.status = PriceCalculationRevision.Status.SEND_FAILED
        current.send_claimed_at = None
        current.save(update_fields=["status", "sent_at", "send_claimed_at"])
    # Keep a caller-held model instance coherent with the committed result;
    # admin callers commonly render the returned revision immediately.
    caller_revision.status = current.status
    caller_revision.sent_at = current.sent_at
    caller_revision.send_claimed_at = current.send_claimed_at
    return SendResult(success, current, error, log)


def recover_stale_send(revision, *, confirm=False, max_age=timedelta(minutes=15)):
    """Convert an old, manually confirmed claim into a retryable failed attempt."""
    if not confirm:
        raise ValueError("Stale-Versandclaim muss explizit bestätigt werden")
    with transaction.atomic():
        current = PriceCalculationRevision.objects.select_for_update().get(pk=revision.pk)
        if current.status != PriceCalculationRevision.Status.SENDING:
            return current
        if not current.send_claimed_at or timezone.now() - current.send_claimed_at < max_age:
            raise ValueError("Versandclaim ist noch nicht stale")
        current.status = PriceCalculationRevision.Status.SEND_FAILED
        current.send_claimed_at = None
        current.save(update_fields=["status", "send_claimed_at"])
        return current


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
    with transaction.atomic():
        locked = Event.objects.select_for_update().get(pk=event.pk)
        base = _v2_result(locked, actor)
        total = base.gross_total + sum((a.amount for a in ManualPriceAdjustment.objects.filter(event=locked)), Decimal("0")) + amount
        _gross_total(total)
        if total < 0:
            raise PricingValidationError("invalid_input", "adjustments cannot produce a negative gross total", "amount")
        return ManualPriceAdjustment.objects.create(event=locked, label=label, amount=amount,
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
        config = AppConfig.objects.select_for_update().get(pk=1)
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
            email_context = _offer_context(current, config)
            email_subject = _render_offer_template(config.offer_email_subject, email_context)
            email_body = _render_offer_template(config.offer_email_body, email_context)
            current.email_context = email_context
            current.email_subject = email_subject
            current.email_body = email_body
            current.email_recipient = locked.client_email
            current.status = PriceCalculationRevision.Status.RELEASED
            current.kind = PriceCalculationRevision.Kind.OFFER
            current.offer_number = offer_number
            current.created_by = actor
            current.save(update_fields=["status", "kind", "offer_number", "created_by",
                                       "email_context", "email_subject", "email_body", "email_recipient"])
            update_released_event_total(locked, current.gross_total)
        except IntegrityError:
            return PriceCalculationRevision.objects.get(event=locked, offer_number=offer_number)
        return current
