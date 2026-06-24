import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone

from .models import (
    Event, SongWish, AppConfig, PriceItem, EventPriceCalculation,
    EmailLog, EventStatus, GoogleToken, SpotifyToken, BlockedClient,
    EventOffer, PricingRule, PricingPackage, PricingFormula, PricingWorkflow,
)
from .google_services import GoogleService

logger = logging.getLogger(__name__)


# ── Dashboard ────────────────────────────────────────────────────────────────

@staff_member_required
def admin_dashboard(request):
    today = timezone.now().date()
    events_confirmed = Event.objects.filter(status=EventStatus.CONFIRMED, date__gte=today).order_by('date')
    events_inquiry = Event.objects.filter(status=EventStatus.INQUIRY).order_by('date')
    events_past = Event.objects.filter(status=EventStatus.PAST).order_by('-date')[:20]
    active_event = Event.objects.filter(is_active=True).first()
    google_token = GoogleToken.objects.order_by('-updated_at').first()
    google_connected = google_token and not google_token.is_expired
    spotify_token = SpotifyToken.objects.order_by('-updated_at').first()
    spotify_connected = spotify_token and not spotify_token.is_expired

    return render(request, 'dj_admin/dashboard.html', {
        'events_confirmed': events_confirmed,
        'events_inquiry': events_inquiry,
        'events_past': events_past,
        'active_event': active_event,
        'google_connected': google_connected,
        'spotify_connected': spotify_connected,
    })


# ── Wishlist Live View (eigene Seite) ────────────────────────────────────────

@staff_member_required
def admin_wishlist_view(request):
    """Dedicated wishlist admin page with live updates and blocking."""
    active_event = Event.objects.filter(is_active=True).first()
    events = Event.objects.filter(status__in=[EventStatus.CONFIRMED, EventStatus.INQUIRY]).order_by('-date')
    blocked = BlockedClient.objects.filter(is_active=True)
    return render(request, 'dj_admin/wishlist_live.html', {
        'active_event': active_event,
        'events': events,
        'blocked_clients': blocked,
    })


# ── Event CRUD ───────────────────────────────────────────────────────────────

@staff_member_required
def event_create(request):
    config = AppConfig.load()
    price_items = PriceItem.objects.filter(is_active=True)
    if request.method == 'POST':
        event = _save_event_from_post(request, config)
        return redirect('dj_admin:event_edit', pk=event.pk)
    default_wf = PricingWorkflow.objects.filter(is_default=True, is_active=True).first()
    return render(request, 'dj_admin/event_form.html', {
        'config': config, 'price_items': price_items, 'event': None,
        'pricing_packages': PricingPackage.objects.filter(is_active=True).order_by('sort_order'),
        'pricing_formulas': PricingFormula.objects.filter(is_active=True),
        'pricing_workflows': PricingWorkflow.objects.filter(is_active=True).order_by('-is_default', 'name'),
        'selected_workflow_id': default_wf.pk if default_wf else None,
    })


@staff_member_required
def event_edit(request, pk):
    event = get_object_or_404(Event, pk=pk)
    config = AppConfig.load()
    price_items = PriceItem.objects.filter(is_active=True)
    if request.method == 'POST':
        _save_event_from_post(request, config, event=event)
        return redirect('dj_admin:event_edit', pk=event.pk)
    wishes_pending = event.wishes.filter(played=False).order_by('-created_at')
    wishes_played = event.wishes.filter(played=True).order_by('-created_at')
    offer = getattr(event, 'offer', None)
    calc = getattr(event, 'price_calculation', None)
    return render(request, 'dj_admin/event_form.html', {
        'event': event, 'config': config, 'price_items': price_items,
        'wishes_pending': wishes_pending, 'wishes_played': wishes_played,
        'calculation': calc,
        'offer': offer,
        'offer_breakdown': offer.get_detailed_breakdown() if offer else None,
        'pricing_packages': PricingPackage.objects.filter(is_active=True).order_by('sort_order'),
        'pricing_formulas': PricingFormula.objects.filter(is_active=True),
        'pricing_workflows': PricingWorkflow.objects.filter(is_active=True).order_by('-is_default', 'name'),
        'selected_package_id': calc.package_id if calc else None,
        'selected_formula_id': calc.formula_id if calc else None,
        'selected_workflow_id': calc.workflow_id if calc else None,
    })


def _save_event_from_post(request, config, event=None):
    is_new = event is None
    if is_new:
        event = Event()

    event.name = request.POST.get('name', '')
    date_str = request.POST.get('date', '')
    event.date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else None
    ts = request.POST.get('time_start') or None
    event.time_start = datetime.strptime(ts, '%H:%M').time() if ts else None
    te = request.POST.get('time_end') or None
    event.time_end = datetime.strptime(te, '%H:%M').time() if te else None
    event.location = request.POST.get('location', '')
    event.address_street = request.POST.get('address_street', '')
    event.address_zip = request.POST.get('address_zip', '')
    event.address_city = request.POST.get('address_city', '')
    event.event_type = request.POST.get('event_type', '')
    event.description = request.POST.get('description', '')
    event.client_name = request.POST.get('client_name', '')
    event.client_email = request.POST.get('client_email', '')
    event.client_phone = request.POST.get('client_phone', '')
    event.client_company = request.POST.get('client_company', '')
    event.guest_count = int(request.POST['guest_count']) if request.POST.get('guest_count') else None
    event.special_requests = request.POST.get('special_requests', '')
    event.cover_image_url = request.POST.get('cover_image_url', '')
    # Spotify URL → ID (auto-parsed in model.save())
    event.spotify_playlist_id = request.POST.get('spotify_playlist_id', '')
    event.spotify_preselection_playlist_id = request.POST.get('spotify_preselection_playlist_id', '') or event.spotify_preselection_playlist_id
    event.status = request.POST.get('status', event.status)
    event.is_active = request.POST.get('is_active') == 'on'
    event.max_wishes_per_session = max(1, int(request.POST.get('max_wishes_per_session', 3) or 3))
    event.wishlist_show_cover = request.POST.get('wishlist_show_cover') == 'on'
    event.wishlist_show_artist = request.POST.get('wishlist_show_artist') == 'on'
    event.wishlist_show_preview = request.POST.get('wishlist_show_preview') == 'on'
    event.wishlist_show_duration = request.POST.get('wishlist_show_duration') == 'on'
    event.wishlist_require_name = request.POST.get('wishlist_require_name') == 'on'
    event.wishlist_show_played = request.POST.get('wishlist_show_played') == 'on'
    event.spotify_remove_on_played = request.POST.get('spotify_remove_on_played') == 'on'
    event.wishlist_custom_message = request.POST.get('wishlist_custom_message', '')
    event.wishlist_dj_message = request.POST.get('wishlist_dj_message', '')
    event.default_block_reason = request.POST.get('default_block_reason', '')
    event.default_block_message = request.POST.get('default_block_message', '')
    event.total_price = request.POST.get('total_price') or None
    event.price_notes = request.POST.get('price_notes', '')
    event.distance_km = request.POST.get('distance_km') or None

    # File upload for cover image
    if 'cover_image' in request.FILES:
        event.cover_image = request.FILES['cover_image']

    event.save()

    # Auto-create Spotify playlists for new events
    if is_new:
        from .spotify import SpotifyService
        date_str = event.date.strftime('%d.%m.%Y') if event.date else ''
        try:
            if not event.spotify_playlist_id:
                pid, url = SpotifyService.create_playlist(
                    f"🎵 Wishlist — {event.name} ({date_str})",
                    description=f"Wunschliste für {event.name}"
                )
                if pid:
                    event.spotify_playlist_id = pid
                    logger.info("Created Wishlist playlist %s for event %s", pid, event.pk)
            if not event.spotify_preselection_playlist_id:
                pid2, url2 = SpotifyService.create_playlist(
                    f"🎧 Vorauswahl — {event.name} ({date_str})",
                    description=f"DJ Vorauswahl für {event.name}"
                )
                if pid2:
                    event.spotify_preselection_playlist_id = pid2
                    logger.info("Created Vorauswahl playlist %s for event %s", pid2, event.pk)
            if event.spotify_playlist_id or event.spotify_preselection_playlist_id:
                event.save(update_fields=['spotify_playlist_id', 'spotify_preselection_playlist_id'])
        except Exception as e:
            logger.warning("Spotify playlist creation failed for event %s: %s", event.pk, e)

    try:
        GoogleService.backup_event_to_sheets(event)
    except Exception:
        logger.warning("Sheets backup failed for event %s", event.pk)

    # EventOffer (Preis-Kalkulator)
    if request.POST.get('offer_threshold'):
        from decimal import Decimal, InvalidOperation
        offer, _ = EventOffer.objects.get_or_create(event=event)
        offer.guest_count = int(request.POST.get('offer_guest_count') or 0)
        offer.threshold = int(request.POST.get('offer_threshold') or 50)
        for f in ('rate_1', 'rate_2', 'base_rent', 'tech_package', 'cleaning_fee'):
            val = request.POST.get(f'offer_{f}', '0')
            try:
                setattr(offer, f, Decimal(val or '0'))
            except InvalidOperation:
                setattr(offer, f, Decimal('0'))
        offer.tech_required = request.POST.get('offer_tech_required') == 'on'
        offer.full_clean()
        offer.save()

    # Preis-Kalkulation in EventPriceCalculation speichern
    pkg_id = request.POST.get('pricing_package_id') or None
    formula_id = request.POST.get('pricing_formula_id') or None
    workflow_id = request.POST.get('pricing_workflow_id') or None
    item_ids = request.POST.getlist('selected_items')
    discount_raw = request.POST.get('pricing_discount_percent')
    has_pricing_data = (
        pkg_id or formula_id or workflow_id or item_ids
        or discount_raw not in (None, '')
    )
    existing_calc = EventPriceCalculation.objects.filter(event=event).exists()
    if has_pricing_data or existing_calc:
        from decimal import Decimal, InvalidOperation
        calc, _ = EventPriceCalculation.objects.get_or_create(event=event)
        calc.package_id = int(pkg_id) if pkg_id else None
        calc.formula_id = int(formula_id) if formula_id else None
        calc.workflow_id = int(workflow_id) if workflow_id else None
        try:
            calc.discount_percent = Decimal(discount_raw) if discount_raw else Decimal('0')
        except InvalidOperation:
            calc.discount_percent = Decimal('0')
        calc.save()
        valid_ids = list(
            PriceItem.objects.filter(pk__in=item_ids).values_list('pk', flat=True)
        )
        calc.selected_items.set(valid_ids)

    return event


@staff_member_required
def event_delete(request, pk):
    event = get_object_or_404(Event, pk=pk)
    if request.method == 'POST':
        if event.google_calendar_event_id:
            GoogleService.delete_calendar_event(event.google_calendar_event_id)
        event.delete()
        return redirect('dj_admin:dashboard')
    return render(request, 'dj_admin/event_confirm_delete.html', {'event': event})


@staff_member_required
@require_POST
def event_confirm(request, pk):
    event = get_object_or_404(Event, pk=pk)
    config = AppConfig.load()
    event.status = EventStatus.CONFIRMED
    event.save()
    errors = []
    if not event.google_calendar_event_id:
        cal_id, err = GoogleService.create_calendar_event(event)
        if cal_id:
            event.google_calendar_event_id = cal_id
            event.save(update_fields=['google_calendar_event_id'])
        elif err:
            errors.append(f"Kalender: {err}")
    if event.client_email:
        subject = config.confirmation_email_subject
        body = config.confirmation_email_body.format(
            client_name=event.client_name or "Kunde", event_name=event.name,
            event_date=event.date.strftime('%d.%m.%Y'),
            event_time=str(event.time_start or "nach Absprache"),
            location=event.location, dj_name=config.dj_name,
        )
        success, msg = GoogleService.send_email(event.client_email, subject, body, sender_name=config.dj_name)
        EmailLog.objects.create(event=event, recipient=event.client_email,
            subject=subject, body=body, success=success, error_message="" if success else msg)
        if not success:
            errors.append(f"E-Mail: {msg}")
    try:
        GoogleService.backup_event_to_sheets(event)
    except Exception:
        pass
    return JsonResponse({'success': True, 'warnings': errors} if errors else {'success': True})


@staff_member_required
@require_POST
def event_set_active(request, pk):
    """Set an event as the active wishlist event."""
    event = get_object_or_404(Event, pk=pk)
    # Deactivate all others, activate this one
    Event.objects.filter(is_active=True).update(is_active=False)
    event.is_active = True
    event.save(update_fields=['is_active'])
    return JsonResponse({'success': True, 'event_name': event.name})


# ── Wishlist Admin (Turbo Frame) ─────────────────────────────────────────────

@staff_member_required
def admin_wishlist_frame(request, pk):
    event = get_object_or_404(Event, pk=pk)
    wishes_pending = event.wishes.filter(played=False).order_by('-created_at')
    wishes_played = event.wishes.filter(played=True).order_by('-created_at')
    blocked = BlockedClient.objects.filter(is_active=True, event=event)
    return render(request, 'dj_admin/_wishlist_frame.html', {
        'event': event, 'wishes_pending': wishes_pending,
        'wishes_played': wishes_played, 'blocked_clients': blocked,
    })


@staff_member_required
@require_POST
def toggle_wish_played(request, wish_id):
    from .spotify import SpotifyService
    wish = get_object_or_404(SongWish, pk=wish_id)
    wish.played = not wish.played
    wish.save(update_fields=['played'])

    spotify_result = None
    event = wish.event
    if event.spotify_playlist_id and event.spotify_remove_on_played:
        try:
            if wish.played:
                success, msg = SpotifyService.remove_from_playlist(event.spotify_playlist_id, wish.spotify_track_id)
                logger.info("Spotify remove %s from %s: %s (%s)", wish.spotify_track_id, event.spotify_playlist_id, success, msg)
                spotify_result = {'action': 'remove', 'success': success, 'message': msg}
            else:
                success, msg = SpotifyService.add_to_playlist(event.spotify_playlist_id, wish.spotify_track_id)
                logger.info("Spotify re-add %s to %s: %s (%s)", wish.spotify_track_id, event.spotify_playlist_id, success, msg)
                spotify_result = {'action': 'add', 'success': success, 'message': msg}
                if success:
                    wish.added_to_playlist = True
                    wish.save(update_fields=['added_to_playlist'])
        except Exception as e:
            logger.error("Spotify sync error for wish %s: %s", wish_id, e)
            spotify_result = {'action': 'error', 'success': False, 'message': str(e)}
    elif not event.spotify_playlist_id:
        logger.info("No spotify_playlist_id on event %s, skipping sync", event.pk)
    else:
        logger.info("spotify_remove_on_played disabled for event %s, skipping sync", event.pk)

    return JsonResponse({
        'success': True, 'played': wish.played,
        'spotify': spotify_result,
    })


@staff_member_required
@require_POST
def delete_wish(request, wish_id):
    wish = get_object_or_404(SongWish, pk=wish_id)
    wish.delete()
    return JsonResponse({'success': True})


# ── Config Page ──────────────────────────────────────────────────────────────

@staff_member_required
def config_page(request):
    config = AppConfig.load()
    email_logs = EmailLog.objects.all()[:20]
    google_token = GoogleToken.objects.order_by('-updated_at').first()
    spotify_token = SpotifyToken.objects.order_by('-updated_at').first()

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'save_config':
            for field in [
                'spotify_client_id', 'spotify_client_secret', 'spotify_redirect_uri',
                'google_client_id', 'google_client_secret', 'google_redirect_uri',
                'google_sheets_id',
                'dj_name', 'dj_real_name', 'dj_email', 'dj_phone', 'dj_address', 'dj_website',
                'confirmation_email_subject', 'confirmation_email_body',
                'inquiry_email_subject', 'inquiry_email_body',
                'event_form_intro', 'event_form_date_prompt', 'event_form_unavailable_msg',
                'default_playlist_id', 'wishlist_limit_message',
                'default_block_reason', 'default_block_message',
                'no_event_message',
            ]:
                setattr(config, field, request.POST.get(field, ''))
            config.default_max_wishes = int(request.POST.get('default_max_wishes', 3))
            config.default_show_cover = request.POST.get('default_show_cover') == 'on'
            config.default_show_artist = request.POST.get('default_show_artist') == 'on'
            config.default_show_preview = request.POST.get('default_show_preview') == 'on'
            config.default_show_duration = request.POST.get('default_show_duration') == 'on'
            config.default_require_name = request.POST.get('default_require_name') == 'on'
            config.event_form_enabled = request.POST.get('event_form_enabled') == 'on'
            from decimal import Decimal, InvalidOperation
            for coord_field in ['dj_home_lat', 'dj_home_lon']:
                raw = request.POST.get(coord_field, '').strip().replace(',', '.')
                try:
                    setattr(config, coord_field, Decimal(raw) if raw else None)
                except InvalidOperation:
                    setattr(config, coord_field, None)
            config.save()
            return redirect('dj_admin:config')

    return render(request, 'dj_admin/config.html', {
        'config': config, 'email_logs': email_logs,
        'google_token': google_token, 'spotify_token': spotify_token,
        'google_connected': google_token and not google_token.is_expired,
        'spotify_connected': spotify_token and not spotify_token.is_expired,
    })


# ── Calendar ─────────────────────────────────────────────────────────────────

@staff_member_required
def calendar_view(request):
    return render(request, 'dj_admin/calendar.html')

@staff_member_required
@require_GET
def calendar_events_api(request):
    events = []
    for ev in Event.objects.filter(status__in=[EventStatus.CONFIRMED, EventStatus.INQUIRY]):
        events.append({
            'id': f'internal-{ev.pk}',
            'title': f"{'🎧' if ev.status == EventStatus.CONFIRMED else '❓'} {ev.name}",
            'start': ev.date.isoformat(),
            'color': '#1db954' if ev.status == EventStatus.CONFIRMED else '#a855f7',
            'url': f'/dj-admin/events/{ev.pk}/edit/',
            'source': 'internal',
        })
    try:
        for gev in GoogleService.get_calendar_events():
            start = gev.get('start', {}).get('dateTime') or gev.get('start', {}).get('date', '')
            events.append({
                'id': gev.get('id', ''), 'title': gev.get('summary', 'Ohne Titel'),
                'start': start, 'color': '#3b82f6', 'source': 'google',
            })
    except Exception:
        pass
    return JsonResponse(events, safe=False)


# ── Client Blocking ─────────────────────────────────────────────────────────

@staff_member_required
def blocked_clients_frame(request):
    """Turbo Frame: list of blocked clients."""
    blocks = BlockedClient.objects.filter(is_active=True)
    return render(request, 'dj_admin/_blocked_frame.html', {'blocks': blocks})

@staff_member_required
@require_POST
def block_client(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = request.POST
    session_key = data.get('session_key', '')
    ip_address = data.get('ip_address', '')
    reason = data.get('reason', 'Vom Admin blockiert')
    public_message = data.get('public_message', 'Du wurdest für diese Wishlist gesperrt.')
    event_id = data.get('event_id')
    hours = int(data.get('hours', 0))
    if not session_key and not ip_address:
        return JsonResponse({'success': False, 'error': 'Session oder IP benötigt'}, status=400)
    event = Event.objects.filter(pk=event_id).first() if event_id else None
    expires_at = timezone.now() + timedelta(hours=hours) if hours > 0 else None

    existing = BlockedClient.objects.filter(
        session_key=session_key or '', ip_address=ip_address or None, event=event
    ).first()

    if existing:
        existing.is_active = True
        existing.reason = reason
        existing.public_message = public_message
        existing.expires_at = expires_at
        existing.save()
        BlockedClient.objects.filter(
            session_key=session_key or '', ip_address=ip_address or None, event=event
        ).exclude(pk=existing.pk).delete()
        block_id = existing.pk
    else:
        block = BlockedClient.objects.create(
            session_key=session_key or '', ip_address=ip_address or None, event=event,
            reason=reason, public_message=public_message,
            expires_at=expires_at, is_active=True)
        block_id = block.pk
    return JsonResponse({'success': True, 'block_id': block_id})

@staff_member_required
@require_POST
def unblock_client(request, block_id):
    """Unblock: deactivate this block AND all other active blocks with the same IP/session."""
    block = get_object_or_404(BlockedClient, pk=block_id)

    # Deactivate ALL active blocks with matching IP or session_key (to clean up duplicates)
    from django.db.models import Q
    q = Q(is_active=True)
    match_q = Q()
    if block.ip_address:
        match_q |= Q(ip_address=block.ip_address)
    if block.session_key:
        match_q |= Q(session_key=block.session_key)
    if match_q:
        BlockedClient.objects.filter(q & match_q).update(is_active=False)
    else:
        block.is_active = False
        block.save(update_fields=['is_active'])
    return JsonResponse({'success': True})

@staff_member_required
@require_POST
def block_wish_client(request, wish_id):
    wish = get_object_or_404(SongWish, pk=wish_id)
    reason = ''
    public_message = ''
    hours = 0
    try:
        body = json.loads(request.body)
        reason = body.get('reason', '')
        public_message = body.get('public_message', '')
        hours = int(body.get('hours', 0))
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback to event defaults, then app config defaults
    config = AppConfig.load()
    if not reason:
        reason = getattr(wish.event, 'default_block_reason', '') or getattr(config, 'default_block_reason', '') or 'Unangemessene Eingaben'
    if not public_message:
        public_message = getattr(wish.event, 'default_block_message', '') or getattr(config, 'default_block_message', '') or 'Du wurdest für diese Wishlist gesperrt.'

    expires_at = timezone.now() + timedelta(hours=hours) if hours > 0 else None

    # Find existing block (handles duplicates gracefully)
    existing = BlockedClient.objects.filter(
        session_key=wish.session_key or '',
        ip_address=wish.ip_address,
        event=wish.event,
    ).first()

    if existing:
        existing.is_active = True
        existing.reason = f"{reason} (Wunsch: {wish.track_name})"
        existing.public_message = public_message
        existing.expires_at = expires_at
        existing.save()
        # Clean up duplicates
        BlockedClient.objects.filter(
            session_key=wish.session_key or '',
            ip_address=wish.ip_address,
            event=wish.event,
        ).exclude(pk=existing.pk).delete()
    else:
        BlockedClient.objects.create(
            session_key=wish.session_key or '',
            ip_address=wish.ip_address,
            event=wish.event,
            reason=f"{reason} (Wunsch: {wish.track_name})",
            public_message=public_message,
            expires_at=expires_at,
            is_active=True,
        )
    return JsonResponse({'success': True})


# ── Spotify: Devices ─────────────────────────────────────────────────────────

@staff_member_required
@require_GET
def api_devices(request):
    """Returns available Spotify playback devices."""
    from .spotify import SpotifyService
    try:
        devices = SpotifyService.get_devices()
        return JsonResponse({'devices': devices})
    except Exception:
        logger.exception("api_devices error")
        return JsonResponse({'devices': []})


# ── Spotify: Audio Features ──────────────────────────────────────────────────

@staff_member_required
@require_GET
def api_audio_features(request, wish_id):
    """Returns audio features for a wish — from cache or fetched from Spotify."""
    from .spotify import SpotifyService
    wish = get_object_or_404(SongWish, pk=wish_id)

    if wish.audio_bpm is not None:
        return JsonResponse({
            'wish_id': wish.id,
            'track_name': wish.track_name,
            'artist_name': wish.artist_name,
            'bpm': wish.audio_bpm,
            'energy': wish.audio_energy,
            'danceability': wish.audio_danceability,
            'valence': wish.audio_valence,
            'acousticness': wish.audio_acousticness,
            'instrumentalness': wish.audio_instrumentalness,
        })

    features = SpotifyService.get_audio_features(wish.spotify_track_id)
    if features:
        wish.audio_bpm = features.get('bpm')
        wish.audio_energy = features.get('energy')
        wish.audio_danceability = features.get('danceability')
        wish.audio_valence = features.get('valence')
        wish.audio_acousticness = features.get('acousticness')
        wish.audio_instrumentalness = features.get('instrumentalness')
        wish.save(update_fields=['audio_bpm', 'audio_energy', 'audio_danceability',
                                  'audio_valence', 'audio_acousticness', 'audio_instrumentalness'])
        return JsonResponse({
            'wish_id': wish.id,
            'track_name': wish.track_name,
            'artist_name': wish.artist_name,
            'bpm': wish.audio_bpm,
            'energy': wish.audio_energy,
            'danceability': wish.audio_danceability,
            'valence': wish.audio_valence,
            'acousticness': wish.audio_acousticness,
            'instrumentalness': wish.audio_instrumentalness,
        })

    return JsonResponse({'error': 'Audio Features nicht verfügbar'}, status=404)


# ── Pricing API ─────────────────────────────────────────────────────────────

@staff_member_required
@require_POST
def api_price_calculate(request):
    from .price_engine import PriceEngine
    data = json.loads(request.body)
    event_id = data.get('event_id')
    event = get_object_or_404(Event, pk=event_id) if event_id else Event()

    workflow_id = data.get('workflow_id')
    if not workflow_id:
        default_wf = PricingWorkflow.objects.filter(is_default=True, is_active=True).first()
        if default_wf:
            workflow_id = default_wf.pk

    ALLOWED_CONTEXT_KEYS = {'guest_count', 'duration_hours', 'distance_km',
                            'date_weekday', 'date_month', 'event_type'}
    test_context = data.get('test_context') or None
    if test_context:
        test_context = {k: v for k, v in test_context.items() if k in ALLOWED_CONTEXT_KEYS}

    kwargs = dict(
        event=event,
        package_id=data.get('package_id'),
        selected_item_ids=data.get('selected_item_ids', []),
        formula_id=data.get('formula_id'),
        custom_items=data.get('custom_items', []),
        discount_percent=data.get('discount_percent', 0),
        context_override=test_context,
    )

    if workflow_id:
        result = PriceEngine.calculate_workflow(workflow_id=workflow_id, **kwargs)
    else:
        result = PriceEngine.calculate(**kwargs)

    def to_json(v):
        from decimal import Decimal
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, dict):
            return {k: to_json(val) for k, val in v.items()}
        if isinstance(v, list):
            return [to_json(i) for i in v]
        return v
    return JsonResponse(to_json(result))


@staff_member_required
def api_price_items(request):
    if request.method == 'GET':
        items = PriceItem.objects.all().order_by('sort_order')
        return JsonResponse({'items': [
            {'id': i.pk, 'name': i.name, 'category': i.category, 'price': float(i.price),
             'is_default': i.is_default, 'is_required': i.is_required, 'is_public': i.is_public}
            for i in items
        ]})
    if request.method == 'POST':
        data = json.loads(request.body)
        item = PriceItem.objects.create(
            name=data['name'],
            category=data.get('category', 'extra'),
            description=data.get('description', ''),
            price=data.get('price', 0),
            is_default=data.get('is_default', False),
            is_required=data.get('is_required', False),
            is_public=data.get('is_public', True),
            sort_order=data.get('sort_order', 0),
        )
        return JsonResponse({'success': True, 'id': item.pk})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def api_price_item_detail(request, pk):
    item = get_object_or_404(PriceItem, pk=pk)
    if request.method == 'GET':
        return JsonResponse({
            'id': item.pk, 'name': item.name, 'category': item.category,
            'description': item.description, 'price': float(item.price),
            'is_default': item.is_default, 'is_required': item.is_required,
            'is_public': item.is_public, 'sort_order': item.sort_order,
        })
    if request.method == 'PUT':
        data = json.loads(request.body)
        if 'name' in data:
            item.name = data['name']
        if 'category' in data:
            item.category = data['category']
        if 'description' in data:
            item.description = data.get('description', '')
        if 'price' in data:
            item.price = Decimal(str(data['price']))
        if 'is_default' in data:
            item.is_default = data['is_default']
        if 'is_required' in data:
            item.is_required = data['is_required']
        if 'is_public' in data:
            item.is_public = data.get('is_public', True)
        if 'sort_order' in data:
            item.sort_order = data.get('sort_order', 0)
        item.save()
        return JsonResponse({'success': True, 'id': item.pk})
    if request.method == 'DELETE':
        item.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def api_pricing_rules(request):
    if request.method == 'GET':
        rules = PricingRule.objects.all().order_by('sort_order')
        return JsonResponse({'rules': [
            {'id': r.pk, 'name': r.name, 'description': r.description,
             'condition_json': r.condition_json, 'effect_type': r.effect_type,
             'effect_value': float(r.effect_value), 'applies_to': r.applies_to,
             'is_active': r.is_active, 'sort_order': r.sort_order}
            for r in rules
        ]})
    if request.method == 'POST':
        data = json.loads(request.body)
        from decimal import Decimal
        rule = PricingRule.objects.create(
            name=data.get('name', ''),
            description=data.get('description', ''),
            condition_json=data.get('condition_json', []),
            effect_type=data.get('effect_type', 'flat_add'),
            effect_value=Decimal(str(data.get('effect_value', 0))),
            applies_to=data.get('applies_to', 'subtotal'),
            is_active=data.get('is_active', True),
            sort_order=data.get('sort_order', 0),
        )
        return JsonResponse({'success': True, 'id': rule.pk})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def api_pricing_rule_detail(request, pk):
    rule = get_object_or_404(PricingRule, pk=pk)
    if request.method == 'PUT':
        data = json.loads(request.body)
        from decimal import Decimal
        for field in ('name', 'description', 'condition_json', 'effect_type', 'applies_to', 'is_active', 'sort_order'):
            if field in data:
                setattr(rule, field, data[field])
        if 'effect_value' in data:
            rule.effect_value = Decimal(str(data['effect_value']))
        rule.save()
        return JsonResponse({'success': True})
    if request.method == 'DELETE':
        rule.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def api_pricing_packages(request):
    if request.method == 'GET':
        pkgs = PricingPackage.objects.all().order_by('sort_order')
        return JsonResponse({'packages': [
            {'id': p.pk, 'name': p.name, 'description': p.description,
             'base_price': float(p.base_price),
             'included_item_ids': list(p.included_items.values_list('pk', flat=True)),
             'badge_label': p.badge_label, 'badge_color': p.badge_color,
             'is_active': p.is_active, 'is_public': p.is_public, 'sort_order': p.sort_order}
            for p in pkgs
        ]})
    if request.method == 'POST':
        data = json.loads(request.body)
        from decimal import Decimal
        pkg = PricingPackage.objects.create(
            name=data.get('name', ''),
            description=data.get('description', ''),
            base_price=Decimal(str(data.get('base_price', 0))),
            badge_label=data.get('badge_label', ''),
            badge_color=data.get('badge_color', ''),
            is_active=data.get('is_active', True),
            is_public=data.get('is_public', True),
            sort_order=data.get('sort_order', 0),
        )
        if data.get('included_item_ids'):
            pkg.included_items.set(data['included_item_ids'])
        return JsonResponse({'success': True, 'id': pkg.pk})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def api_pricing_package_detail(request, pk):
    pkg = get_object_or_404(PricingPackage, pk=pk)
    if request.method == 'PUT':
        data = json.loads(request.body)
        from decimal import Decimal
        for field in ('name', 'description', 'badge_label', 'badge_color', 'is_active', 'is_public', 'sort_order'):
            if field in data:
                setattr(pkg, field, data[field])
        if 'base_price' in data:
            pkg.base_price = Decimal(str(data['base_price']))
        pkg.save()
        if 'included_item_ids' in data:
            pkg.included_items.set(data['included_item_ids'])
        return JsonResponse({'success': True})
    if request.method == 'DELETE':
        pkg.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def api_pricing_formulas(request):
    if request.method == 'GET':
        formulas = PricingFormula.objects.all()
        return JsonResponse({'formulas': [
            {'id': f.pk, 'name': f.name, 'expression': f.expression,
             'description': f.description, 'is_active': f.is_active}
            for f in formulas
        ]})
    if request.method == 'POST':
        data = json.loads(request.body)
        formula = PricingFormula.objects.create(
            name=data.get('name', ''),
            expression=data.get('expression', ''),
            description=data.get('description', ''),
            is_active=data.get('is_active', True),
        )
        return JsonResponse({'success': True, 'id': formula.pk})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def api_pricing_formula_detail(request, pk):
    formula = get_object_or_404(PricingFormula, pk=pk)
    if request.method == 'PUT':
        data = json.loads(request.body)
        for field in ('name', 'expression', 'description', 'is_active'):
            if field in data:
                setattr(formula, field, data[field])
        formula.save()
        return JsonResponse({'success': True})
    if request.method == 'DELETE':
        formula.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def workflow_builder(request):
    workflows = PricingWorkflow.objects.all().order_by('-is_default', 'name')
    selected_wf_id = request.GET.get('wf')
    workflow_blocks = {str(w.pk): (w.workflow_json or []) for w in workflows}
    return render(request, 'dj_admin/workflow_builder.html', {
        'workflows': workflows,
        'workflow_blocks': workflow_blocks,
        'selected_wf_id': int(selected_wf_id) if selected_wf_id and selected_wf_id.isdigit() else None,
        'price_items': PriceItem.objects.all().order_by('sort_order'),
        'pricing_packages': PricingPackage.objects.all().order_by('sort_order'),
        'pricing_rules': PricingRule.objects.all().order_by('sort_order'),
        'pricing_formulas': PricingFormula.objects.filter(is_active=True),
    })


@staff_member_required
def api_pricing_workflows(request):
    if request.method == 'GET':
        wfs = PricingWorkflow.objects.all().order_by('-is_default', 'name')
        data = [{'id': w.pk, 'name': w.name, 'workflow_json': w.workflow_json,
                 'is_active': w.is_active, 'is_default': w.is_default} for w in wfs]
        return JsonResponse(data, safe=False)
    if request.method == 'POST':
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'error': 'Name erforderlich'}, status=400)
        if PricingWorkflow.objects.filter(name=name).exists():
            return JsonResponse({'error': f'Workflow "{name}" existiert bereits'}, status=400)
        if data.get('is_default'):
            PricingWorkflow.objects.filter(is_default=True).update(is_default=False)
        wf = PricingWorkflow.objects.create(
            name=name,
            workflow_json=data.get('workflow_json', []),
            is_active=data.get('is_active', True),
            is_default=data.get('is_default', False),
        )
        return JsonResponse({'success': True, 'id': wf.pk})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
def api_pricing_workflow_detail(request, pk):
    wf = get_object_or_404(PricingWorkflow, pk=pk)
    if request.method == 'PUT':
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        if name and name != wf.name and PricingWorkflow.objects.filter(name=name).exclude(pk=pk).exists():
            return JsonResponse({'error': f'Workflow "{name}" existiert bereits'}, status=400)
        if data.get('is_default'):
            PricingWorkflow.objects.filter(is_default=True).exclude(pk=pk).update(is_default=False)
        for field in ('workflow_json', 'is_active', 'is_default'):
            if field in data:
                setattr(wf, field, data[field])
        if name:
            wf.name = name
        wf.save()
        return JsonResponse({'success': True})
    if request.method == 'DELETE':
        wf.delete()
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Method not allowed'}, status=405)


@staff_member_required
@require_GET
def api_workflow_context(request):
    packages = [{'id': p.pk, 'name': p.name, 'price': float(p.base_price)}
                for p in PricingPackage.objects.filter(is_active=True).order_by('sort_order')]
    formulas = [{'id': f.pk, 'name': f.name, 'expression': f.expression}
                for f in PricingFormula.objects.filter(is_active=True)]
    rules_count = PricingRule.objects.filter(is_active=True).count()
    items_count = PriceItem.objects.filter(is_active=True).count()
    return JsonResponse({
        'packages': packages, 'formulas': formulas,
        'rules_count': rules_count, 'items_count': items_count,
    })
