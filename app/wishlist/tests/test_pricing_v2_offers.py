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


@pytest.mark.parametrize('mode', ['legacy', 'shadow'])
def test_offer_workflow_rejects_non_v2_modes(offer_setup, mode):
    event, actor = offer_setup
    AppConfig.objects.update(pricing_engine_mode=mode)
    with pytest.raises(PricingValidationError):
        build_draft(event, actor)
    with pytest.raises(PricingValidationError):
        add_adjustment(event, 'Fee', '1.00', 'Reason', actor)
    with pytest.raises(PricingValidationError):
        release_offer(event, actor)


@pytest.mark.parametrize('amount', [True, False, 1.0, '1.001', '10000000000', '-10000000000'])
def test_adjustment_amount_is_exact_decimal_field_range(offer_setup, amount):
    event, actor = offer_setup
    with pytest.raises(PricingValidationError) as error:
        add_adjustment(event, 'Fee', amount, 'Reason', actor)
    assert error.value.code == 'invalid_input'
    assert not ManualPriceAdjustment.objects.exists()


def test_release_is_idempotent_and_immutable(offer_setup):
    event, actor = offer_setup
    draft = build_draft(event, actor)
    released = release_offer(event, actor)
    assert released.pk == release_offer(event, actor).pk
    assert released.offer_number == 1
    assert Event.objects.get(pk=event.pk).status == 'inquiry'
    before = released.result_json
    # A recalculation after release must not mutate the released snapshot.
    build_draft(event, actor)
    assert PriceCalculationRevision.objects.get(pk=released.pk).result_json == before
    assert release_offer(event, actor).pk == released.pk

    # A changed draft is a new offer; retrying it must not return the old one.
    add_adjustment(event, 'Extra', '5', 'Neue Leistung', actor)
    newer_draft = build_draft(event, actor)
    newer_offer = release_offer(event, actor)
    assert newer_offer.pk != released.pk
    assert newer_offer.offer_number == 2
    assert newer_offer.gross_total == released.gross_total + Decimal('5.00')
    assert release_offer(event, actor).pk == newer_offer.pk
    released.refresh_from_db()
    assert released.offer_number == 1 and released.result_json == before


def test_same_total_but_changed_input_creates_new_offer(offer_setup):
    event, actor = offer_setup
    first = release_offer(event, actor) if build_draft(event, actor) else None
    event.guest_count = 1
    event.save(update_fields=['guest_count'])
    second_draft = build_draft(event, actor)
    second = release_offer(event, actor)
    assert second.pk != first.pk
    assert second.offer_number == 2
    assert second.input_json != first.input_json


def test_released_snapshot_unchanged_by_later_adjustment_and_draft(offer_setup):
    event, actor = offer_setup
    released = release_offer(event, actor) if build_draft(event, actor) else None
    snapshot = (released.input_json, released.result_json, released.gross_total, released.offer_number)
    add_adjustment(event, 'Later', '5.00', 'After release', actor)
    build_draft(event, actor)
    released.refresh_from_db()
    assert (released.input_json, released.result_json, released.gross_total, released.offer_number) == snapshot


def test_event_panel_uses_earliest_estimate_and_current_difference(offer_setup):
    event, actor = offer_setup
    from django.test import Client
    from django.urls import reverse
    estimate = PriceCalculationRevision.objects.create(
        event=event, kind=PriceCalculationRevision.Kind.ESTIMATE,
        status=PriceCalculationRevision.Status.DRAFT, revision_number=1,
        engine_version='2', input_json={'estimate': True}, result_json={'estimate': True},
        gross_total=Decimal('90.00'), created_by=actor)
    draft = build_draft(event, actor)
    event.total_price = Decimal('999.00')
    event.save(update_fields=['total_price'])
    client = Client()
    client.force_login(actor)
    response = client.get(reverse('dj_admin:event_edit', kwargs={'pk': event.pk}))
    assert response.context['original_estimate'].pk == estimate.pk
    assert response.context['current_draft'].pk == draft.pk
    assert response.context['current_difference'] == draft.gross_total - estimate.gross_total


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
