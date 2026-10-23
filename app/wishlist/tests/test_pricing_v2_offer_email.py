from decimal import Decimal

import pytest
from wishlist.tests.test_pricing_v2_offers import offer_setup

from wishlist.models import AppConfig, EmailLog, EventStatus, PriceCalculationRevision
from wishlist.pricing_v2.offers import OfferTemplateError, _render_offer_template, build_draft, release_offer, send_offer


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
    second = send_offer(released, retry=True)
    assert second.error == "", second.error
    assert calls[0][0][1:3] == calls[1][0][1:3]
    assert second.revision.offer_number == first.revision.offer_number
    assert second.revision.gross_total == first.revision.gross_total
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
