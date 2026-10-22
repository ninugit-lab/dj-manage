"""The canonical dispatcher prices a public individual-items selection without a package."""
from decimal import Decimal

import pytest
from django.utils import timezone

from wishlist.models import AppConfig, PriceItem, PricingPackage, PricingProfile, PricingWorkflow
from wishlist.pricing_v2.services import calculate_with_mode


@pytest.fixture
def catalog(db):
    package = PricingPackage.objects.create(name='Other offer', base_price=Decimal('500'))
    required = PriceItem.objects.create(name='Required', price=Decimal('20'), is_required=True)
    extra = PriceItem.objects.create(name='Optional', price=Decimal('30'))
    PricingWorkflow.objects.create(name='Default', is_default=True, workflow_json=[
        {'type': 'package', 'config': {'package_id': package.pk}}, {'type': 'items'},
        {'type': 'duration'}, {'type': 'travel'}])
    PricingProfile.objects.create(name='Live', status='active', valid_from=timezone.now(), config_json=dict(
        included_hours='3', extra_hour_rate='10', setup_minutes=0, teardown_minutes=0,
        setup_teardown_hour_rate='0', included_km='0', km_rate='2', saturday_percent='0',
        sunday_percent='0', holiday_percent='0', holiday_region='BY', seasons=[],
        lead_time_tiers=[], guest_tiers=[{'lower_bound': 0, 'upper_bound': None, 'value': '0'}],
        minimum_price='0', rounding_mode='nearest', rounding_step='0.01'))
    return required, extra


@pytest.mark.parametrize('mode,version,gross', [('legacy', '1', '50.00'), ('shadow', '1', '50.00'), ('v2', '2', '70.00')])
def test_individual_items_use_authoritative_profile_and_workflow(catalog, mode, version, gross):
    AppConfig.objects.create(pricing_engine_mode=mode)
    required, extra = catalog
    payload = {'event_date': '2026-12-02', 'start_time': '18:00', 'end_time': '22:00',
               'distance_km': '5', 'package_id': None, 'selected_item_ids': [str(extra.pk)]}
    result = calculate_with_mode(payload, public=True).visible_result
    assert result.engine_version == version
    # V1 retains its pre-cutover arithmetic; only V2 applies profile travel and duration factors.
    assert result.gross_total == Decimal(gross)
    assert {line.label for line in result.lines if line.code.startswith('item:')} == {'Required', 'Optional'}


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'v2'])
def test_individual_items_required_only_and_private_selection(catalog, mode):
    AppConfig.objects.create(pricing_engine_mode=mode)
    required, _ = catalog
    payload = {'event_date': '2026-12-02', 'start_time': '18:00', 'end_time': '21:00',
               'distance_km': '0', 'package_id': None, 'selected_item_ids': []}
    assert calculate_with_mode(payload, public=True).visible_result.gross_total == Decimal('20.00')
    private = PriceItem.objects.create(name='Private', price=Decimal('900'), is_public=False)
    from wishlist.pricing_v2.input import PricingValidationError
    with pytest.raises(PricingValidationError):
        calculate_with_mode({**payload, 'selected_item_ids': [str(private.pk)]}, public=True)


def test_nonpublic_legacy_workflow_with_no_package_keeps_prior_fallback(catalog):
    AppConfig.objects.create(pricing_engine_mode='legacy')
    payload = {'event_date': '2026-12-02', 'start_time': '18:00', 'end_time': '21:00',
               'package_id': None, 'selected_item_ids': []}
    result = calculate_with_mode(payload, public=False).visible_result
    assert result.gross_total == Decimal('500.00')


def test_individual_items_fail_when_profile_or_workflow_missing(catalog):
    from wishlist.pricing_v2.input import PricingValidationError
    AppConfig.objects.create(pricing_engine_mode='v2')
    payload = {'event_date': '2026-12-02', 'start_time': '18:00', 'end_time': '21:00',
               'package_id': None, 'selected_item_ids': [], 'distance_km': '0'}
    PricingProfile.objects.all().delete()
    with pytest.raises(PricingValidationError):
        calculate_with_mode(payload, public=True)
