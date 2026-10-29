import json
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from wishlist.models import AppConfig, PricingPackage
from django.test import override_settings
from django.core.cache import cache


@pytest.fixture(autouse=True)
def isolated_provider_settings():
    cache.clear()
    with override_settings(GEOAPIFY_API_KEY='test-only-key'):
        yield
    cache.clear()


def test_route_uses_current_address_and_driving_distance_not_airline_factor(db):
    from wishlist.booking_distance import resolve_booking_distance
    AppConfig.objects.create(dj_home_lat='51.430000', dj_home_lon='6.770000')
    geocode = Mock(status_code=200)
    geocode.json.return_value = {'results': [{'lat': '51.5', 'lon': '7.0', 'street': 'Teststraße', 'housenumber': '2', 'postcode': '45127', 'city': 'Essen', 'result_type': 'building', 'rank': {'confidence': 1}}]}
    route = Mock(status_code=200)
    route.json.return_value = {'features': [{'properties': {'distance': 60000}}]}
    with patch('wishlist.booking_distance.requests.get', side_effect=[geocode, route]) as get:
        distance = resolve_booking_distance(dict(address_street='Teststraße 2', address_zip='45127', address_city='Essen'))
    assert distance == Decimal('60.0')
    assert 'Teststraße 2' in get.call_args_list[0].kwargs['params']['text']
    assert get.call_args_list[1].kwargs['params']['waypoints'] == '51.43,6.77|51.5,7.0'
    assert all('api.geoapify.com' in call.args[0] for call in get.call_args_list)


def test_missing_origin_never_fabricates_distance(db):
    from wishlist.booking_distance import resolve_booking_distance
    with patch('wishlist.booking_distance.requests.get') as get:
        assert resolve_booking_distance(dict(address_street='Teststraße 2', address_city='Essen')) is None
    get.assert_not_called()


def test_routing_failure_does_not_fall_back_to_browser_distance(db):
    from wishlist.booking_distance import resolve_booking_distance
    AppConfig.objects.create(dj_home_lat='51.43', dj_home_lon='6.77')
    with patch('wishlist.booking_distance.requests.get', side_effect=RuntimeError('offline')):
        assert resolve_booking_distance(dict(address_street='Teststraße 3', address_city='Essen', distance_km='0')) is None


def test_postcode_city_alone_cannot_be_used_as_venue_distance(db):
    from wishlist.booking_distance import resolve_booking_distance
    AppConfig.objects.create(dj_home_lat='51.43', dj_home_lon='6.77')
    geocode = Mock(status_code=200)
    geocode.json.return_value = [{'lat': '51.5', 'lon': '7.0'}]
    route = Mock(status_code=200)
    route.json.return_value = {'code': 'Ok', 'routes': [{'distance': 60000}]}
    with patch('wishlist.booking_distance.requests.get', side_effect=[geocode, route]) as get:
        assert resolve_booking_distance(dict(address_zip='45127', address_city='Essen')) is None
    get.assert_not_called()


def test_geocoder_locality_fallback_cannot_be_used_as_venue(db):
    from wishlist.booking_distance import resolve_booking_distance
    AppConfig.objects.create(dj_home_lat='51.43', dj_home_lon='6.77')
    geocode = Mock(status_code=200)
    geocode.json.return_value = {'results': [{'lat': '51.5', 'lon': '7.0', 'city': 'Essen', 'postcode': '45127', 'result_type': 'city'}]}
    with patch('wishlist.booking_distance.requests.get', return_value=geocode) as get:
        assert resolve_booking_distance(dict(address_street='Ungültige Straße 2', address_zip='45127', address_city='Essen')) is None
    assert get.call_count == 1


def test_missing_api_key_makes_no_external_requests(db):
    from wishlist.booking_distance import resolve_booking_distance
    AppConfig.objects.create(dj_home_lat='51.43', dj_home_lon='6.77')
    with override_settings(GEOAPIFY_API_KEY=''), patch('wishlist.booking_distance.requests.get') as get:
        assert resolve_booking_distance(dict(address_street='Teststraße 2', address_zip='45127', address_city='Essen')) is None
    get.assert_not_called()


def test_daily_budget_is_shared_and_never_exceeds_free_allowance(db):
    from wishlist.booking_distance import reserve_provider_credits
    from wishlist.models import LocationApiBudget
    from django.utils import timezone
    key = 'day:' + timezone.now().date().isoformat()
    LocationApiBudget.objects.create(key=key, used=2495)
    assert reserve_provider_credits(20) is False
    assert LocationApiBudget.objects.get(key=key).used == 2495


def test_request_rate_is_limited_across_workers(db):
    from wishlist.booking_distance import reserve_provider_credits
    from django.utils import timezone
    now = timezone.now()
    with patch('wishlist.booking_distance.timezone.now', return_value=now):
        assert [reserve_provider_credits() for _ in range(5)] == [True, True, True, True, False]


def test_different_building_is_not_treated_as_resolved_venue(db):
    from wishlist.booking_distance import resolve_booking_distance
    AppConfig.objects.create(dj_home_lat='51.43', dj_home_lon='6.77')
    geocode = Mock(status_code=200)
    geocode.json.return_value = {'results': [{'lat': '51.5', 'lon': '7.0', 'street': 'Teststraße',
        'housenumber': '99', 'postcode': '45127', 'city': 'Essen', 'result_type': 'building', 'rank': {'confidence': 1}}]}
    with patch('wishlist.booking_distance.requests.get', return_value=geocode) as get:
        assert resolve_booking_distance(dict(address_street='Teststraße 2', address_zip='45127', address_city='Essen')) is None
    assert get.call_count == 1


def test_transient_provider_limit_does_not_cache_address_as_unavailable(db):
    from wishlist.booking_distance import resolve_booking_distance
    AppConfig.objects.create(dj_home_lat='51.43', dj_home_lon='6.77')
    data = dict(address_street='Teststraße 2', address_zip='45127', address_city='Essen')
    geocode = Mock(status_code=200)
    geocode.json.return_value = {'results': [{'lat': '51.5', 'lon': '7.0', 'street': 'Teststraße',
        'housenumber': '2', 'postcode': '45127', 'city': 'Essen', 'result_type': 'building', 'rank': {'confidence': 1}}]}
    route = Mock(status_code=200)
    route.json.return_value = {'features': [{'properties': {'distance': 60000}}]}
    with patch('wishlist.booking_distance.reserve_provider_credits', side_effect=[False, True, True]), \
         patch('wishlist.booking_distance.requests.get', side_effect=[geocode, route]) as get:
        assert resolve_booking_distance(data) is None
        assert resolve_booking_distance(data) == Decimal('60.0')
    assert get.call_count == 2


def test_autocomplete_is_proxied_without_exposing_api_key(client, db):
    response = Mock(status_code=200)
    response.json.return_value = {'results': [{'formatted': 'Teststraße 2, Essen', 'street': 'Teststraße', 'housenumber': '2', 'postcode': '45127', 'city': 'Essen', 'lat': 51.5, 'lon': 7.0}]}
    with patch('wishlist.booking_distance.requests.get', return_value=response):
        result = client.post('/api/address-search/', data=json.dumps({'query': 'Teststraße 2'}), content_type='application/json')
    assert result.status_code == 200
    assert result.json()['results'][0]['address']['road'] == 'Teststraße'
    assert 'test-only-key' not in result.content.decode()


def test_public_address_search_has_a_per_client_credit_limit(client, db):
    with patch('wishlist.booking_distance._request', return_value={'results': []}) as provider:
        for number in range(15):
            response = client.post('/api/address-search/', data=json.dumps({'query': f'Example address {number}'}),
                content_type='application/json', REMOTE_ADDR='203.0.113.8')
            assert response.status_code == (200 if number < 10 else 429)
    assert provider.call_count <= 10


@pytest.mark.parametrize('city,zip_code,street,country', [
    ('Wien', '1010', 'Kärntner Straße 1', 'at'),
    ('Zürich', '8001', 'Bahnhofstrasse 1', 'ch'),
])
def test_austrian_and_swiss_bookings_remain_searchable_and_routeable(db, city, zip_code, street, country):
    from wishlist.booking_distance import resolve_booking_distance, search_booking_addresses
    AppConfig.objects.create(dj_home_lat='51.43', dj_home_lon='6.77')
    geocode = Mock(status_code=200)
    geocode.json.return_value = {'results': [{'lat': '47.0', 'lon': '8.0', 'street': street.rsplit(' ', 1)[0],
        'housenumber': '1', 'postcode': zip_code, 'city': city, 'country_code': country,
        'result_type': 'building', 'rank': {'confidence': 1}}]}
    route = Mock(status_code=200)
    route.json.return_value = {'features': [{'properties': {'distance': 200000}}]}
    with patch('wishlist.booking_distance.requests.get', side_effect=[geocode, geocode, route]) as get:
        search_booking_addresses(f'{street}, {zip_code} {city}')
        km = resolve_booking_distance({'address_street': street, 'address_zip': zip_code, 'address_city': city})
    assert km == Decimal('200.0')
    assert 'de,at,ch' in get.call_args_list[0].kwargs['params']['filter']
    assert 'de,at,ch' in get.call_args_list[1].kwargs['params']['filter']


def test_street_name_with_number_is_not_cut_at_first_number(db):
    from wishlist.booking_distance import resolve_booking_distance
    AppConfig.objects.create(dj_home_lat='51.43', dj_home_lon='6.77')
    geocode = Mock(status_code=200)
    geocode.json.return_value = {'results': [{'lat': '52.5', 'lon': '13.3', 'street': 'Straße des 17. Juni',
        'housenumber': '135', 'postcode': '10623', 'city': 'Berlin',
        'result_type': 'building', 'rank': {'confidence': 1}}]}
    route = Mock(status_code=200)
    route.json.return_value = {'features': [{'properties': {'distance': 400000}}]}
    with patch('wishlist.booking_distance.requests.get', side_effect=[geocode, route]):
        assert resolve_booking_distance({'address_street': 'Straße des 17. Juni 135',
            'address_zip': '10623', 'address_city': 'Berlin'}) == Decimal('400.0')


def test_booking_package_cannot_trust_addressless_browser_distance(client, db):
    from django.core.management import call_command
    call_command('setup_basis_package')
    package = PricingPackage.objects.get(name='Basis-Paket')
    response = client.post('/api/price-estimate/', data=json.dumps(dict(
        date='2027-01-04', time_start='18:00', time_end='00:30', guest_count=70,
        with_sound=False, package_id=package.pk, selected_item_ids=[], distance_km='0')),
        content_type='application/json')
    assert response.status_code == 200
    assert response.json()['gross_total'] is None


def test_public_estimate_uses_server_resolved_address_distance(client, db):
    from django.core.management import call_command
    call_command('setup_basis_package')
    package = PricingPackage.objects.get(name='Basis-Paket')
    with patch('wishlist.booking_distance.resolve_booking_distance', return_value=Decimal('60.0')) as resolve:
        response = client.post('/api/price-estimate/', data=json.dumps(dict(
            date='2027-01-04', time_start='18:00', time_end='00:30', guest_count=70,
            with_sound=False, package_id=package.pk, selected_item_ids=[], distance_km='0',
            address_street='Teststraße 2', address_zip='45127', address_city='Essen')),
            content_type='application/json')
    assert response.status_code == 200
    assert response.json()['gross_total'] == '736.00'
    assert response.json()['distance_km'] == '60.0'
    assert resolve.call_args.args[0]['address_city'] == 'Essen'
