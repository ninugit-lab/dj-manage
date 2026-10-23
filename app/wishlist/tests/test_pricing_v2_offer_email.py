from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Event, Thread

import pytest
from django.db import OperationalError
from django.utils import timezone
from wishlist.tests.test_pricing_v2_offers import offer_setup

from wishlist.models import AppConfig, EmailLog, EventStatus, PriceCalculationRevision
from wishlist.pricing_v2.offers import (OfferTemplateError, _render_offer_template, build_draft,
                                        recover_stale_send, release_offer, send_offer)


def test_offer_template_contract_has_only_the_six_fields():
    context = {name: name for name in (
        "client_name", "event_name", "event_date", "price_breakdown", "gross_total", "dj_name")}
    assert _render_offer_template("{client_name} {event_name} {event_date} {price_breakdown} {gross_total} {dj_name}", context)
    with pytest.raises(OfferTemplateError):
        _render_offer_template("{unknown}", context)
    with pytest.raises(OfferTemplateError):
        _render_offer_template("{gross_total:.2f}", context)


def test_email_log_can_be_linked_to_frozen_revision(db):
    assert "revision" in {field.name for field in EmailLog._meta.fields}
    assert AppConfig._meta.get_field("offer_email_subject").default
    assert PriceCalculationRevision._meta.get_field("gross_total").get_internal_type() == "DecimalField"
    assert Decimal("1.00") == Decimal("1.00")


def test_retry_uses_release_snapshot_after_event_and_config_mutation(offer_setup, monkeypatch):
    event, actor = offer_setup
    event.client_email = "client@example.test"
    event.client_name = "Original Client"
    event.save(update_fields=["client_email", "client_name"])
    config = AppConfig.load()
    config.offer_email_subject = "Offer {event_name} {gross_total}"
    config.offer_email_body = "{client_name}|{event_name}|{event_date}|{price_breakdown}|{gross_total}|{dj_name}"
    config.dj_name = "DJ Original"
    config.save()
    released = release_offer(event, actor) if build_draft(event, actor) else None
    calls = []
    monkeypatch.setattr("wishlist.google_services.GoogleService.send_email",
                        lambda *args, **kwargs: (calls.append((args, kwargs)) or (True, "Gesendet")))
    first = send_offer(released)
    assert first.error == "", first.error
    event.name = "MUTATED"
    event.client_name = "Other Client"
    event.date = event.date.replace(year=2027)
    event.save(update_fields=["name", "client_name", "date"])
    config.offer_email_subject = "CHANGED {event_name}"
    config.offer_email_body = "CHANGED"
    config.dj_name = "Changed DJ"
    config.save()
    with pytest.raises(ValueError, match="bereits gesendet"):
        send_offer(released, retry=True)
    assert len(calls) == 1
    assert first.revision.offer_number == released.offer_number
    assert first.revision.gross_total == released.gross_total
    assert EventStatus.INQUIRY == event.status


def test_delivery_exception_is_sanitized_and_retry_is_separate_attempt(offer_setup, monkeypatch):
    event, actor = offer_setup
    event.client_email = "client@example.test"
    event.save(update_fields=["client_email"])
    released = release_offer(event, actor) if build_draft(event, actor) else None
    monkeypatch.setattr("wishlist.google_services.GoogleService.send_email",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            RuntimeError("token=SECRET https://private.example/path")))
    result = send_offer(released)
    assert result.sent is False
    assert result.error == "EMAIL_DELIVERY_FAILED"
    log = EmailLog.objects.get(revision=released)
    assert log.success is False
    assert "SECRET" not in log.error_message and "private.example" not in log.error_message
    assert released.status == PriceCalculationRevision.Status.SEND_FAILED


def test_send_endpoint_is_revision_scoped_post_staff_csrf_and_blocks_unreleased(offer_setup):
    event, actor = offer_setup
    from django.test import Client
    from django.urls import reverse
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    draft = build_draft(event, actor)
    url = reverse("dj_admin:price_revision_send", kwargs={"pk": draft.pk})
    assert client.post(url).status_code == 403
    client.get(reverse("dj_admin:event_edit", kwargs={"pk": event.pk}))
    token = client.cookies["csrftoken"].value
    assert client.post(url, HTTP_X_CSRFTOKEN=token).status_code == 400


def test_recipient_is_frozen_and_event_email_change_cannot_leak(offer_setup, monkeypatch):
    event, actor = offer_setup
    event.client_email = "original@example.test"
    event.save(update_fields=["client_email"])
    released = release_offer(event, actor) if build_draft(event, actor) else None
    event.client_email = "attacker@example.test"
    event.save(update_fields=["client_email"])
    recipients = []
    monkeypatch.setattr("wishlist.google_services.GoogleService.send_email",
                        lambda to, *args, **kwargs: (recipients.append(to) or (True, "ok")))
    send_offer(released)
    assert recipients == ["original@example.test"]
    assert EmailLog.objects.get(revision=released).recipient == "original@example.test"


def test_historic_offer_without_snapshot_is_controlled_and_never_fabricated(offer_setup):
    event, actor = offer_setup
    released = release_offer(event, actor) if build_draft(event, actor) else None
    PriceCalculationRevision.objects.filter(pk=released.pk).update(
        email_context={}, email_subject="", email_body="", email_recipient="")
    released.refresh_from_db()
    with pytest.raises(OfferTemplateError, match="erneut freigeben"):
        send_offer(released)


def test_send_claim_is_committed_before_delivery_and_blocks_second_attempt(offer_setup, monkeypatch):
    event, actor = offer_setup
    event.client_email = "client@example.test"
    event.save(update_fields=["client_email"])
    released = release_offer(event, actor) if build_draft(event, actor) else None
    calls = []
    monkeypatch.setattr("wishlist.google_services.GoogleService.send_email",
                        lambda *args, **kwargs: (calls.append(args) or (True, "ok")))

    first = send_offer(released)
    assert first.sent is True
    with pytest.raises(ValueError, match="bereits gesendet"):
        send_offer(released)
    assert len(calls) == 1


def test_stale_sending_claim_requires_explicit_recovery_before_retry(offer_setup, monkeypatch):
    event, actor = offer_setup
    event.client_email = "client@example.test"
    event.save(update_fields=["client_email"])
    released = release_offer(event, actor) if build_draft(event, actor) else None
    PriceCalculationRevision.objects.filter(
        pk=released.pk,
    ).update(status="sending", send_claimed_at=timezone.now() - timedelta(hours=1))
    released.refresh_from_db()
    with pytest.raises(ValueError, match="Versand läuft"):
        send_offer(released, retry=True)
    with pytest.raises(ValueError, match="Doppelzustellung"):
        recover_stale_send(released, confirm=True)
    recovered = recover_stale_send(released, confirm=True, acknowledge_duplicate_risk=True)
    assert recovered.status == PriceCalculationRevision.Status.SEND_FAILED
    monkeypatch.setattr("wishlist.google_services.GoogleService.send_email", lambda *args, **kwargs: (True, "ok"))
    assert send_offer(recovered, retry=True).sent is True


def test_old_sender_cannot_complete_after_stale_claim_recovery(offer_setup, monkeypatch):
    event, actor = offer_setup
    event.client_email = "client@example.test"
    event.save(update_fields=["client_email"])
    released = release_offer(event, actor) if build_draft(event, actor) else None
    started = Event()
    calls = []

    def deliver(*args, **kwargs):
        calls.append(args)
        started.set()
        return True, "ok"

    monkeypatch.setattr("wishlist.google_services.GoogleService.send_email", deliver)
    result = []

    def old_sender():
        result.append(send_offer(PriceCalculationRevision.objects.get(pk=released.pk)))

    thread = Thread(target=old_sender)
    thread.start()
    assert started.wait(timeout=5)
    stale = PriceCalculationRevision.objects.get(pk=released.pk)
    stale.send_claimed_at = timezone.now() - timedelta(hours=1)
    stale.save(update_fields=["send_claimed_at"])
    recover_stale_send(stale, confirm=True, acknowledge_duplicate_risk=True)
    thread.join(timeout=5)
    assert len(calls) == 1
    assert result[0].sent is False
    assert result[0].error == "SEND_CLAIM_LOST"
    assert EmailLog.objects.filter(revision=released).count() == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_send_requests_claim_one_delivery(offer_setup, monkeypatch):
    event, actor = offer_setup
    event.client_email = "client@example.test"
    event.save(update_fields=["client_email"])
    released = release_offer(event, actor) if build_draft(event, actor) else None
    barrier = Barrier(2)
    calls = []

    def deliver(*args, **kwargs):
        calls.append(args)
        return True, "ok"

    monkeypatch.setattr("wishlist.google_services.GoogleService.send_email", deliver)
    results, errors = [], []

    def request():
        from django.db import close_old_connections
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            results.append(send_offer(PriceCalculationRevision.objects.get(pk=released.pk), retry=True))
        except Exception as exc:
            errors.append(exc)
        finally:
            close_old_connections()

    threads = [Thread(target=request) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(calls) <= 1
    assert sum(result.sent for result in results) <= 1
    assert all(isinstance(error, (ValueError, OperationalError)) for error in errors)
