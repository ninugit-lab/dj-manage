"""
Spotify Service — robust version with:
- Thread-safe token refresh (lock prevents concurrent refreshes)
- Network retry for transient errors (Timeout, ConnectionError, 5xx)
- 401-aware retry: if API returns 401, force refresh and retry once
- Larger expiry buffer (120s instead of 60s)
- Clear token invalidation on invalid_grant (user revoked access)
- Structured error logging
"""
import base64
import logging
import threading
import time
from datetime import timedelta
import json

import requests
from requests.exceptions import RequestException, Timeout, ConnectionError as ReqConnectionError

from django.utils import timezone
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
MAX_RETRIES = 2           # retry for transient network errors
RETRY_BACKOFF = 0.6       # seconds
EXPIRY_BUFFER = 120       # refresh tokens 2 minutes before they expire

# Module-level lock to serialize token refreshes across threads/workers
_REFRESH_LOCK = threading.Lock()


def _request_with_retry(method, url, **kwargs):
    """
    Perform an HTTP request with retries on transient failures.
    Retries on Timeout, ConnectionError, and 5xx responses.
    Returns the final Response object or raises the last exception.
    """
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES:
                logger.warning("Spotify %s %s → %s (retry %d)", method, url, resp.status_code, attempt + 1)
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            return resp
        except (Timeout, ReqConnectionError) as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                logger.warning("Spotify %s %s network error: %s (retry %d)", method, url, e, attempt + 1)
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc


class SpotifyService:
    AUTH_URL = "https://accounts.spotify.com/authorize"
    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_BASE = "https://api.spotify.com/v1"
    SCOPES = "playlist-modify-public playlist-modify-private user-read-playback-state user-read-currently-playing"

    # ── Credentials / Auth URL ───────────────────────────────────────────────

    @staticmethod
    def _b64_credentials():
        raw = f"{settings.SPOTIFY_CLIENT_ID}:{settings.SPOTIFY_CLIENT_SECRET}"
        return base64.b64encode(raw.encode()).decode()

    @staticmethod
    def get_auth_url(state=None):
        from urllib.parse import urlencode
        params = {
            "client_id": settings.SPOTIFY_CLIENT_ID,
            "response_type": "code",
            "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
            "scope": SpotifyService.SCOPES,
        }
        if state:
            params["state"] = state
        return f"{SpotifyService.AUTH_URL}?{urlencode(params)}"

    @staticmethod
    def exchange_code(code):
        try:
            resp = _request_with_retry(
                "POST", SpotifyService.TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.SPOTIFY_REDIRECT_URI,
                },
                headers={"Authorization": f"Basic {SpotifyService._b64_credentials()}"},
            )
            return resp.json()
        except Exception as e:
            logger.error("Spotify exchange_code error: %s", e)
            return {"error": str(e)}

    # ── Token Refresh (thread-safe) ──────────────────────────────────────────

    @staticmethod
    def _refresh_token_locked(token):
        """
        Refresh the given SpotifyToken. Must be called from within the lock.
        Returns True on success, False on failure.
        Deletes token on invalid_grant (user revoked access).
        """
        try:
            resp = _request_with_retry(
                "POST", SpotifyService.TOKEN_URL,
                data={"grant_type": "refresh_token", "refresh_token": token.refresh_token},
                headers={"Authorization": f"Basic {SpotifyService._b64_credentials()}"},
            )
            data = resp.json() if resp is not None else {}
        except Exception as e:
            logger.error("Spotify refresh network error: %s", e)
            return False

        if resp.status_code == 400 and data.get("error") == "invalid_grant":
            logger.error("Spotify refresh: invalid_grant — deleting token, user must re-login")
            token.delete()
            return False

        if "access_token" not in data:
            logger.warning("Spotify refresh failed: %s", data)
            return False

        with transaction.atomic():
            token.access_token = data["access_token"]
            token.expires_at = timezone.now() + timedelta(
                seconds=data.get("expires_in", 3600) - EXPIRY_BUFFER
            )
            if data.get("refresh_token"):
                token.refresh_token = data["refresh_token"]
            token.save()
        logger.info("Spotify token refreshed, expires_at=%s", token.expires_at)
        return True

    @staticmethod
    def get_valid_token(force_refresh=False):
        """
        Returns a valid DJ access token, refreshing if necessary.
        Thread-safe: only one refresh happens at a time across all workers.
        """
        from .models import SpotifyToken

        with _REFRESH_LOCK:
            token = SpotifyToken.objects.order_by("-updated_at").first()
            if not token:
                return None

            # Re-read within lock in case another thread refreshed already
            token.refresh_from_db()

            needs_refresh = force_refresh or token.is_expired or \
                (token.expires_at - timezone.now()).total_seconds() < EXPIRY_BUFFER

            if needs_refresh:
                if not SpotifyService._refresh_token_locked(token):
                    return None
                token.refresh_from_db()

            return token.access_token

    # ── Client Credentials (for public search) ──────────────────────────────

    # Cache the client credentials token to avoid fetching one per search
    _cc_token = None
    _cc_expires_at = 0  # unix timestamp

    @staticmethod
    def _get_client_credentials_token():
        now = time.time()
        if SpotifyService._cc_token and now < SpotifyService._cc_expires_at - EXPIRY_BUFFER:
            return SpotifyService._cc_token
        try:
            resp = _request_with_retry(
                "POST", SpotifyService.TOKEN_URL,
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {SpotifyService._b64_credentials()}"},
            )
            if resp.status_code != 200:
                logger.error("Spotify client_credentials failed %s: %s", resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            SpotifyService._cc_token = data.get("access_token")
            SpotifyService._cc_expires_at = now + data.get("expires_in", 3600)
            return SpotifyService._cc_token
        except Exception as e:
            logger.error("Spotify client_credentials error: %s", e)
            return None

    # ── Search ──────────────────────────────────────────────────────────────

    @staticmethod
    def search_tracks(query, limit=8, types="track"):
        if not settings.SPOTIFY_CLIENT_ID or not settings.SPOTIFY_CLIENT_SECRET:
            logger.warning("Spotify credentials not configured")
            return []

        cc_token = SpotifyService._get_client_credentials_token()
        if not cc_token:
            return []

        def _do_search(tok):
            return _request_with_retry(
                "GET", f"{SpotifyService.API_BASE}/search",
                params={"q": query, "type": types, "limit": limit, "market": "DE"},
                headers={"Authorization": f"Bearer {tok}"},
            )

        try:
            resp = _do_search(cc_token)
            if resp.status_code == 401:
                SpotifyService._cc_token = None
                cc_token = SpotifyService._get_client_credentials_token()
                if cc_token:
                    resp = _do_search(cc_token)
            if resp.status_code != 200:
                logger.error("Spotify search returned %s: %s", resp.status_code, resp.text[:200])
                return [] if "artist" not in types else {"tracks": [], "artists": []}

            data = resp.json()
            tracks = []
            for item in data.get("tracks", {}).get("items", []):
                images = item["album"].get("images", [])
                tracks.append({
                    "id": item["id"],
                    "name": item["name"],
                    "artists": ", ".join(a["name"] for a in item["artists"]),
                    "album": item["album"]["name"],
                    "cover": images[0]["url"] if images else "",
                    "cover_small": images[-1]["url"] if images else "",
                    "duration_ms": item["duration_ms"],
                    "preview_url": item.get("preview_url") or "",
                    "popularity": item.get("popularity", 0),
                })

            if "artist" not in types:
                return tracks

            artists = []
            for item in data.get("artists", {}).get("items", []):
                images = item.get("images", [])
                artists.append({
                    "id": item["id"],
                    "name": item["name"],
                    "image": images[0]["url"] if images else "",
                    "genres": item.get("genres", [])[:3],
                    "popularity": item.get("popularity", 0),
                })
            return {"tracks": tracks, "artists": artists}

        except Exception as e:
            logger.error("Spotify search error: %s", e)
            return [] if "artist" not in types else {"tracks": [], "artists": []}

    # ── Playlist Add (with 401-retry) ───────────────────────────────────────

    @staticmethod
    def add_to_playlist(playlist_id, track_id):
        """
        Add a track to a playlist. Retries once on 401 with fresh token.
        """
        token = SpotifyService.get_valid_token()
        if not token:
            return False, "Kein Spotify-Token. Bitte als DJ einloggen unter /spotify/login/"

        url = f"{SpotifyService.API_BASE}/playlists/{playlist_id}/items"
        body = {"uris": [f"spotify:track:{track_id}"]}

        def _post(tok):
            return _request_with_retry(
                "POST", url, json=body,
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            )

        try:
            resp = _post(token)

            # 401 → token was invalidated server-side; force refresh and retry once
            if resp.status_code == 401:
                logger.warning("Spotify add_to_playlist 401, forcing token refresh")
                token = SpotifyService.get_valid_token(force_refresh=True)
                if not token:
                    return False, "Spotify-Token konnte nicht erneuert werden. Bitte neu einloggen."
                resp = _post(token)

            if resp.status_code in (200, 201):
                return True, "Erfolgreich zur Playlist hinzugefügt"

            logger.error("Spotify add_to_playlist returned %s: %s", resp.status_code, resp.text[:300])
            return False, f"Spotify-Fehler {resp.status_code}"
        except Exception as e:
            logger.error("Spotify add_to_playlist error: %s", e)
            return False, f"Verbindungsfehler: {e}"

    # ── Playlist Remove (with 401-retry) ────────────────────────────────────

    @staticmethod
    def remove_from_playlist(playlist_id, track_id):
        token = SpotifyService.get_valid_token()
        if not token:
            return False, "Kein Spotify-Token."

        # 1. URI absolut sicher aufbereiten
        clean_id = track_id.strip().split(':')[-1] # Holt nur die ID, egal ob uri oder id übergeben wurde
        track_uri = f"spotify:track:{clean_id}"
        
        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/items"
        
        payload = {
            "tracks": [{"uri": track_uri}]
        }

        try:
            response = _request_with_retry(
                "DELETE", url,
                data=json.dumps(payload),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                },
            )

            logger.debug("Spotify DELETE %s from %s → %s", track_uri, playlist_id, response.status_code)

            if response.status_code == 401:
                token = SpotifyService.get_valid_token(force_refresh=True)
                if not token:
                    return False, "Spotify-Token abgelaufen — bitte neu verbinden."
                response = _request_with_retry(
                    "DELETE", url,
                    data=json.dumps(payload),
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                )

            if response.status_code in (200, 201):
                return True, "Erfolgreich entfernt"

            return False, f"Spotify-Fehler {response.status_code}: {response.text[:200]}"

        except Exception as e:
            logger.error("Spotify remove_from_playlist error: %s", e)
            return False, str(e)

    # ── Get Current User ID ──────────────────────────────────────────────────

    @staticmethod
    def get_user_id():
        """Get the Spotify user ID of the authenticated user."""
        token = SpotifyService.get_valid_token()
        if not token:
            return None
        try:
            resp = _request_with_retry(
                "GET", f"{SpotifyService.API_BASE}/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401:
                token = SpotifyService.get_valid_token(force_refresh=True)
                if not token:
                    return None
                resp = _request_with_retry(
                    "GET", f"{SpotifyService.API_BASE}/me",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code == 200:
                return resp.json().get("id")
            logger.error("Spotify get_user_id returned %s", resp.status_code)
            return None
        except Exception as e:
            logger.error("Spotify get_user_id error: %s", e)
            return None

    # ── Create Playlist ──────────────────────────────────────────────────────

    @staticmethod
    def create_playlist(name, description="", public=False):
        """
        Create a new playlist for the authenticated user.
        Returns (playlist_id, playlist_url) on success, (None, error_msg) on failure.
        """
        user_id = SpotifyService.get_user_id()
        if not user_id:
            return None, "Spotify User-ID nicht gefunden. Bitte neu einloggen."

        token = SpotifyService.get_valid_token()
        if not token:
            return None, "Kein Spotify-Token."

        url = f"{SpotifyService.API_BASE}/users/{user_id}/playlists"
        body = {"name": name, "description": description, "public": public}

        def _post(tok):
            return _request_with_retry(
                "POST", url, json=body,
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            )

        try:
            resp = _post(token)
            if resp.status_code == 401:
                token = SpotifyService.get_valid_token(force_refresh=True)
                if not token:
                    return None, "Token-Refresh fehlgeschlagen."
                resp = _post(token)

            if resp.status_code in (200, 201):
                data = resp.json()
                playlist_id = data.get("id")
                playlist_url = data.get("external_urls", {}).get("spotify", "")
                logger.info("Spotify playlist created: %s (%s)", name, playlist_id)
                return playlist_id, playlist_url

            logger.error("Spotify create_playlist returned %s: %s", resp.status_code, resp.text[:300])
            return None, f"Spotify-Fehler {resp.status_code}"
        except Exception as e:
            logger.error("Spotify create_playlist error: %s", e)
            return None, f"Verbindungsfehler: {e}"

    # ── Now Playing / Playback State ─────────────────────────────────────────

    @staticmethod
    def get_current_playback():
        """Returns current playback state or None if nothing is playing."""
        token = SpotifyService.get_valid_token()
        if not token:
            return None
        try:
            resp = _request_with_retry(
                "GET", f"{SpotifyService.API_BASE}/me/player",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401:
                token = SpotifyService.get_valid_token(force_refresh=True)
                if not token:
                    return None
                resp = _request_with_retry(
                    "GET", f"{SpotifyService.API_BASE}/me/player",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code == 204 or resp.status_code == 200 and not resp.text:
                return None
            if resp.status_code != 200:
                logger.error("Spotify get_current_playback returned %s", resp.status_code)
                return None
            data = resp.json()
            item = data.get("item")
            if not item:
                return None
            images = item.get("album", {}).get("images", [])
            device = data.get("device", {})
            return {
                "track_name": item.get("name", ""),
                "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                "album": item.get("album", {}).get("name", ""),
                "cover": images[0]["url"] if images else "",
                "track_id": item.get("id", ""),
                "progress_ms": data.get("progress_ms", 0),
                "duration_ms": item.get("duration_ms", 0),
                "is_playing": data.get("is_playing", False),
                "device": {
                    "name": device.get("name", ""),
                    "type": device.get("type", ""),
                    "id": device.get("id", ""),
                },
            }
        except Exception as e:
            logger.error("Spotify get_current_playback error: %s", e)
            return None

    # ── Devices ──────────────────────────────────────────────────────────────

    @staticmethod
    def get_devices():
        """Returns list of available Spotify devices."""
        token = SpotifyService.get_valid_token()
        if not token:
            return []
        try:
            resp = _request_with_retry(
                "GET", f"{SpotifyService.API_BASE}/me/player/devices",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401:
                token = SpotifyService.get_valid_token(force_refresh=True)
                if not token:
                    return []
                resp = _request_with_retry(
                    "GET", f"{SpotifyService.API_BASE}/me/player/devices",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.status_code != 200:
                logger.error("Spotify get_devices returned %s", resp.status_code)
                return []
            return [
                {
                    "id": d.get("id", ""),
                    "name": d.get("name", ""),
                    "type": d.get("type", ""),
                    "is_active": d.get("is_active", False),
                }
                for d in resp.json().get("devices", [])
            ]
        except Exception as e:
            logger.error("Spotify get_devices error: %s", e)
            return []

    # ── Audio Features ───────────────────────────────────────────────────────

    @staticmethod
    def get_audio_features(track_id):
        """Returns audio features for a track. Uses client credentials (no user scope needed)."""
        cc_token = SpotifyService._get_client_credentials_token()
        if not cc_token:
            return None
        try:
            resp = _request_with_retry(
                "GET", f"{SpotifyService.API_BASE}/audio-features/{track_id}",
                headers={"Authorization": f"Bearer {cc_token}"},
            )
            if resp.status_code == 401:
                SpotifyService._cc_token = None
                cc_token = SpotifyService._get_client_credentials_token()
                if not cc_token:
                    return None
                resp = _request_with_retry(
                    "GET", f"{SpotifyService.API_BASE}/audio-features/{track_id}",
                    headers={"Authorization": f"Bearer {cc_token}"},
                )
            if resp.status_code != 200:
                logger.error("Spotify get_audio_features returned %s", resp.status_code)
                return None
            data = resp.json()
            return {
                "bpm": data.get("tempo"),
                "energy": data.get("energy"),
                "danceability": data.get("danceability"),
                "valence": data.get("valence"),
                "acousticness": data.get("acousticness"),
                "instrumentalness": data.get("instrumentalness"),
            }
        except Exception as e:
            logger.error("Spotify get_audio_features error: %s", e)
            return None

    # ── Artist Top Tracks ────────────────────────────────────────────────────

    @staticmethod
    def get_artist_top_tracks(artist_id):
        """Returns top tracks for an artist in DE market."""
        cc_token = SpotifyService._get_client_credentials_token()
        if not cc_token:
            return []
        try:
            resp = _request_with_retry(
                "GET", f"{SpotifyService.API_BASE}/artists/{artist_id}/top-tracks",
                params={"market": "DE"},
                headers={"Authorization": f"Bearer {cc_token}"},
            )
            if resp.status_code == 401:
                SpotifyService._cc_token = None
                cc_token = SpotifyService._get_client_credentials_token()
                if not cc_token:
                    return []
                resp = _request_with_retry(
                    "GET", f"{SpotifyService.API_BASE}/artists/{artist_id}/top-tracks",
                    params={"market": "DE"},
                    headers={"Authorization": f"Bearer {cc_token}"},
                )
            if resp.status_code != 200:
                logger.error("Spotify get_artist_top_tracks returned %s", resp.status_code)
                return []
            tracks = []
            for item in resp.json().get("tracks", []):
                images = item.get("album", {}).get("images", [])
                tracks.append({
                    "id": item["id"],
                    "name": item["name"],
                    "artists": ", ".join(a["name"] for a in item.get("artists", [])),
                    "album": item.get("album", {}).get("name", ""),
                    "cover": images[0]["url"] if images else "",
                    "cover_small": images[-1]["url"] if images else "",
                    "duration_ms": item.get("duration_ms", 0),
                    "preview_url": item.get("preview_url") or "",
                    "popularity": item.get("popularity", 0),
                })
            return tracks
        except Exception as e:
            logger.error("Spotify get_artist_top_tracks error: %s", e)
            return []
