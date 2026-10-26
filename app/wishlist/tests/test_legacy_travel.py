"""The public legacy estimate charges only round-trip kilometres over 100."""
from datetime import date, time
from decimal import Decimal

import pytest

from wishlist.models import AppConfig, Event, PricingPackage, PricingWorkflow
from wishlist.pricing_v2.services import calculate_with_mode


@pytest.mark.django_db
@pytest.mark.parametrize(('one_way_km', 'travel'), [
    ('0', '0'), ('49', '0'), ('50', '0'), ('50.5', '0.30'),
    ('51', '0.60'), ('60', '6.00'),
])
def test_legacy_public_estimate_bills_round_trip_only_beyond_100_km(one_way_km, travel):
    assert AppConfig.load().pricing_engine_mode == 'legacy'
    package = PricingPackage.objects.create(name='DJ', base_price=Decimal('660'))
    PricingWorkflow.objects.create(name='Basis', is_default=True, workflow_json=[
        {'type': 'package', 'config': {'package_id': package.pk}},
    ])
    payload = {
        'event_date': date(2026, 10, 31), 'start_time': time(18), 'end_time': time(23),
        'guest_count': 25, 'distance_km': Decimal(one_way_km),
        'package_id': package.pk, 'selected_item_ids': [],
    }
    result = calculate_with_mode(payload, public=True).visible_result
    assert result.gross_total == Decimal('660') + Decimal(travel)
    travel_lines = [line for line in result.lines if line.label.startswith('Anfahrt')]
    if Decimal(travel):
        assert len(travel_lines) == 1
        assert travel_lines[0].amount == Decimal(travel)
    else:
        assert travel_lines == []


@pytest.mark.django_db
def test_legacy_saved_event_uses_same_round_trip_travel_charge():
    package = PricingPackage.objects.create(name='DJ', base_price=Decimal('660'))
    PricingWorkflow.objects.create(name='Basis', is_default=True, workflow_json=[
        {'type': 'package', 'config': {'package_id': package.pk}},
    ])
    event = Event.objects.create(name='Gig', date=date(2026, 10, 31), location='X',
                                 time_start=time(18), time_end=time(23), distance_km=Decimal('60'))
    from wishlist.models import EventPriceCalculation
    EventPriceCalculation.objects.create(event=event, package=package)
    from wishlist.price_engine import PriceEngine
    direct = PriceEngine.calculate_workflow(event, workflow_id=1, package_id=package.pk)
    assert direct['grand_total'] == Decimal('666.00')
    result = calculate_with_mode(event=event).visible_result
    assert result.gross_total == Decimal('666.00')


@pytest.mark.django_db
def test_legacy_discount_does_not_reduce_distance_charge():
    from wishlist.price_engine import PriceEngine
    package = PricingPackage.objects.create(name='DJ', base_price=Decimal('660'))
    event = Event.objects.create(name='Gig', date=date(2026, 10, 31), location='X',
                                 time_start=time(18), time_end=time(23), distance_km=Decimal('60'))
    result = PriceEngine.calculate(event, package_id=package.pk, discount_percent=10)
    assert result['grand_total'] == Decimal('600.00')


@pytest.mark.django_db
def test_legacy_public_api_displays_round_trip_travel(client):
    import json
    from django.test import override_settings
    package = PricingPackage.objects.create(name='DJ', base_price=Decimal('660'))
    PricingWorkflow.objects.create(name='Basis', is_default=True, workflow_json=[
        {'type': 'package', 'config': {'package_id': package.pk}},
    ])
    with override_settings(SECURE_SSL_REDIRECT=False):
        response = client.post('/api/price-estimate/', data=json.dumps({
            'date': '2026-10-31', 'time_start': '18:00', 'time_end': '23:00',
            'guest_count': 25, 'package_id': package.pk, 'selected_item_ids': [],
            'distance_km': '60',
        }), content_type='application/json')
    assert response.status_code == 200
    assert response.json()['gross_total'] == '666.00'
    assert any(line['label'] == 'Anfahrt (Hin- und Rückfahrt)' and
               line['amount'] == '6.00' for line in response.json()['lines'])
