"""JSON bodies deliver decimals as floats; the V2 boundary must normalise them.

The pricing_v2 core deliberately rejects floats (see
test_pricing_v2_contracts.test_float_decimal_inputs_are_rejected) so money never
inherits binary float error. That contract stays, but callers that parse JSON
cannot honour it on their own: json.loads turns 40.5 into a float. Normalisation
therefore belongs at the service boundary.
"""
import copy

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from wishlist.models import PricingPackage, PricingProfile, PricingWorkflow
from wishlist.pricing_v2.input import PricingValidationError
from wishlist.pricing_v2.services import calculate_payload
from wishlist.tests.test_pricing_v2_profile_admin import CONFIG


@pytest.fixture
def setup(db):
    PricingProfile.objects.create(name='Global', version=1, status='active',
                                  valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    package = PricingPackage.objects.create(name='Basis', base_price=1000)
    workflow = PricingWorkflow.objects.create(name='Vollstaendig', workflow_json=[
        {'type': 'package', 'config': {'package_id': package.pk}},
        *[{'type': name} for name in ('duration', 'setup_teardown', 'travel', 'guest_tiers',
                                      'calendar_factors', 'discount', 'minimum_price', 'rounding')]])
    actor = get_user_model().objects.create_user('float-boundary-staff', is_staff=True)
    return package, workflow, actor


def body(setup, **overrides):
    package, workflow, _ = setup
    payload = {'event_date': '2026-12-05', 'start_time': '18:00', 'end_time': '23:00',
               'guest_count': 70, 'event_type': 'hochzeit', 'package_id': str(package.pk),
               'workflow_id': str(workflow.pk), 'selected_item_ids': [], 'distance_km': '30'}
    payload.update(overrides)
    return payload


def gross(setup, **overrides):
    _, _, actor = setup
    return calculate_payload(body(setup, **overrides), public=False, actor=actor).gross_total


@pytest.mark.django_db
def test_json_float_distance_matches_the_string_equivalent(setup):
    # Guard: distance has to move the price, otherwise the equality below is vacuous.
    assert gross(setup, distance_km='40.5') != gross(setup, distance_km='25')
    assert gross(setup, distance_km=40.5) == gross(setup, distance_km='40.5')


@pytest.mark.django_db
def test_json_float_discount_percent_matches_the_string_equivalent(setup):
    assert gross(setup, discount_percent='12.5') != gross(setup, discount_percent='0')
    assert gross(setup, discount_percent=12.5) == gross(setup, discount_percent='12.5')


@pytest.mark.django_db
@pytest.mark.parametrize('field', ['distance_km', 'discount_percent', 'discount_amount'])
def test_booleans_are_still_rejected(setup, field):
    with pytest.raises(PricingValidationError) as error:
        gross(setup, **{field: True})
    assert (error.value.code, error.value.field) == ('invalid_input', field)
