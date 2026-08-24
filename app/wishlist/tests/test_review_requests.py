from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from wishlist.models import AppConfig, EmailLog, Event, EventStatus
from wishlist.review_requests import send_review_requests


@pytest.fixture
def config(db):
    cfg = AppConfig.load()
    cfg.review_request_enabled = True
    cfg.google_review_url = "https://g.page/r/TESTID/review"
    cfg.dj_name = "DJ Redoo"
    cfg.save()
    return cfg


@pytest.fixture
def past_event(db):
    return Event.objects.create(
        name="Hochzeit Muster",
        date=timezone.localdate() - timedelta(days=5),
        location="Duisburg",
        client_name="Anna",
        client_email="anna@example.com",
        status=EventStatus.PAST,
    )


@pytest.fixture
def send_ok():
    with patch("wishlist.google_services.GoogleService.send_email",
               return_value=(True, "ok")) as m:
        yield m


def test_sendet_und_markiert(config, past_event, send_ok):
    assert send_review_requests() == 1
    past_event.refresh_from_db()
    assert past_event.review_requested_at is not None
    to, subject, body = send_ok.call_args.args
    assert to == "anna@example.com"
    assert "https://g.page/r/TESTID/review" in body
    assert "Anna" in body
    log = EmailLog.objects.get(event=past_event)
    assert log.success is True


def test_kein_doppelversand(config, past_event, send_ok):
    send_review_requests()
    assert send_review_requests() == 0
    assert send_ok.call_count == 1


def test_deaktiviert(config, past_event, send_ok):
    config.review_request_enabled = False
    config.save()
    assert send_review_requests() == 0
    send_ok.assert_not_called()


def test_ohne_review_url(config, past_event, send_ok):
    config.google_review_url = ""
    config.save()
    assert send_review_requests() == 0
    send_ok.assert_not_called()


def test_wartezeit_noch_nicht_erreicht(config, past_event, send_ok):
    past_event.date = timezone.localdate()
    Event.objects.filter(pk=past_event.pk).update(date=past_event.date)
    assert send_review_requests() == 0
    send_ok.assert_not_called()


def test_storniert_wird_uebersprungen(config, past_event, send_ok):
    Event.objects.filter(pk=past_event.pk).update(status=EventStatus.CANCELLED)
    assert send_review_requests() == 0
    send_ok.assert_not_called()


def test_ohne_email(config, past_event, send_ok):
    Event.objects.filter(pk=past_event.pk).update(client_email="")
    assert send_review_requests() == 0
    send_ok.assert_not_called()


def test_fehlschlag_gibt_event_frei(config, past_event, db):
    with patch("wishlist.google_services.GoogleService.send_email",
               return_value=(False, "SMTP down")):
        assert send_review_requests() == 0
    past_event.refresh_from_db()
    assert past_event.review_requested_at is None
    log = EmailLog.objects.get(event=past_event)
    assert log.success is False
    assert log.error_message == "SMTP down"
