import json
import logging
from datetime import timedelta

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.admin.views.decorators import staff_member_required
from django.utils import timezone

from .models import Event, SongWish, SpotifyToken, AppConfig, PriceItem, EventPriceCalculation, EmailLog, EventStatus, BlockedClient, PricingPackage, PricingRule
from .spotify import SpotifyService
from .google_services import GoogleService
from .price_engine import PriceEngine

logger = logging.getLogger(__name__)


def _get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def get_active_event():
    return Event.objects.filter(is_active=True).first()


# ── Public Wishlist ──────────────────────────────────────────────────────────

def wishlist_view(request):
    event = get_active_event()

    # Reset session wish_ids when active event changes
    current_event_id = request.session.get('current_event_id')
    if event and current_event_id != event.pk:
        request.session['wish_ids'] = []
        request.session['current_event_id'] = event.pk
        request.session.modified = True

    # Blocked check — returns BlockedClient object or None
    client_ip = _get_client_ip(request)
    session_key = request.session.session_key
    block_obj = None
    is_blocked = False
    if event:
        block_obj = BlockedClient.is_blocked(session_key=session_key, ip_address=client_ip, event=event)
        is_blocked = block_obj is not None
    wishes_pending = []
    wishes_played = []
    if event:
        wishes_pending = event.wishes.filter(played=False).order_by('-created_at')
        wishes_played = event.wishes.filter(played=True).order_by('-created_at') if event.wishlist_show_played else []

    # Reconcile session wish IDs: keep only those that still exist in this event.
    session_wishes = request.session.get('wish_ids', [])
    if event and session_wishes:
        existing_ids = set(event.wishes.filter(pk__in=session_wishes).values_list('pk', flat=True))
        valid_session_wishes = [wid for wid in session_wishes if wid in existing_ids]
        if valid_session_wishes != session_wishes:
            request.session['wish_ids'] = valid_session_wishes
            request.session.modified = True
        session_wishes = valid_session_wishes

    max_wishes = event.max_wishes_per_session if event else 3
    can_add_more = len(session_wishes) < max_wishes and not is_blocked

    config = AppConfig.load()

    response = render(request, 'wishlist/index.html', {
        'event': event,
        'config': config,
        'wishes_pending': wishes_pending,
        'wishes_played': wishes_played,
        'session_wishes': session_wishes,
        'can_add_more': can_add_more,
        'is_blocked': is_blocked,
        'block_message': block_obj.public_message if block_obj else '',
        'wishes_left': max(0, max_wishes - len(session_wishes)),
    })
    response['Turbo-Visit-Control'] = 'reload'
    return response


@require_GET
def search_tracks(request):
    query = request.GET.get('q', '').strip()
    search_type = request.GET.get('type', 'track')
    if len(query) < 2:
        return JsonResponse({'tracks': [], 'artists': [], 'error': None})
    try:
        if search_type == 'artist':
            result = SpotifyService.search_tracks(query, limit=8, types="track,artist")
            if isinstance(result, dict):
                return JsonResponse({'tracks': result.get('tracks', []), 'artists': result.get('artists', []), 'error': None})
            return JsonResponse({'tracks': [], 'artists': [], 'error': None})
        tracks = SpotifyService.search_tracks(query, limit=8)
        return JsonResponse({'tracks': tracks, 'artists': [], 'error': None})
    except Exception:
        logger.exception("search_tracks error")
        return JsonResponse({'tracks': [], 'artists': [], 'error': 'Suche momentan nicht verfügbar'})


@require_POST
def add_wish(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Ungültige Anfrage'}, status=400)

    event = get_active_event()
    if not event:
        return JsonResponse({'success': False, 'error': 'Kein aktives Event'}, status=400)

    # Blocked check
    client_ip = _get_client_ip(request)
    if not request.session.session_key:
        request.session.create()
    block = BlockedClient.is_blocked(session_key=request.session.session_key, ip_address=client_ip, event=event)
    if block:
        msg = block.public_message or 'Du wurdest für diese Wishlist gesperrt.'
        return JsonResponse({'success': False, 'error': msg}, status=403)

    # Guest name required?
    guest_name = str(data.get('guest_name', '') or '').strip()[:100]
    if event.wishlist_require_name and not guest_name:
        return JsonResponse({'success': False, 'error': 'Bitte gib deinen Namen ein.'}, status=400)

    session_key = request.session.session_key

    session_wishes = request.session.get('wish_ids', [])
    # Reconcile: drop IDs that no longer exist in this event's wishes
    if session_wishes:
        existing_ids = set(event.wishes.filter(pk__in=session_wishes).values_list('pk', flat=True))
        session_wishes = [wid for wid in session_wishes if wid in existing_ids]
        request.session['wish_ids'] = session_wishes
        request.session.modified = True
    if len(session_wishes) >= event.max_wishes_per_session:
        return JsonResponse({
            'success': False,
            'error': f'Du hast bereits {event.max_wishes_per_session} Wünsche abgegeben.',
        }, status=429)

    track_id = data.get('track_id', '').strip()
    if not track_id:
        return JsonResponse({'success': False, 'error': 'Track ID fehlt'}, status=400)

    track_name = str(data.get('track_name', '') or '').strip()[:300]
    artist_name = str(data.get('artist_name', '') or '').strip()[:300]
    if not track_name or not artist_name:
        return JsonResponse({'success': False, 'error': 'Lied- oder Künstlername fehlt'}, status=400)

    if SongWish.objects.filter(event=event, spotify_track_id=track_id).exists():
        return JsonResponse({'success': False, 'error': 'Dieses Lied wurde bereits gewünscht!'}, status=409)

    try:
        wish = SongWish.objects.create(
            event=event,
            spotify_track_id=track_id,
            track_name=track_name,
            artist_name=artist_name,
            album_name=str(data.get('album_name', '') or '')[:300],
            album_cover_url=str(data.get('cover_url', '') or '')[:500],
            duration_ms=int(data.get('duration_ms', 0) or 0),
            preview_url=str(data.get('preview_url', '') or '')[:500] or None,
            guest_name=guest_name,
            ip_address=client_ip,
            session_key=session_key,
        )
    except Exception:
        logger.exception("Error creating SongWish")
        return JsonResponse({'success': False, 'error': 'Datenbankfehler'}, status=500)

    # Cache audio features
    try:
        features = SpotifyService.get_audio_features(track_id)
        if features:
            wish.audio_bpm = features.get('bpm')
            wish.audio_energy = features.get('energy')
            wish.audio_danceability = features.get('danceability')
            wish.audio_valence = features.get('valence')
            wish.audio_acousticness = features.get('acousticness')
            wish.audio_instrumentalness = features.get('instrumentalness')
            wish.save(update_fields=['audio_bpm', 'audio_energy', 'audio_danceability',
                                     'audio_valence', 'audio_acousticness', 'audio_instrumentalness'])
    except Exception:
        logger.warning("Audio features fetch failed for track %s", track_id)

    playlist_added = False
    if event.spotify_playlist_id:
        try:
            success, msg = SpotifyService.add_to_playlist(event.spotify_playlist_id, track_id)
            if success:
                wish.added_to_playlist = True
                wish.save(update_fields=['added_to_playlist'])
                playlist_added = True
        except Exception:
            logger.exception("Spotify add_to_playlist error")

    session_wishes.append(wish.id)
    request.session['wish_ids'] = session_wishes
    request.session.modified = True

    return JsonResponse({
        'success': True,
        'wish_id': wish.id,
        'playlist_added': playlist_added,
        'wishes_left': max(0, event.max_wishes_per_session - len(session_wishes)),
        'guest_name': guest_name,
    })


# Turbo Stream endpoint for live wish updates
@require_GET
def wishes_stream(request):
    """Returns current wishes as HTML partial for Turbo Frame updates."""
    event = get_active_event()
    wishes_pending = []
    wishes_played = []
    if event:
        wishes_pending = event.wishes.filter(played=False).order_by('-created_at')
        if event.wishlist_show_played:
            wishes_played = event.wishes.filter(played=True).order_by('-created_at')
    return render(request, 'wishlist/_wishes_list.html', {
        'event': event,
        'wishes_pending': wishes_pending,
        'wishes_played': wishes_played,
    })


# ── Public Event Booking Form ────────────────────────────────────────────────

def event_form_view(request):
    config = AppConfig.load()
    if not config.event_form_enabled:
        return render(request, 'wishlist/event_form_disabled.html')

    from .models import PricingPackage
    import json as _json
    price_items = PriceItem.objects.filter(is_active=True, is_public=True)
    pricing_packages = PricingPackage.objects.filter(is_active=True, is_public=True).order_by('sort_order')
    dj_coords_json = _json.dumps({
        'lat': float(config.dj_home_lat) if config.dj_home_lat is not None else None,
        'lon': float(config.dj_home_lon) if config.dj_home_lon is not None else None,
    })
    response = render(request, 'wishlist/event_form.html', {
        'config': config,
        'price_items': price_items,
        'pricing_packages': pricing_packages,
        'dj_coords_json': dj_coords_json,
    })
    response['Turbo-Visit-Control'] = 'reload'
    return response


@require_GET
def check_date_availability(request):
    """API: Check if a date is available in Google Calendar."""
    date_str = request.GET.get('date', '')
    if not date_str:
        return JsonResponse({'available': False, 'error': 'Kein Datum angegeben'})

    # Also check internal events
    internal_conflict = Event.objects.filter(
        date=date_str,
        status__in=[EventStatus.CONFIRMED, EventStatus.INQUIRY]
    ).exists()

    if internal_conflict:
        return JsonResponse({'available': False, 'reason': 'An diesem Datum ist bereits ein Event geplant.'})

    try:
        available, events = GoogleService.check_date_available(date_str)
        if available:
            return JsonResponse({'available': True})
        return JsonResponse({'available': False, 'reason': 'An diesem Datum ist bereits ein Termin im Kalender.'})
    except Exception:
        # If Google not connected, just check internal
        return JsonResponse({'available': not internal_conflict})


@require_POST
def submit_event_form(request):
    """Handle public event booking form submission."""
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Ungültige Anfrage'}, status=400)

    required = ['name', 'date', 'location', 'client_name', 'client_email']
    for field in required:
        if not data.get(field, '').strip():
            return JsonResponse({'success': False, 'error': f'Feld "{field}" ist erforderlich'}, status=400)

    config = AppConfig.load()

    # Parse date/time strings to proper objects
    from datetime import datetime
    try:
        parsed_date = datetime.strptime(data['date'], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Ungültiges Datum'}, status=400)

    parsed_time_start = None
    if data.get('time_start'):
        try:
            parsed_time_start = datetime.strptime(data['time_start'], '%H:%M').time()
        except ValueError:
            pass

    parsed_time_end = None
    if data.get('time_end'):
        try:
            parsed_time_end = datetime.strptime(data['time_end'], '%H:%M').time()
        except ValueError:
            pass

    event = Event.objects.create(
        name=data['name'],
        date=parsed_date,
        time_start=parsed_time_start,
        time_end=parsed_time_end,
        location=data['location'],
        address_street=data.get('address_street', ''),
        address_zip=data.get('address_zip', ''),
        address_city=data.get('address_city', ''),
        event_type=data.get('event_type', ''),
        description=data.get('description', ''),
        client_name=data['client_name'],
        client_email=data['client_email'],
        client_phone=data.get('client_phone', ''),
        client_company=data.get('client_company', ''),
        guest_count=int(data['guest_count']) if data.get('guest_count') else None,
        distance_km=data.get('distance_km') if data.get('distance_km') is not None else None,
        special_requests=data.get('special_requests', ''),
        status=EventStatus.INQUIRY,
        max_wishes_per_session=config.default_max_wishes,
        wishlist_show_cover=config.default_show_cover,
        wishlist_show_artist=config.default_show_artist,
        wishlist_show_preview=config.default_show_preview,
        wishlist_show_duration=config.default_show_duration,
        wishlist_require_name=config.default_require_name,
    )

    # Save price calculation with PriceEngine
    from .models import PricingWorkflow
    selected_item_ids = data.get('selected_items', [])
    package_id = data.get('package_id')
    package = None
    if package_id:
        package = PricingPackage.objects.filter(pk=package_id, is_active=True, is_public=True).first()

    default_wf = PricingWorkflow.objects.filter(is_default=True, is_active=True).first()
    calc = EventPriceCalculation.objects.create(event=event, package=package, workflow=default_wf)
    if selected_item_ids:
        calc.selected_items.set(PriceItem.objects.filter(pk__in=selected_item_ids, is_active=True))

    try:
        calc_kwargs = dict(
            event=event,
            package_id=package.pk if package else None,
            selected_item_ids=selected_item_ids,
        )
        if default_wf:
            result = PriceEngine.calculate_workflow(workflow_id=default_wf.pk, **calc_kwargs)
        else:
            result = PriceEngine.calculate(**calc_kwargs)
        calc.calculation_snapshot = result
        active_rule_ids = [r['rule_id'] for r in result.get('rules_applied', []) if r.get('rule_id')]
        if active_rule_ids:
            calc.applied_rules.set(PricingRule.objects.filter(pk__in=active_rule_ids))
        calc.save()
        event.total_price = result.get('grand_total', calc.total)
    except Exception:
        logger.warning("PriceEngine calculation failed for event %s, using legacy total", event.pk)
        event.total_price = calc.total
    event.save(update_fields=['total_price'])

    # Backup to Google Sheets
    try:
        GoogleService.backup_event_to_sheets(event)
    except Exception:
        logger.warning("Sheets backup failed for event %s", event.pk)

    # Send inquiry confirmation email to customer
    if event.client_email:
        try:
            inquiry_subject = config.inquiry_email_subject
            inquiry_body = config.inquiry_email_body.format(
                client_name=event.client_name or "Kunde",
                event_name=event.name,
                event_date=event.date if isinstance(event.date, str) else event.date.strftime('%d.%m.%Y'),
                location=event.location,
                dj_name=config.dj_name,
            )
            success, msg = GoogleService.send_email(
                event.client_email, inquiry_subject, inquiry_body, sender_name=config.dj_name)
            EmailLog.objects.create(
                event=event, recipient=event.client_email,
                subject=inquiry_subject, body=inquiry_body,
                success=success, error_message="" if success else msg)
        except Exception:
            logger.warning("Inquiry email to customer failed")

    # Send notification email to DJ
    if config.dj_email:
        admin_link = request.build_absolute_uri(f'/dj-admin/events/{event.pk}/edit/?token={event.admin_token}')
        body = (
            f"Neue Event-Anfrage!\n\n"
            f"Event: {event.name}\n"
            f"Datum: {event.date.strftime('%d.%m.%Y')}\n"
            f"Location: {event.location}\n"
            f"Kunde: {event.client_name} ({event.client_email})\n"
            f"Telefon: {event.client_phone}\n"
            f"Typ: {event.event_type}\n"
            f"Gäste: {event.guest_count or 'k.A.'}\n"
            f"Preis: {event.total_price or 'k.A.'} €\n\n"
            f"Zum Event im Admin:\n{admin_link}\n"
        )
        try:
            success, msg = GoogleService.send_email(
                config.dj_email,
                f"Neue Anfrage: {event.name} am {event.date.strftime('%d.%m.%Y')}",
                body,
                sender_name=config.dj_name
            )
            EmailLog.objects.create(
                event=event, recipient=config.dj_email,
                subject=f"Neue Anfrage: {event.name}",
                body=body, success=success,
                error_message="" if success else msg
            )
        except Exception:
            logger.warning("DJ notification email failed")

    return JsonResponse({'success': True, 'event_id': event.pk})


# ── Spotify OAuth ────────────────────────────────────────────────────────────

@staff_member_required
def spotify_login(request):
    auth_url = SpotifyService.get_auth_url()
    return redirect(auth_url)


def spotify_callback(request):
    code = request.GET.get('code')
    error = request.GET.get('error')
    if error or not code:
        return render(request, 'wishlist/spotify_error.html',
                      {'error': error or 'Kein Autorisierungs-Code erhalten'})
    data = SpotifyService.exchange_code(code)
    if 'access_token' not in data:
        return render(request, 'wishlist/spotify_error.html',
                      {'error': f"Token-Austausch fehlgeschlagen: {data.get('error', 'Unbekannt')}"})
    expires_at = timezone.now() + timedelta(seconds=data.get('expires_in', 3600) - 120)
    SpotifyToken.objects.all().delete()
    SpotifyToken.objects.create(
        access_token=data['access_token'],
        refresh_token=data.get('refresh_token', ''),
        expires_at=expires_at,
    )
    return render(request, 'wishlist/spotify_success.html')


@staff_member_required
def spotify_status(request):
    token = SpotifyToken.objects.order_by('-updated_at').first()
    connected = token is not None and not token.is_expired
    expires_at = token.expires_at.isoformat() if (token and token.expires_at) else None
    return JsonResponse({'connected': connected, 'expires_at': expires_at})


# ── Google OAuth ─────────────────────────────────────────────────────────────

@staff_member_required
def google_login(request):
    auth_url = GoogleService.get_auth_url()
    return redirect(auth_url)


def google_callback(request):
    from .models import GoogleToken
    code = request.GET.get('code')
    error = request.GET.get('error')
    if error or not code:
        return render(request, 'wishlist/google_error.html',
                      {'error': error or 'Kein Autorisierungs-Code erhalten'})
    data = GoogleService.exchange_code(code)
    if 'access_token' not in data:
        return render(request, 'wishlist/google_error.html',
                      {'error': f"Token-Austausch fehlgeschlagen: {data.get('error', 'Unbekannt')}"})
    expires_at = timezone.now() + timedelta(seconds=data.get('expires_in', 3600) - 120)
    GoogleToken.objects.all().delete()
    GoogleToken.objects.create(
        access_token=data['access_token'],
        refresh_token=data.get('refresh_token', ''),
        expires_at=expires_at,
        scopes=data.get('scope', ''),
    )
    return redirect('/dj-admin/config/')


@staff_member_required
def google_status(request):
    from .models import GoogleToken
    token = GoogleToken.objects.order_by('-updated_at').first()
    connected = token is not None and not token.is_expired
    expires_at = token.expires_at.isoformat() if (token and token.expires_at) else None
    return JsonResponse({'connected': connected, 'expires_at': expires_at})


# ── Spotify: Now Playing ─────────────────────────────────────────────────────

@require_GET
def api_now_playing(request):
    """Public endpoint — returns currently playing track or null."""
    try:
        playback = SpotifyService.get_current_playback()
        return JsonResponse({'playing': playback})
    except Exception:
        logger.exception("api_now_playing error")
        return JsonResponse({'playing': None})


# ── Spotify: Artist Top Tracks ───────────────────────────────────────────────

@require_GET
def artist_top_tracks(request):
    """Public endpoint — returns top tracks for an artist."""
    artist_id = request.GET.get('artist_id', '').strip()
    if not artist_id:
        return JsonResponse({'tracks': [], 'error': 'artist_id fehlt'}, status=400)
    try:
        tracks = SpotifyService.get_artist_top_tracks(artist_id)
        return JsonResponse({'tracks': tracks})
    except Exception:
        logger.exception("artist_top_tracks error")
        return JsonResponse({'tracks': [], 'error': 'Fehler beim Laden'})


# ── Public Price Estimate ──────────────────────────────────────────────────

@require_POST
def api_price_estimate(request):
    import json as _json
    from .price_engine import PriceEngine
    from .models import Event, PricingPackage, PriceItem
    from decimal import Decimal, InvalidOperation

    data = _json.loads(request.body)
    event = Event()
    event.date = datetime.strptime(data['date'], '%Y-%m-%d').date() if data.get('date') else None
    event.guest_count = int(data['guest_count']) if data.get('guest_count') else None
    event.event_type = data.get('event_type', '')
    ts = data.get('time_start')
    event.time_start = datetime.strptime(ts, '%H:%M').time() if ts else None
    te = data.get('time_end')
    event.time_end = datetime.strptime(te, '%H:%M').time() if te else None
    try:
        event.distance_km = Decimal(str(data['distance_km'])) if data.get('distance_km') is not None else None
    except (InvalidOperation, ValueError):
        event.distance_km = None

    pkg_id = data.get('package_id')
    if pkg_id:
        if not PricingPackage.objects.filter(pk=pkg_id, is_active=True, is_public=True).exists():
            pkg_id = None

    item_ids = data.get('selected_item_ids', [])
    if item_ids:
        item_ids = list(PriceItem.objects.filter(pk__in=item_ids, is_active=True, is_public=True).values_list('pk', flat=True))

    from .models import PricingWorkflow
    calc_kwargs = dict(event=event, package_id=pkg_id, selected_item_ids=item_ids)
    default_wf = PricingWorkflow.objects.filter(is_default=True, is_active=True).first()
    if default_wf:
        result = PriceEngine.calculate_workflow(workflow_id=default_wf.pk, **calc_kwargs)
    else:
        result = PriceEngine.calculate(**calc_kwargs)

    def to_json(v):
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, dict):
            return {k: to_json(val) for k, val in v.items()}
        if isinstance(v, list):
            return [to_json(i) for i in v]
        return v

    return JsonResponse(to_json(result))
