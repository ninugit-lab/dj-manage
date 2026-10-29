"""Resolve a booking's current address to one-way driving kilometres."""
import hashlib
import logging
import re
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.core.cache import cache
from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import AppConfig, LocationApiBudget


logger = logging.getLogger(__name__)


class LocationProviderUnavailable(Exception):
    """Transient rate/budget/API failure; never cache as an invalid address."""


def reserve_provider_credits(credits=1):
    """Fail closed under concurrency/DB contention. Leaves headroom below free limits."""
    now = timezone.now()
    try:
        with transaction.atomic():
            LocationApiBudget.objects.filter(key__startswith='second:',
                key__lt=f'second:{int(now.timestamp()) - 10}').delete()
            for key, limit, cost in ((f'day:{now.date().isoformat()}', 2500, credits),
                                     (f'second:{int(now.timestamp())}', 4, 1)):
                LocationApiBudget.objects.get_or_create(key=key)
                if not LocationApiBudget.objects.filter(key=key, used__lte=limit-cost).update(used=F('used')+cost):
                    raise ValueError('free budget exhausted')
        return True
    except Exception:
        return False


def _request(path, params, credits=1):
    api_key = getattr(settings, 'GEOAPIFY_API_KEY', '')
    if not api_key or not reserve_provider_credits(credits):
        raise LocationProviderUnavailable('location provider unavailable')
    try:
        response = requests.get('https://api.geoapify.com/v1/' + path,
            params={**params, 'apiKey': api_key},
            headers={'User-Agent': 'DJRedoo-Booking/1.0 (https://dj-redoo.de)'}, timeout=7)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise LocationProviderUnavailable('location provider request failed') from exc


def search_booking_addresses(query):
    if not isinstance(query, str) or not 3 <= len(query.strip()) <= 300:
        return []
    key = 'geoapify-search:' + hashlib.sha256(query.strip().encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        data = _request('geocode/autocomplete', {'text': query.strip(), 'format': 'json',
            'filter': 'countrycode:de,at,ch', 'limit': 5, 'lang': 'de'})
        result = [{'display_name': item.get('formatted', ''), 'address': {
            'road': item.get('street', ''), 'house_number': item.get('housenumber', ''),
            'postcode': item.get('postcode', ''), 'city': item.get('city', ''),
            'amenity': item.get('name', '')}} for item in data.get('results', [])[:5]]
        cache.set(key, result, timeout=3600)
        return result
    except Exception:
        return []


def resolve_booking_distance(data):
    if not getattr(settings, 'GEOAPIFY_API_KEY', ''):
        return None
    config = AppConfig.load()
    if config.dj_home_lat is None or config.dj_home_lon is None:
        return None
    parts = [data.get(key, '') for key in ('address_street', 'address_zip', 'address_city')]
    if any(not isinstance(part, str) or len(part) > 300 for part in parts):
        return None
    street, postal, city = [part.strip() for part in parts]
    if not city or not street or not postal:
        return None
    address = ', '.join(part for part in (street, f'{postal} {city}'.strip()) if part)
    origin = f'{float(config.dj_home_lon)},{float(config.dj_home_lat)}'
    key = 'geoapify-route:' + hashlib.sha256(f'{origin}|{address}'.encode()).hexdigest()
    stored = cache.get(key)
    if stored is not None:
        return Decimal(stored) if stored != 'unavailable' else None
    try:
        candidates = _request('geocode/search', {'text': address, 'format': 'json',
            'limit': 1, 'filter': 'countrycode:de,at,ch', 'lang': 'de'}).get('results', [])
        if not candidates:
            raise ValueError('address unresolved')
        matched = candidates[0]
        matched_street = matched.get('street', '')
        normalize = lambda text: re.sub(r'[^a-z0-9]', '', text.casefold().replace('ß', 'ss'))
        # Broad locality/postcode fallbacks are not a resolved venue address.
        entered_street = re.sub(r'\s+\d+\S*\s*$', '', street).strip()
        if not matched_street or normalize(matched_street) != normalize(entered_street) or matched.get('postcode') != postal:
            raise ValueError('address match too broad')
        house_number = re.search(r'\s+(\d+\S*)\s*$', street)
        if not house_number or normalize(matched.get('housenumber', '')) != normalize(house_number.group(1)):
            raise ValueError('building not matched')
        if matched.get('result_type') not in ('building', 'amenity') or Decimal(str(matched.get('rank', {}).get('confidence', 0))) < Decimal('.9'):
            raise ValueError('address match uncertain')
        lat, lon = Decimal(str(candidates[0]['lat'])), Decimal(str(candidates[0]['lon']))
        if not lat.is_finite() or not lon.is_finite() or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError('invalid coordinates')
        result = _request('routing', {'waypoints': f'{float(config.dj_home_lat)},{float(config.dj_home_lon)}|{float(lat)},{float(lon)}',
            'mode': 'drive', 'units': 'metric'}, credits=20)
        metres = Decimal(str(result['features'][0]['properties']['distance']))
        if not metres.is_finite() or metres < 0 or metres > 9000000:
            raise ValueError('invalid route distance')
        distance = (metres / 1000).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)
        cache.set(key, str(distance), timeout=3600)
        return distance
    except LocationProviderUnavailable:
        return None
    except Exception:
        # Never log addresses or remote responses containing location data.
        logger.warning('Booking address or driving distance could not be resolved')
        cache.set(key, 'unavailable', timeout=30)
        return None
