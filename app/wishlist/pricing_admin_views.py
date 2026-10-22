"""Staff-only administration of immutable global pricing profile versions."""
import json

from django.contrib.auth.decorators import user_passes_test
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .models import PricingProfile
from .pricing_v2.config import _FIELDS, validate_profile_config
from .pricing_v2.input import PricingValidationError


staff_only = user_passes_test(
    lambda user: user.is_active and user.is_staff, login_url='/dj-admin/login/',
)
STALE = 'Die Profilversion ist veraltet. Bitte Seite neu laden.'
INITIAL_CONFIG = {
    'included_hours': '0', 'extra_hour_rate': '0',
    'setup_minutes': 30, 'teardown_minutes': 30,
    'setup_teardown_hour_rate': '0', 'included_km': '0', 'km_rate': '0',
    'saturday_percent': '0', 'sunday_percent': '0', 'holiday_percent': '0',
    'holiday_region': 'NW', 'seasons': [], 'lead_time_tiers': [],
    'guest_tiers': [], 'minimum_price': '0', 'rounding_mode': 'nearest',
    'rounding_step': '1',
}


def _data(request):
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None
    data = request.POST
    try:
        config = {field: json.loads(data[field]) if field in ('seasons', 'lead_time_tiers', 'guest_tiers', 'setup_minutes', 'teardown_minutes')
                  else data[field] for field in _FIELDS}
        return {'name': data.get('name'), 'config_json': config,
                'expected_profile_id': json.loads(data.get('expected_profile_id', 'null')),
                'expected_version': json.loads(data.get('expected_version', 'null'))}
    except (KeyError, ValueError, TypeError):
        return None


def _bad_body():
    return JsonResponse({'errors': {'config_json': 'Ungültige oder unvollständige Eingabe.'}}, status=400)


def _validated(config):
    try:
        return validate_profile_config(config), None
    except PricingValidationError as error:
        return None, JsonResponse({'errors': {error.field or 'config_json': error.message}}, status=400)


def _current(queryset):
    return queryset.order_by('-version', '-pk').first()


@staff_only
@require_http_methods(['GET', 'POST'])
def profile_page(request):
    if request.method == 'GET':
        profiles = list(PricingProfile.objects.order_by('-version', '-pk'))
        active = next((profile for profile in profiles if profile.status == 'active'), None)
        latest = profiles[0] if profiles else None
        source = latest or active
        config = source.config_json if source else INITIAL_CONFIG
        fields = [{'key': key, 'value': json.dumps(config.get(key, []), ensure_ascii=False)
                   if key in ('seasons', 'lead_time_tiers', 'guest_tiers') else config.get(key, '')}
                  for key in _FIELDS]
        return render(request, 'dj_admin/pricing_v2/profile.html', {
            'profiles': profiles, 'active': active, 'latest': latest,
            'fields': fields, 'name': source.name if source else 'Global',
            'initial_config': config,
        })
    data = _data(request)
    if data is None:
        return _bad_body()
    _, error = _validated(data.get('config_json'))
    if error:
        return error
    name = data.get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return JsonResponse({'errors': {'name': 'Ein gültiger Profilname ist erforderlich.'}}, status=400)
    try:
        with transaction.atomic():
            latest = _current(PricingProfile.objects.select_for_update())
            if (data.get('expected_profile_id'), data.get('expected_version')) != (
                latest.pk if latest else None, latest.version if latest else None,
            ):
                return JsonResponse({'error': STALE}, status=409)
            new = PricingProfile.objects.create(
                name=name.strip(), version=latest.version + 1 if latest else 1,
                status=PricingProfile.Status.DRAFT, valid_from=timezone.now(),
                config_json=data['config_json'], created_by=request.user,
            )
    except IntegrityError:
        return JsonResponse({'error': STALE}, status=409)
    return JsonResponse({'id': new.pk, 'version': new.version, 'status': new.status}, status=201)


@staff_only
@require_POST
def validate_profile(request):
    data = _data(request)
    if data is None:
        return _bad_body()
    normalized, error = _validated(data.get('config_json'))
    if error:
        return error
    return JsonResponse({'valid': True, 'preview': {key: str(getattr(normalized, key)) for key in _FIELDS}})


@staff_only
@require_POST
def activate_profile(request):
    data = _data(request)
    if data is None:
        return _bad_body()
    try:
        profile_id = data.get('profile_id')
        if isinstance(profile_id, bool) or not isinstance(profile_id, int):
            raise ValueError
        with transaction.atomic():
            # Lock the active row and target in a consistent order. The database's
            # conditional unique constraint is the last line of defense.
            profiles = list(PricingProfile.objects.select_for_update().order_by('pk'))
            active = next((p for p in profiles if p.status == 'active'), None)
            if (data.get('expected_profile_id'), data.get('expected_version')) != (
                active.pk if active else None, active.version if active else None,
            ):
                return JsonResponse({'error': STALE}, status=409)
            target = next((p for p in profiles if p.pk == profile_id and p.status == 'draft'), None)
            if target is None:
                return JsonResponse({'errors': {'profile_id': 'Entwurf nicht gefunden.'}}, status=400)
            _, error = _validated(target.config_json)
            if error:
                return error
            if active:
                active.status = PricingProfile.Status.RETIRED
                active.save(update_fields=['status'])
            target.status = PricingProfile.Status.ACTIVE
            target.valid_from = timezone.now()
            target.save(update_fields=['status', 'valid_from'])
    except IntegrityError:
        return JsonResponse({'error': STALE}, status=409)
    except ValueError:
        return JsonResponse({'errors': {'profile_id': 'Ungültige Profil-ID.'}}, status=400)
    return JsonResponse({'id': target.pk, 'version': target.version, 'status': target.status})
