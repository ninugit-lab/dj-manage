import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from wishlist.models import (
    Event,
    EventStatus,
    SongWish,
    EventOffer,
    BlockedClient,
    _extract_spotify_id,
)


# ── _extract_spotify_id (pure) ───────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "37i9dQZF1DXcBWIGoYBM5M"),
    ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "37i9dQZF1DXcBWIGoYBM5M"),
    ("37i9dQZF1DXcBWIGoYBM5M", "37i9dQZF1DXcBWIGoYBM5M"),
    ("  37i9dQZF1DXcBWIGoYBM5M  ", "37i9dQZF1DXcBWIGoYBM5M"),
    ("", ""),
])
def test_extract_spotify_id(value, expected):
    assert _extract_spotify_id(value) == expected


# ── Event ────────────────────────────────────────────────────
@pytest.mark.django_db
def test_event_is_past():
    past = Event.objects.create(name="Old", date=datetime.date(2000, 1, 1), location="X")
    future = Event.objects.create(name="New", date=datetime.date(2999, 1, 1), location="X")
    assert past.is_past is True
    assert future.is_past is False


@pytest.mark.django_db
def test_event_full_address():
    ev = Event.objects.create(name="E", date=datetime.date(2999, 1, 1), location="Hall",
                              address_street="Main 1", address_zip="12345", address_city="Berlin")
    assert ev.full_address == "Main 1, 12345 Berlin"


@pytest.mark.django_db
def test_event_save_marks_past_status():
    ev = Event.objects.create(name="E", date=datetime.date(2000, 1, 1), location="X",
                              status=EventStatus.INQUIRY)
    assert ev.status == EventStatus.PAST


@pytest.mark.django_db
def test_event_save_parses_spotify_url():
    ev = Event.objects.create(name="E", date=datetime.date(2999, 1, 1), location="X",
                              spotify_playlist_id="https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    assert ev.spotify_playlist_id == "37i9dQZF1DXcBWIGoYBM5M"


@pytest.mark.django_db
def test_only_one_active_event():
    a = Event.objects.create(name="A", date=datetime.date(2999, 1, 1), location="X", is_active=True)
    b = Event.objects.create(name="B", date=datetime.date(2999, 1, 2), location="X", is_active=True)
    a.refresh_from_db()
    assert a.is_active is False
    assert b.is_active is True


# ── SongWish ─────────────────────────────────────────────────
@pytest.mark.django_db
def test_song_wish_duration_formatted():
    ev = Event.objects.create(name="E", date=datetime.date(2999, 1, 1), location="X")
    wish = SongWish.objects.create(event=ev, spotify_track_id="t1", track_name="Song",
                                   artist_name="Artist", duration_ms=215000)
    assert wish.duration_formatted == "3:35"


# ── EventOffer ───────────────────────────────────────────────
@pytest.mark.django_db
def test_offer_breakdown_below_threshold():
    ev = Event.objects.create(name="E", date=datetime.date(2999, 1, 1), location="X")
    offer = EventOffer.objects.create(event=ev, guest_count=40, threshold=50,
                                      rate_1=Decimal("10"), rate_2=Decimal("8"),
                                      base_rent=Decimal("200"), cleaning_fee=Decimal("50"))
    b = offer.get_detailed_breakdown()
    assert b["subtotal_variable"] == Decimal("400")  # 40 * 10
    assert b["subtotal_fix"] == Decimal("250")
    assert b["grand_total"] == Decimal("650")


@pytest.mark.django_db
def test_offer_breakdown_above_threshold_with_tech():
    ev = Event.objects.create(name="E", date=datetime.date(2999, 1, 1), location="X")
    offer = EventOffer.objects.create(event=ev, guest_count=80, threshold=50,
                                      rate_1=Decimal("10"), rate_2=Decimal("8"),
                                      base_rent=Decimal("200"), cleaning_fee=Decimal("50"),
                                      tech_package=Decimal("100"), tech_required=True)
    b = offer.get_detailed_breakdown()
    # 50*10 + 30*8 = 740 variable; 200+50+100 = 350 fix
    assert b["subtotal_variable"] == Decimal("740")
    assert b["subtotal_fix"] == Decimal("350")
    assert b["grand_total"] == Decimal("1090")
    assert b["tech_included"] is True


@pytest.mark.django_db
def test_offer_clean_rejects_negative():
    ev = Event.objects.create(name="E", date=datetime.date(2999, 1, 1), location="X")
    offer = EventOffer(event=ev, rate_1=Decimal("-5"))
    with pytest.raises(ValidationError):
        offer.clean()


# ── BlockedClient ────────────────────────────────────────────
@pytest.mark.django_db
def test_blocked_client_is_expired():
    past = timezone.now() - datetime.timedelta(hours=1)
    bc = BlockedClient.objects.create(session_key="s", expires_at=past)
    assert bc.is_expired is True
    permanent = BlockedClient.objects.create(session_key="s2", expires_at=None)
    assert permanent.is_expired is False


@pytest.mark.django_db
def test_is_blocked_returns_active_block():
    BlockedClient.objects.create(session_key="abc", is_active=True)
    assert BlockedClient.is_blocked(session_key="abc") is not None
    assert BlockedClient.is_blocked(session_key="other") is None


@pytest.mark.django_db
def test_is_blocked_deactivates_expired():
    past = timezone.now() - datetime.timedelta(hours=1)
    BlockedClient.objects.create(session_key="abc", is_active=True, expires_at=past)
    assert BlockedClient.is_blocked(session_key="abc") is None
    assert BlockedClient.objects.get(session_key="abc").is_active is False
