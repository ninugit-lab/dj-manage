from datetime import date, time
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from wishlist.models import AppConfig, Event, EventPriceCalculation, PriceCalculationRevision, PriceItem, PricingPackage, PricingProfile, PricingWorkflow, ManualPriceAdjustment
from wishlist.pricing_v2.offers import add_adjustment, build_draft, release_offer
from wishlist.pricing_v2.input import PricingValidationError


@pytest.fixture
def offer_setup(db):
    AppConfig.objects.create(pricing_engine_mode='v2')
    PricingProfile.objects.create(name='Offers', version=1, status='active', valid_from=timezone.now(), config_json={
        'included_hours': '3', 'extra_hour_rate': '0', 'setup_minutes': 0,
        'teardown_minutes': 0, 'setup_teardown_hour_rate': '0', 'included_km': '0',
        'km_rate': '0', 'saturday_percent': '0', 'sunday_percent': '0',
        'holiday_percent': '0', 'holiday_region': 'BY', 'seasons': [],
        'lead_time_tiers': [], 'guest_tiers': [{'lower_bound': 0, 'upper_bound': None, 'value': '0'}],
        'minimum_price': '0', 'rounding_mode': 'nearest', 'rounding_step': '0.01',
    })
    package = PricingPackage.objects.create(name='Basis', version=1, base_price=Decimal('100'))
    item = PriceItem.objects.create(name='Licht', price=Decimal('20'))
    workflow = PricingWorkflow.objects.create(name='Standard', version=1, is_default=True,
        workflow_json=[{'type': 'package', 'config': {'package_id': package.pk}}, {'type': 'items', 'config': {'item_ids': [item.pk]}}])
    event = Event.objects.create(name='Party', date=date(2026, 12, 1), location='X', time_start=time(18), time_end=time(21))
    calc = EventPriceCalculation.objects.create(event=event, package=package, workflow=workflow)
    calc.selected_items.add(item)
    actor = get_user_model().objects.create_user('offer-admin', is_staff=True)
    return event, actor


def test_adjustment_validation_and_snapshot(offer_setup):
    event, actor = offer_setup
    with pytest.raises(PricingValidationError):
        add_adjustment(event, ' ', '1', 'why', actor)
    with pytest.raises(PricingValidationError):
        add_adjustment(event, 'Fee', '0', 'why', actor)
    adjustment = add_adjustment(event, 'Rabatt', '-10', 'Kulanz', actor)
    draft = build_draft(event, actor)
    assert adjustment.amount == Decimal('-10.00')
    assert draft.gross_total == Decimal('110.00')
    adjustment_snapshot = draft.result_json['admin_adjustments'][0]
    assert adjustment_snapshot['label'] == 'Rabatt' and adjustment_snapshot['reason'] == 'Kulanz'


def test_release_is_idempotent_and_immutable(offer_setup):
    event, actor = offer_setup
    draft = build_draft(event, actor)
    released = release_offer(event, actor)
    assert released.pk == release_offer(event, actor).pk
    assert released.offer_number == 1
    assert Event.objects.get(pk=event.pk).status == 'inquiry'
    before = released.result_json
    build_draft(event, actor)
    assert PriceCalculationRevision.objects.get(pk=released.pk).result_json == before


def test_offer_routes_staff_csrf_and_no_direct_total_override(offer_setup):
    event, actor = offer_setup
    client = Client(enforce_csrf_checks=True)
    client.force_login(actor)
    url = reverse('dj_admin:event_price_adjust', kwargs={'pk': event.pk})
    assert client.post(url, {'label': 'X', 'amount': '1', 'reason': 'R'}).status_code == 403
    client.get(reverse('dj_admin:event_edit', kwargs={'pk': event.pk}))
    token = client.cookies['csrftoken'].value
    response = client.post(url, {'label': 'X', 'amount': '1', 'reason': 'R'}, HTTP_X_CSRFTOKEN=token)
    assert response.status_code == 200
    assert Event.objects.get(pk=event.pk).total_price is None
