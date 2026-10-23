from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from wishlist.models import AppConfig, EmailLog, Event, EventStatus


@pytest.fixture
def staff_client(client, db):
    user = get_user_model().objects.create_user(
        username="admin", password="test-pass", is_staff=True,
    )
    client.force_login(user)
    client.user = user
    return client


@pytest.fixture
def confirmed_event(db):
    return Event.objects.create(
        name="Hochzeit Muster",
        date=timezone.localdate() + timedelta(days=30),
        time_start="18:00",
        location="Duisburg",
        client_name="Anna",
        client_email="anna@example.com",
        status=EventStatus.CONFIRMED,
        is_active=True,
        google_calendar_event_id="google-123",
    )


def test_cancel_requires_staff(client, confirmed_event):
    result = client.post(
        reverse("dj_admin:event_cancel", kwargs={"pk": confirmed_event.pk}),
        {"reason": "Terminänderung"},
    )
    assert result.status_code == 302


def test_cancel_preserves_event_and_updates_integrations(staff_client, confirmed_event):
    config = AppConfig.load()
    config.cancellation_email_subject = "Storno: {event_name}"
    config.cancellation_email_body = (
        "Hallo {client_name}, {event_name} wurde storniert. Grund: {cancellation_reason}"
    )
    config.save()

    with (
        patch("wishlist.admin_views.GoogleService.delete_calendar_event",
              return_value=(True, None)) as delete_calendar,
        patch("wishlist.admin_views.GoogleService.send_email",
              return_value=(True, "Gesendet")) as send_email,
        patch("wishlist.admin_views.GoogleService.backup_event_to_sheets",
              return_value=(True, "Backup erfolgreich")) as backup,
    ):
        result = staff_client.post(
            reverse("dj_admin:event_cancel", kwargs={"pk": confirmed_event.pk}),
            {"reason": "Kunde ist verhindert"},
        )

    assert result.status_code == 200
    assert result.json() == {"success": True}
    confirmed_event.refresh_from_db()
    assert confirmed_event.status == EventStatus.CANCELLED
    assert confirmed_event.status_before_cancellation == EventStatus.CONFIRMED
    assert confirmed_event.cancellation_reason == "Kunde ist verhindert"
    assert confirmed_event.cancelled_at is not None
    assert confirmed_event.cancelled_by == staff_client.user
    assert confirmed_event.is_active is False
    assert confirmed_event.google_calendar_event_id == ""
    delete_calendar.assert_called_once_with("google-123")
    backup.assert_called_once_with(confirmed_event)
    assert "Kunde ist verhindert" in send_email.call_args.args[2]
    log = EmailLog.objects.get(event=confirmed_event)
    assert log.success is True


def test_cancel_succeeds_with_warnings_when_google_is_unavailable(
    staff_client, confirmed_event,
):
    with (
        patch("wishlist.admin_views.GoogleService.delete_calendar_event",
              return_value=(False, "Google muss erneut verbunden werden")),
        patch("wishlist.admin_views.GoogleService.send_email",
              return_value=(False, "Google muss erneut verbunden werden")),
        patch("wishlist.admin_views.GoogleService.backup_event_to_sheets",
              return_value=(False, "Google muss erneut verbunden werden")),
    ):
        result = staff_client.post(
            reverse("dj_admin:event_cancel", kwargs={"pk": confirmed_event.pk}),
            {"reason": "Abgesagt"},
        )

    assert result.status_code == 200
    body = result.json()
    assert body["success"] is True
    assert len(body["warnings"]) == 3
    confirmed_event.refresh_from_db()
    assert confirmed_event.status == EventStatus.CANCELLED


def test_cancel_rejects_already_cancelled_event(staff_client, confirmed_event):
    Event.objects.filter(pk=confirmed_event.pk).update(status=EventStatus.CANCELLED)
    result = staff_client.post(
        reverse("dj_admin:event_cancel", kwargs={"pk": confirmed_event.pk}),
        {"reason": "Nochmal"},
    )
    assert result.status_code == 400


def test_restore_confirmed_event_recreates_calendar_and_sends_confirmation(
    staff_client, confirmed_event,
):
    Event.objects.filter(pk=confirmed_event.pk).update(
        status=EventStatus.CANCELLED,
        status_before_cancellation=EventStatus.CONFIRMED,
        cancellation_reason="Abgesagt",
        cancelled_at=timezone.now(),
        cancelled_by=staff_client.user,
        google_calendar_event_id="",
        is_active=False,
    )

    with (
        patch("wishlist.admin_views.GoogleService.create_calendar_event",
              return_value=("new-google-id", None)),
        patch("wishlist.admin_views.GoogleService.send_email",
              return_value=(True, "Gesendet")) as send_email,
        patch("wishlist.admin_views.GoogleService.backup_event_to_sheets",
              return_value=(True, "Backup erfolgreich")),
    ):
        result = staff_client.post(
            reverse("dj_admin:event_restore", kwargs={"pk": confirmed_event.pk}),
        )

    assert result.status_code == 200
    confirmed_event.refresh_from_db()
    assert confirmed_event.status == EventStatus.CONFIRMED
    assert confirmed_event.google_calendar_event_id == "new-google-id"
    assert confirmed_event.is_active is False
    assert confirmed_event.cancellation_reason == ""
    assert confirmed_event.cancelled_at is None
    assert confirmed_event.cancelled_by is None
    send_email.assert_called_once()


def test_restore_inquiry_event_does_not_contact_google(staff_client, db):
    event = Event.objects.create(
        name="Anfrage", date=timezone.localdate() + timedelta(days=10),
        location="Duisburg", status=EventStatus.CANCELLED,
        status_before_cancellation=EventStatus.INQUIRY,
    )
    with (
        patch("wishlist.admin_views.GoogleService.create_calendar_event") as create,
        patch("wishlist.admin_views.GoogleService.send_email") as send,
        patch("wishlist.admin_views.GoogleService.backup_event_to_sheets",
              return_value=(True, "ok")),
    ):
        result = staff_client.post(
            reverse("dj_admin:event_restore", kwargs={"pk": event.pk}),
        )

    assert result.status_code == 200
    event.refresh_from_db()
    assert event.status == EventStatus.INQUIRY
    create.assert_not_called()
    send.assert_not_called()


def test_dashboard_has_cancelled_tab(staff_client, confirmed_event):
    Event.objects.filter(pk=confirmed_event.pk).update(status=EventStatus.CANCELLED)
    result = staff_client.get(reverse("dj_admin:dashboard"))
    assert result.status_code == 200
    assert b"Storniert (1)" in result.content


def test_event_form_does_not_offer_direct_cancelled_status(staff_client, confirmed_event):
    Event.objects.filter(pk=confirmed_event.pk).update(status=EventStatus.CANCELLED)
    result = staff_client.get(
        reverse("dj_admin:event_edit", kwargs={"pk": confirmed_event.pk}),
    )
    assert result.status_code == 200
    assert b'value="cancelled" selected' in result.content
    assert b"Wiederherstellen" in result.content


def test_event_cancellation_uses_an_explicit_form_instead_of_browser_prompt(staff_client, confirmed_event):
    result = staff_client.get(reverse("dj_admin:event_edit", kwargs={"pk": confirmed_event.pk}))
    content = result.content.decode()
    assert result.status_code == 200
    assert '<dialog id="cancel-dialog"' in content
    assert 'id="cancel-reason"' in content
    assert 'id="cancel-submit"' in content
    assert "prompt('Stornogrund" not in content


def test_calendar_id_is_retained_when_deletion_fails(staff_client, confirmed_event):
    with (
        patch("wishlist.admin_views.GoogleService.delete_calendar_event",
              return_value=(False, "offline")),
        patch("wishlist.admin_views.GoogleService.send_email", return_value=(True, "ok")),
        patch("wishlist.admin_views.GoogleService.backup_event_to_sheets", return_value=(True, "ok")),
    ):
        staff_client.post(
            reverse("dj_admin:event_cancel", kwargs={"pk": confirmed_event.pk}),
        )
    confirmed_event.refresh_from_db()
    assert confirmed_event.google_calendar_event_id == "google-123"


def test_restore_past_confirmed_event_does_not_reconfirm(staff_client, confirmed_event):
    Event.objects.filter(pk=confirmed_event.pk).update(
        date=timezone.localdate() - timedelta(days=1),
        status=EventStatus.CANCELLED,
        status_before_cancellation=EventStatus.CONFIRMED,
        google_calendar_event_id="",
    )
    with (
        patch("wishlist.admin_views.GoogleService.create_calendar_event") as create,
        patch("wishlist.admin_views.GoogleService.send_email") as send,
        patch("wishlist.admin_views.GoogleService.backup_event_to_sheets", return_value=(True, "ok")),
    ):
        staff_client.post(reverse("dj_admin:event_restore", kwargs={"pk": confirmed_event.pk}))
    confirmed_event.refresh_from_db()
    assert confirmed_event.status == EventStatus.PAST
    create.assert_not_called()
    send.assert_not_called()


def test_bad_cancellation_template_returns_warning_not_server_error(staff_client, confirmed_event):
    config = AppConfig.load()
    config.cancellation_email_body = "Unbekannt: {wrong_placeholder}"
    config.save()
    with (
        patch("wishlist.admin_views.GoogleService.delete_calendar_event", return_value=(True, None)),
        patch("wishlist.admin_views.GoogleService.backup_event_to_sheets", return_value=(True, "ok")),
    ):
        result = staff_client.post(reverse("dj_admin:event_cancel", kwargs={"pk": confirmed_event.pk}))
    assert result.status_code == 200
    assert "E-Mail-Vorlage" in " ".join(result.json()["warnings"])
