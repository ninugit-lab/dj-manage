from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from requests.exceptions import Timeout

from wishlist.google_services import GoogleService
from wishlist.models import GoogleToken


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


@pytest.fixture
def staff_client(client, db):
    user = get_user_model().objects.create_user(
        username="admin", password="test-pass", is_staff=True,
    )
    client.force_login(user)
    return client


@pytest.fixture
def expired_google_token(db):
    return GoogleToken.objects.create(
        access_token="expired-access",
        refresh_token="durable-refresh",
        expires_at=timezone.now() - timedelta(minutes=5),
        scopes=GoogleService.SCOPES,
    )


def test_status_refreshes_expired_access_token(staff_client, expired_google_token):
    response = FakeResponse(200, {
        "access_token": "fresh-access",
        "expires_in": 3600,
        "scope": GoogleService.SCOPES,
    })

    with patch("wishlist.google_services._request_with_retry", return_value=response):
        result = staff_client.get(reverse("google_status"))

    assert result.status_code == 200
    assert result.json()["connected"] is True
    assert result.json()["state"] == "connected"
    expired_google_token.refresh_from_db()
    assert expired_google_token.access_token == "fresh-access"
    assert expired_google_token.last_refresh_at is not None
    assert expired_google_token.last_success_at is not None


def test_invalid_grant_keeps_diagnostics_and_requires_reconnect(expired_google_token):
    response = FakeResponse(400, {
        "error": "invalid_grant",
        "error_description": "Token has been revoked",
    })

    with patch("wishlist.google_services._request_with_retry", return_value=response):
        status = GoogleService.get_connection_status(refresh_if_needed=True)

    expired_google_token.refresh_from_db()
    assert status["connected"] is False
    assert status["state"] == "reauthorization_required"
    assert expired_google_token.reauthorization_required is True
    assert expired_google_token.last_error_code == "invalid_grant"
    assert "revoked" in expired_google_token.last_error_message
    assert expired_google_token.refresh_token == ""


def test_network_error_is_temporary_and_keeps_refresh_token(expired_google_token):
    with patch("wishlist.google_services._request_with_retry", side_effect=Timeout("offline")):
        status = GoogleService.get_connection_status(refresh_if_needed=True)

    expired_google_token.refresh_from_db()
    assert status["connected"] is True
    assert status["state"] == "temporary_error"
    assert status["reauthorization_required"] is False
    assert expired_google_token.refresh_token == "durable-refresh"
    assert expired_google_token.last_error_code == "network_error"


def test_reconnect_without_new_refresh_token_preserves_existing(
    staff_client, expired_google_token,
):
    session = staff_client.session
    session["google_oauth_state"] = "known-state"
    session.save()
    staff_client.cookies["sessionid"] = session.session_key
    token_payload = {
        "access_token": "new-access",
        "expires_in": 3600,
        "scope": GoogleService.SCOPES,
    }

    with patch.object(GoogleService, "exchange_code", return_value=token_payload):
        result = staff_client.get(
            reverse("google_callback"),
            {"code": "auth-code", "state": "known-state"},
        )

    assert result.status_code == 302, result.content.decode()
    assert GoogleToken.objects.count() == 1
    expired_google_token.refresh_from_db()
    assert expired_google_token.access_token == "new-access"
    assert expired_google_token.refresh_token == "durable-refresh"
    assert expired_google_token.reauthorization_required is False


def test_status_reports_missing_required_scopes(staff_client, db):
    GoogleToken.objects.create(
        access_token="valid",
        refresh_token="refresh",
        expires_at=timezone.now() + timedelta(hours=1),
        scopes="https://www.googleapis.com/auth/calendar",
    )

    result = staff_client.get(reverse("google_status"))

    assert result.status_code == 200
    assert result.json()["connected"] is False
    assert result.json()["state"] == "missing_scopes"
    assert sorted(result.json()["missing_scopes"]) == sorted([
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/spreadsheets",
    ])


def test_refresh_uses_database_row_lock(expired_google_token):
    response = FakeResponse(200, {
        "access_token": "fresh-access", "expires_in": 3600,
        "scope": GoogleService.SCOPES,
    })
    with (
        patch("wishlist.google_services._request_with_retry", return_value=response),
        patch("wishlist.models.GoogleToken.objects.select_for_update",
              wraps=GoogleToken.objects.select_for_update) as row_lock,
    ):
        assert GoogleService.get_valid_token() == "fresh-access"
    row_lock.assert_called()
