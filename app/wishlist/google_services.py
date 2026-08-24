"""
Google Service — robust version with:
- Thread-safe token refresh (lock prevents concurrent refreshes)
- Network retry for transient errors (Timeout, ConnectionError, 5xx)
- 401-aware retry: if API returns 401, force refresh and retry once
- Larger expiry buffer (120s)
- Refresh-token rotation (Google sometimes sends a new refresh_token)
- Clear token invalidation on invalid_grant (user revoked access)
- Structured error logging
"""
import base64
import logging
import threading
import time
from datetime import timedelta
from email.mime.text import MIMEText

import requests
from requests.exceptions import Timeout, ConnectionError as ReqConnectionError

from django.utils import timezone
from django.db import transaction

logger = logging.getLogger(__name__)

TIMEOUT = 12
MAX_RETRIES = 2
RETRY_BACKOFF = 0.6
EXPIRY_BUFFER = 120  # refresh 2 minutes before expiry

_REFRESH_LOCK = threading.Lock()


def _request_with_retry(method, url, **kwargs):
    """HTTP request with retry on transient failures."""
    kwargs.setdefault("timeout", TIMEOUT)
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES:
                logger.warning("Google %s %s → %s (retry %d)", method, url, resp.status_code, attempt + 1)
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            return resp
        except (Timeout, ReqConnectionError) as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                logger.warning("Google %s %s network error: %s (retry %d)", method, url, e, attempt + 1)
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc


class GoogleService:
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    CALENDAR_API = "https://www.googleapis.com/calendar/v3"
    GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
    SHEETS_API = "https://sheets.googleapis.com/v4/spreadsheets"

    SCOPES = " ".join([
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/spreadsheets",
    ])

    # ── Config / Credentials ─────────────────────────────────────────────────

    @staticmethod
    def _get_config():
        from django.conf import settings as django_settings
        from .models import AppConfig
        config = AppConfig.load()
        config._client_id = config.google_client_id or getattr(django_settings, 'GOOGLE_CLIENT_ID', '') or ''
        config._client_secret = config.google_client_secret or getattr(django_settings, 'GOOGLE_CLIENT_SECRET', '') or ''
        config._redirect_uri = config.google_redirect_uri or getattr(django_settings, 'GOOGLE_REDIRECT_URI', '') or ''
        return config

    @staticmethod
    def get_auth_url(state=None):
        from urllib.parse import urlencode
        config = GoogleService._get_config()
        params = {
            "client_id": config._client_id,
            "redirect_uri": config._redirect_uri,
            "response_type": "code",
            "scope": GoogleService.SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
        if state:
            params["state"] = state
        return f"{GoogleService.AUTH_URL}?{urlencode(params)}"

    @staticmethod
    def exchange_code(code):
        config = GoogleService._get_config()
        try:
            resp = _request_with_retry(
                "POST", GoogleService.TOKEN_URL,
                data={
                    "code": code,
                    "client_id": config._client_id,
                    "client_secret": config._client_secret,
                    "redirect_uri": config._redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            return resp.json()
        except Exception as e:
            logger.error("Google exchange_code error: %s", e)
            return {"error": str(e)}

    # ── Token Refresh (thread-safe) ──────────────────────────────────────────

    @staticmethod
    def _refresh_token_locked(token):
        """
        Refresh given GoogleToken. Must be called from within _REFRESH_LOCK.
        Returns True on success, False on failure.
        Deletes token on invalid_grant.
        """
        config = GoogleService._get_config()
        try:
            resp = _request_with_retry(
                "POST", GoogleService.TOKEN_URL,
                data={
                    "refresh_token": token.refresh_token,
                    "client_id": config._client_id,
                    "client_secret": config._client_secret,
                    "grant_type": "refresh_token",
                },
            )
            data = resp.json() if resp is not None else {}
        except Exception as e:
            logger.error("Google refresh network error: %s", e)
            return False

        if resp.status_code == 400 and data.get("error") == "invalid_grant":
            logger.error("Google refresh: invalid_grant — deleting token, user must re-login")
            token.delete()
            return False

        if "access_token" not in data:
            logger.warning("Google refresh failed (status %s): %s", resp.status_code, data)
            return False

        with transaction.atomic():
            token.access_token = data["access_token"]
            token.expires_at = timezone.now() + timedelta(
                seconds=data.get("expires_in", 3600) - EXPIRY_BUFFER
            )
            # Google RARELY sends a new refresh_token on refresh, but save it if it does
            if data.get("refresh_token"):
                token.refresh_token = data["refresh_token"]
            if data.get("scope"):
                token.scopes = data["scope"]
            token.save()
        logger.info("Google token refreshed, expires_at=%s", token.expires_at)
        return True

    @staticmethod
    def get_valid_token(force_refresh=False):
        """
        Returns a valid access token, refreshing if necessary.
        Thread-safe: only one refresh happens at a time.
        """
        from .models import GoogleToken

        with _REFRESH_LOCK:
            token = GoogleToken.objects.order_by("-updated_at").first()
            if not token:
                return None

            token.refresh_from_db()

            needs_refresh = force_refresh or token.is_expired or \
                (token.expires_at - timezone.now()).total_seconds() < EXPIRY_BUFFER

            if needs_refresh:
                if not GoogleService._refresh_token_locked(token):
                    return None
                token.refresh_from_db()

            return token.access_token

    @staticmethod
    def _headers(force_refresh=False):
        token = GoogleService.get_valid_token(force_refresh=force_refresh)
        if not token:
            return None
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    @staticmethod
    def _api_call(method, url, retry_on_401=True, **kwargs):
        """
        Authenticated API call with 401-retry.
        Returns Response object, or None if no token.
        """
        headers = GoogleService._headers()
        if not headers:
            return None
        # Merge caller headers into auth headers
        if "headers" in kwargs:
            merged = {**headers, **kwargs.pop("headers")}
            headers = merged

        try:
            resp = _request_with_retry(method, url, headers=headers, **kwargs)
        except Exception as e:
            logger.error("Google API %s %s error: %s", method, url, e)
            return None

        if resp.status_code == 401 and retry_on_401:
            logger.warning("Google API 401 on %s — forcing token refresh", url)
            headers = GoogleService._headers(force_refresh=True)
            if not headers:
                return resp
            if "headers" in kwargs:
                merged = {**headers, **kwargs.pop("headers")}
                headers = merged
            try:
                resp = _request_with_retry(method, url, headers=headers, **kwargs)
            except Exception as e:
                logger.error("Google API retry %s %s error: %s", method, url, e)
                return None

        return resp

    # ── Calendar ─────────────────────────────────────────────────────────────

    @staticmethod
    def get_calendar_events(time_min=None, time_max=None, max_results=50):
        if not time_min:
            time_min = timezone.now().isoformat()
        if not time_max:
            time_max = (timezone.now() + timedelta(days=90)).isoformat()
        resp = GoogleService._api_call(
            "GET", f"{GoogleService.CALENDAR_API}/calendars/primary/events",
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "maxResults": max_results,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        if resp is None:
            return []
        if resp.status_code == 200:
            return resp.json().get("items", [])
        logger.error("Calendar events error %s: %s", resp.status_code, resp.text[:200])
        return []

    @staticmethod
    def check_date_available(date_str):
        time_min = f"{date_str}T00:00:00Z"
        time_max = f"{date_str}T23:59:59Z"
        events = GoogleService.get_calendar_events(time_min=time_min, time_max=time_max)
        # Als "Frei" markierte Termine (transparency == 'transparent') blockieren nicht
        blocking = [ev for ev in events if ev.get("transparency") != "transparent"]
        return len(blocking) == 0, blocking

    @staticmethod
    def create_calendar_event(event_obj):
        body = {
            "summary": f"🎧 DJ Event: {event_obj.name}",
            "location": f"{event_obj.location}" + (f", {event_obj.full_address}" if event_obj.full_address else ""),
            "description": (
                f"Kunde: {event_obj.client_name}\n"
                f"Tel: {event_obj.client_phone}\n"
                f"E-Mail: {event_obj.client_email}\n"
                f"Typ: {event_obj.event_type}\n"
                f"Gäste: {event_obj.guest_count or 'k.A.'}\n\n"
                f"{event_obj.description}\n\n"
                f"Sonderwünsche: {event_obj.special_requests}"
            ),
        }

        date_iso = event_obj.date.isoformat()
        if event_obj.time_start and event_obj.time_end:
            body["start"] = {"dateTime": f"{date_iso}T{event_obj.time_start.isoformat()}", "timeZone": "Europe/Berlin"}
            body["end"] = {"dateTime": f"{date_iso}T{event_obj.time_end.isoformat()}", "timeZone": "Europe/Berlin"}
        elif event_obj.time_start:
            body["start"] = {"dateTime": f"{date_iso}T{event_obj.time_start.isoformat()}", "timeZone": "Europe/Berlin"}
            body["end"] = {"dateTime": f"{date_iso}T23:59:00", "timeZone": "Europe/Berlin"}
        else:
            body["start"] = {"date": date_iso}
            body["end"] = {"date": date_iso}

        resp = GoogleService._api_call(
            "POST", f"{GoogleService.CALENDAR_API}/calendars/primary/events",
            json=body,
        )
        if resp is None:
            return None, "Kein Google-Token oder Netzwerkfehler"
        if resp.status_code in (200, 201):
            return resp.json().get("id"), None
        return None, f"Calendar API {resp.status_code}: {resp.text[:200]}"

    @staticmethod
    def delete_calendar_event(calendar_event_id):
        if not calendar_event_id:
            return
        GoogleService._api_call(
            "DELETE",
            f"{GoogleService.CALENDAR_API}/calendars/primary/events/{calendar_event_id}",
        )

    # ── Gmail ────────────────────────────────────────────────────────────────

    @staticmethod
    def send_email(to, subject, body_text, sender_name=None):
        message = MIMEText(body_text, "plain", "utf-8")
        message["to"] = to
        message["subject"] = subject
        if sender_name:
            message["from"] = sender_name
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

        resp = GoogleService._api_call(
            "POST", f"{GoogleService.GMAIL_API}/users/me/messages/send",
            json={"raw": raw},
        )
        if resp is None:
            return False, "Kein Google-Token oder Netzwerkfehler"
        if resp.status_code in (200, 201):
            return True, "Gesendet"
        return False, f"Gmail API {resp.status_code}: {resp.text[:200]}"

    # ── Sheets (Backup) ─────────────────────────────────────────────────────

    @staticmethod
    def backup_event_to_sheets(event_obj):
        config = GoogleService._get_config()
        if not config.google_sheets_id:
            return False, "Keine Google Sheets ID konfiguriert"

        sheet_id = config.google_sheets_id
        row = [
            str(event_obj.pk),
            event_obj.name,
            event_obj.date.isoformat() if event_obj.date else "",
            str(event_obj.time_start or ""),
            str(event_obj.time_end or ""),
            event_obj.location,
            event_obj.full_address,
            event_obj.event_type,
            event_obj.get_status_display(),
            event_obj.client_name,
            event_obj.client_email,
            event_obj.client_phone,
            event_obj.client_company,
            str(event_obj.guest_count or ""),
            str(event_obj.total_price or ""),
            event_obj.description,
            event_obj.special_requests,
            event_obj.created_at.isoformat() if event_obj.created_at else "",
            event_obj.updated_at.isoformat() if event_obj.updated_at else "",
            str(event_obj.wishes.count()),
        ]

        # Ensure headers exist (best-effort)
        GoogleService._ensure_sheet_headers(sheet_id)

        # Check if row already exists (by ID in column A)
        search_resp = GoogleService._api_call(
            "GET", f"{GoogleService.SHEETS_API}/{sheet_id}/values/Events!A:A",
        )
        existing_row = None
        if search_resp is not None and search_resp.status_code == 200:
            values = search_resp.json().get("values", [])
            for idx, r in enumerate(values):
                if r and r[0] == str(event_obj.pk):
                    existing_row = idx + 1
                    break

        if existing_row:
            resp = GoogleService._api_call(
                "PUT", f"{GoogleService.SHEETS_API}/{sheet_id}/values/Events!A{existing_row}",
                params={"valueInputOption": "RAW"},
                json={"values": [row]},
            )
        else:
            resp = GoogleService._api_call(
                "POST", f"{GoogleService.SHEETS_API}/{sheet_id}/values/Events!A:T:append",
                params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                json={"values": [row]},
            )

        if resp is None:
            return False, "Kein Google-Token oder Netzwerkfehler"
        if resp.status_code in (200, 201):
            return True, "Backup erfolgreich"
        return False, f"Sheets API {resp.status_code}"

    @staticmethod
    def _ensure_sheet_headers(sheet_id):
        header_row = [
            "ID", "Name", "Datum", "Beginn", "Ende", "Location", "Adresse",
            "Typ", "Status", "Kunde", "E-Mail", "Telefon", "Firma",
            "Gäste", "Preis", "Beschreibung", "Sonderwünsche",
            "Erstellt", "Aktualisiert", "Wünsche"
        ]
        resp = GoogleService._api_call(
            "GET", f"{GoogleService.SHEETS_API}/{sheet_id}/values/Events!A1:T1",
        )
        if resp is None:
            return
        if resp.status_code == 200:
            values = resp.json().get("values", [])
            if not values or values[0] != header_row:
                GoogleService._api_call(
                    "PUT", f"{GoogleService.SHEETS_API}/{sheet_id}/values/Events!A1",
                    params={"valueInputOption": "RAW"},
                    json={"values": [header_row]},
                )
