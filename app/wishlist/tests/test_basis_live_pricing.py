"""Live basis package must calculate the same price in preview and booking."""
import json
from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from django.core.cache import cache
from wishlist.models import AppConfig, Event, PricingPackage, PricingWorkflow


@pytest.fixture
def basis(db):
    from django.core.management import call_command
    call_command('setup_basis_package')
    return PricingPackage.objects.get(name='Basis-Paket')


def estimate(client, pkg, *, guests, sound, start='18:00', end='00:00', distance='50', day='2026-11-02'):
    with patch('wishlist.booking_distance.resolve_booking_distance', return_value=Decimal(distance) if distance is not None else None):
        return client.post('/api/price-estimate/', data=json.dumps({
            'date': day, 'time_start': start, 'time_end': end, 'guest_count': guests,
            'package_id': pkg.pk, 'selected_item_ids': [], 'distance_km': distance,
            'address_street': 'Teststraße 1', 'address_zip': '45127', 'address_city': 'Essen',
            'with_sound': sound,
        }), content_type='application/json', HTTP_X_FORWARDED_PROTO='https')


@pytest.mark.django_db
@pytest.mark.parametrize('guests,sound,want', [
    (50, True, '660.00'), (51, True, '750.00'), (100, True, '750.00'),
    (50, False, '580.00'), (51, False, '670.00'),
])
def test_both_guest_tiers_and_sound_options_apply_on_weekdays(client, basis, guests, sound, want):
    assert estimate(client, basis, guests=guests, sound=sound).json()['gross_total'] == want


@pytest.mark.django_db
@pytest.mark.parametrize('end,want', [('00:00','660.00'), ('00:30','720.00'),
                                      ('01:00','720.00'), ('01:30','780.00')])
def test_each_started_extra_hour_is_billed_in_full(client, basis, end, want):
    assert estimate(client, basis, guests=50, sound=True, end=end).json()['gross_total'] == want


@pytest.mark.django_db
def test_all_factors_add_up_and_are_itemized(client, basis):
    result = estimate(client, basis, guests=51, sound=False, end='00:30', distance='60').json()
    assert result['gross_total'] == '736.00'  # 660 + 90 - 80 + 60 + 6
    labels = [line['label'] for line in result['lines']]
    assert any('Gäste' in label for label in labels)
    assert any('Tontechnik' in label for label in labels)
    assert any('Zusatzstunde' in label for label in labels)
    assert any('Anfahrt' in label for label in labels)


@pytest.mark.django_db
@pytest.mark.parametrize('changes', [dict(guests=None), dict(start=None), dict(distance=None)])
def test_incomplete_basis_estimate_never_shows_final_price(client, basis, changes):
    args = dict(guests=50, sound=True, start='18:00', distance='50')
    args.update(changes)
    data = estimate(client, basis, **args).json()
    assert data['gross_total'] is None
    assert data['status'] == 'individual_review'


@pytest.mark.django_db
def test_submission_saves_the_same_quote_and_sound_selection(client, basis):
    cache.clear()
    day = (timezone.now().date() + timedelta(days=40)).isoformat()
    preview = estimate(client, basis, guests=51, sound=False, end='00:30', distance='60', day=day).json()
    body = dict(name='Party', date=day, time_start='18:00', time_end='00:30',
        address_street='Teststraße 1', address_zip='45127', address_city='Essen',
        location='Ort', client_name='Max', client_email='max@example.com', privacy_consent=True,
        package_id=basis.pk, selected_items=[], guest_count=51, distance_km='60', with_sound=False)
    with patch('wishlist.booking_distance.resolve_booking_distance', return_value=Decimal('60')), patch('wishlist.views.GoogleService.check_date_available', return_value=(True, [])), patch('wishlist.views.threading.Thread'):
        response = client.post('/api/submit-event/', data=json.dumps(body),
            content_type='application/json', HTTP_X_FORWARDED_PROTO='https')
    assert response.status_code == 200
    event = Event.objects.get(pk=response.json()['event_id'])
    assert event.total_price == Decimal(preview['gross_total']) == Decimal('736.00')
    assert event.with_sound is False


@pytest.mark.django_db
def test_public_booking_form_shows_tech_selection_and_sends_it_to_both_endpoints(client, basis):
    page = client.get('/buchen/?embed=1', HTTP_X_FORWARDED_PROTO='https')
    assert page.status_code == 200
    html = page.content.decode()
    assert 'name="f-with-sound"' in html
    assert 'Ohne Tontechnik' in html
    assert 'Grundpreis 660' in html
    assert 'Nebelmaschine inklusive' in html
    assert html.count('with_sound:') == 2


@pytest.mark.django_db
def test_incomplete_basis_submission_keeps_inquiry_without_final_price(client, basis):
    cache.clear()
    body = dict(name='Party', date=(timezone.now().date() + timedelta(days=40)).isoformat(),
        time_start=None, time_end=None, location='Ort', client_name='Max',
        client_email='max@example.com', privacy_consent=True, package_id=basis.pk,
        selected_items=[], guest_count=51, distance_km='60', with_sound=True)
    with patch('wishlist.views.GoogleService.check_date_available', return_value=(True, [])), patch('wishlist.views.threading.Thread'):
        response = client.post('/api/submit-event/', data=json.dumps(body),
            content_type='application/json', HTTP_X_FORWARDED_PROTO='https')
    assert response.status_code == 200
    event = Event.objects.get(pk=response.json()['event_id'])
    assert event.total_price is None


@pytest.mark.django_db
def test_invalid_sound_selection_cannot_be_submitted(client, basis):
    cache.clear()
    body = dict(name='Party', date=(timezone.now().date() + timedelta(days=40)).isoformat(),
        time_start='18:00', time_end='00:00', location='Ort', client_name='Max',
        client_email='max@example.com', privacy_consent=True, package_id=basis.pk,
        selected_items=[], guest_count=51, distance_km='60', with_sound='false')
    response = client.post('/api/submit-event/', data=json.dumps(body),
        content_type='application/json', HTTP_X_FORWARDED_PROTO='https')
    assert response.status_code == 400
    assert not Event.objects.exists()
