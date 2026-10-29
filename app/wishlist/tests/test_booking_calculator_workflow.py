"""Booking prices must be authored as ordinary calculator components."""
import json
from datetime import date, time
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.cache import cache
from django.utils import timezone

from wishlist.models import (AppConfig, Event, PricingPackage, PricingFormula, PricingRule,
                             PricingWorkflow, PricingProfile, PriceItem)
from wishlist.price_engine import SafeFormulaEvaluator, FORMULA_VARS
from wishlist.pricing_v2.services import calculate_with_mode
from wishlist.tests.test_pricing_v2_profile_admin import CONFIG


@pytest.fixture
def basis(db):
    call_command('setup_basis_package')
    return PricingPackage.objects.get(name='Basis-Paket')


def payload(package, **changes):
    values = dict(event_date='2027-01-04', start_time='18:00', end_time='00:30',
                  guest_count=70, with_sound=False, distance_km='60',
                  package_id=str(package.pk), selected_item_ids=[])
    values.update(changes)
    return values


@pytest.mark.parametrize('expression,want', [
    ('max(0, ceil((duration_minutes - 360) / 60)) * 60', '60'),
    ('max(0, 2 * distance - 100) * 0.30', '6.00'),
])
def test_general_formula_functions(expression, want):
    variables = {key: 0 for key in FORMULA_VARS}
    variables.update(duration_minutes=390, distance=60)
    assert SafeFormulaEvaluator(FORMULA_VARS).evaluate(expression, variables) == Decimal(want)


@pytest.mark.parametrize('expression', [
    '__import__("os")', 'ceil()', 'max(1)', 'ceil(1, 2)', 'max(*[1,2])',
    'max(a=1, b=2)', '(1).__class__', '1 if guests > 0 else __import__("os")',
])
def test_formula_whitelist_rejects_unsafe_calls_including_unused_branches(expression):
    with pytest.raises(ValueError):
        SafeFormulaEvaluator(FORMULA_VARS).evaluate(expression, {key: 1 for key in FORMULA_VARS})


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'v2'])
@pytest.mark.parametrize('guests,sound,end,km,want', [
    (50, True, '00:00', '50', '660'), (51, True, '00:00', '50', '750'),
    (50, False, '00:00', '50', '580'), (51, False, '00:00', '50', '670'),
    (70, False, '00:30', '60', '736'), (50, True, '00:01', '50', '720'),
    (50, True, '01:00', '50', '720'), (50, True, '01:01', '50', '780'),
    (50, True, '23:00', '0', '660'), (50, True, '00:00', '50.5', '660.30'),
])
def test_stored_workflow_prices_every_factor(basis, mode, guests, sound, end, km, want):
    from copy import deepcopy
    AppConfig.objects.update_or_create(pk=1, defaults={'pricing_engine_mode': mode})
    PricingProfile.objects.create(name='Live', status='active', valid_from=timezone.now(), config_json=deepcopy(CONFIG))
    # A conflicting default must never be picked instead of the package's workflow.
    PricingWorkflow.objects.create(name='Unrelated', is_default=True,
        workflow_json=[{'type': 'package'}, {'type': 'items'}, {'type': 'rules'}])
    PricingRule.objects.create(name='Unrelated surcharge', effect_type='flat_add', effect_value='999')
    result = calculate_with_mode(payload(basis, guest_count=guests, with_sound=sound,
        end_time=end, distance_km=km), public=True)
    assert result.visible_result.gross_total == Decimal(want)
    if mode == 'shadow':
        assert result.v2_result.gross_total == Decimal(want)


def test_setup_creates_real_components_preserves_admin_edits_and_other_entries(basis):
    assert not hasattr(basis, 'pricing_config')
    assert basis.workflow_id is not None
    workflow = basis.workflow
    assert [b['type'] for b in workflow.workflow_json] == ['package', 'items', 'rules', 'formula']
    rules_block = workflow.workflow_json[2]
    rules = PricingRule.objects.filter(pk__in=rules_block['config']['rule_ids'])
    assert rules.count() == 2
    assert sorted(rules.values_list('effect_value', flat=True)) == [Decimal('-80'), Decimal('90')]
    formula = PricingFormula.objects.get(pk=workflow.workflow_json[3]['config']['formula_id'])
    assert formula.mode == 'add'
    assert 'ceil(' in formula.expression
    assert basis.included_items.count() == 3
    basis.base_price = '700'
    basis.save()
    call_command('setup_basis_package')
    basis.refresh_from_db()
    assert basis.base_price == Decimal('700')
    assert PricingPackage.objects.filter(name='Basis-Paket').count() == 1
    assert PricingFormula.objects.count() == 1


def test_renaming_package_does_not_change_workflow_price(basis):
    basis.name = 'Beliebiger neuer Name'
    basis.save()
    assert calculate_with_mode(payload(basis), public=True).visible_result.gross_total == Decimal('736')


def test_basis_rules_do_not_change_unrelated_packages(basis):
    other = PricingPackage.objects.create(name='Unrelated', base_price='100')
    assert calculate_with_mode(payload(other, distance_km='0'), public=True).visible_result.gross_total == Decimal('100')


def test_cloned_package_can_share_its_configured_workflow(basis):
    clone = PricingPackage.objects.create(name='Kopie', base_price='660', workflow=basis.workflow)
    assert calculate_with_mode(payload(clone), public=True).visible_result.gross_total == Decimal('736')


def test_linked_workflow_honors_percentage_rule_basis(basis):
    tech = PriceItem.objects.create(name='Technology', category='tech', price='100')
    rule = PricingRule.objects.create(name='Tech adjustment', effect_type='percent_add', effect_value='10', applies_to='tech')
    workflow = basis.workflow
    workflow.workflow_json = [{'type': 'package'}, {'type': 'items'},
        {'type': 'rules', 'config': {'rule_ids': [rule.pk]}}]
    workflow.save()
    assert calculate_with_mode(payload(basis, selected_item_ids=[tech.pk]), public=False).visible_result.gross_total == Decimal('770')


def test_v2_validates_unused_formula_branches_and_normalizes_comparisons():
    from wishlist.pricing_v2.advanced import _evaluate
    from wishlist.pricing_v2.input import PricingValidationError
    with pytest.raises(PricingValidationError):
        _evaluate('1 if guests > 0 else __import__("os")', {'guests': Decimal('70')})
    assert _evaluate('guests >= 51', {'guests': Decimal('70')}) == Decimal('1')


def test_direct_admin_calculation_uses_package_workflow(basis):
    from wishlist.price_engine import PriceEngine
    event = Event(date=date(2027, 1, 4), time_start=time(18), time_end=time(0, 30),
        guest_count=70, with_sound=False, distance_km=Decimal('60'))
    assert PriceEngine.calculate(event, package_id=basis.pk)['grand_total'] == Decimal('736')


def test_configured_but_inactive_workflow_does_not_fall_back_to_global_rules(basis):
    basis.workflow.is_active = False
    basis.workflow.save()
    with pytest.raises(ValueError):
        calculate_with_mode(payload(basis), public=True)


def test_missing_formula_never_becomes_partial_price(basis):
    formula = PricingFormula.objects.get(pk=basis.workflow.workflow_json[3]['config']['formula_id'])
    formula.is_active = False
    formula.save()
    with pytest.raises(ValueError):
        calculate_with_mode(payload(basis), public=True)


def test_broken_formula_in_linked_workflow_never_returns_partial_price(basis):
    formula = PricingFormula.objects.get(pk=basis.workflow.workflow_json[3]['config']['formula_id'])
    formula.expression = 'unknown_variable + 1'
    formula.save()
    with pytest.raises(ValueError):
        calculate_with_mode(payload(basis), public=True)


def test_v2_component_editor_accepts_negative_flat_rule(client, basis):
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    client.force_login(get_user_model().objects.create_user('v2-editor', is_staff=True))
    ids = basis.workflow.workflow_json[2]['config']['rule_ids']
    rule = PricingRule.objects.get(pk__in=ids, effect_value=-80)
    response = client.put(reverse('dj_admin:pricing_v2_component_detail', kwargs={'pk': rule.pk}),
        data=json.dumps({'kind': 'rules', 'effect_value': '-85'}), content_type='application/json')
    assert response.status_code == 200
    rule.refresh_from_db()
    assert rule.effect_value == Decimal('-85')


def test_rule_editor_preserves_hidden_workflow_scope(client, basis):
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    client.force_login(get_user_model().objects.create_user('scope-editor', is_staff=True))
    html = client.get(reverse('dj_admin:workflow_builder')).content.decode()
    assert 'editingRuleScope' in html
    assert "condition_json:editingRuleScope.concat(" in html


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'v2'])
def test_booking_completion_has_no_optional_or_hidden_required_extras(client, basis, mode):
    from copy import deepcopy
    AppConfig.objects.update_or_create(pk=1, defaults={'pricing_engine_mode': mode})
    PricingProfile.objects.create(name='Live', status='active', valid_from=timezone.now(), config_json=deepcopy(CONFIG))
    extra = PriceItem.objects.create(name='Nicht angefragter Zuschlag', category='extra',
        price='999', is_required=True, is_default=True, is_public=True)
    html = client.get('/buchen/').content.decode()
    completion = html.split('id="step-5"', 1)[1].split('id="success-view"', 1)[0]
    assert 'Anfrage abschließen' in completion
    assert 'Zusatzoptionen' not in completion
    assert 'price-checkbox' not in completion
    assert extra.name not in completion
    with patch('wishlist.booking_distance.resolve_booking_distance', return_value=Decimal('60')):
        response = client.post('/api/price-estimate/', data=json.dumps(dict(
            date='2027-01-04', time_start='18:00', time_end='00:30', guest_count=70,
            with_sound=False, package_id=basis.pk, selected_item_ids=[],
            address_street='Teststraße 1', address_zip='45127', address_city='Essen')),
            content_type='application/json')
    assert response.json()['gross_total'] == '736.00'
    extra.refresh_from_db()
    assert extra.is_required and extra.price == Decimal('999')


def test_calculated_amount_has_separate_review_notice_in_both_steps(client, basis):
    html = client.get('/buchen/').content.decode()
    for amount_id, notice_id in [('package-price-total', 'package-price-review'), ('price-total', 'price-review')]:
        assert f'id="{notice_id}"' in html
        assert html.index(f'id="{amount_id}"') < html.index(f'id="{notice_id}"')
        notice = html.split(f'id="{notice_id}"', 1)[1].split('</p>', 1)[0]
        assert 'Preis wird individuell geprüft' in notice


def test_step_four_quote_is_above_tech_options_in_embedded_form(client, basis):
    html = client.get('/buchen/?embed=1').content.decode()
    step = html.split('id="step-4"', 1)[1].split('id="step-5"', 1)[0]
    assert step.index('id="package-price-total"') < step.index('aria-label="Tontechnik"')
    assert step.index('id="package-price-total"') < step.index('id="package-price-breakdown"')


def test_unused_preselection_playlist_is_not_exposed_in_event_or_admin_form(client, db):
    from wishlist.models import Event
    assert 'spotify_preselection_playlist_id' not in [field.name for field in Event._meta.fields]
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    client.force_login(get_user_model().objects.create_user('event-field-check', is_staff=True))
    html = client.get(reverse('dj_admin:event_create')).content.decode()
    assert 'spotify_preselection_playlist_id' not in html


def test_admin_rule_change_alters_public_quote_without_code_change(client, basis):
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    client.force_login(get_user_model().objects.create_user('calculator-editor', is_staff=True))
    ids = basis.workflow.workflow_json[2]['config']['rule_ids']
    rule = PricingRule.objects.get(pk__in=ids, effect_value=90)
    response = client.put(reverse('dj_admin:pricing_rule_detail', kwargs={'pk': rule.pk}),
        data=json.dumps({'effect_value': '100'}), content_type='application/json')
    assert response.status_code == 200
    data = payload(basis)
    with patch('wishlist.booking_distance.resolve_booking_distance', return_value=Decimal('60')):
        response = client.post('/api/price-estimate/', data=json.dumps(dict(
            date=data['event_date'], time_start=data['start_time'], time_end=data['end_time'],
            address_street='Teststraße 1', address_zip='45127', address_city='Essen',
            guest_count=70, with_sound=False, distance_km='60', package_id=basis.pk, selected_item_ids=[])),
            content_type='application/json')
    assert response.status_code == 200
    assert response.json()['gross_total'] == '746.00'


def test_admin_preview_selects_same_package_workflow_and_sound_option(client, basis):
    from django.contrib.auth import get_user_model
    from django.urls import reverse
    client.force_login(get_user_model().objects.create_user('preview-editor', is_staff=True))
    PricingWorkflow.objects.create(name='Other default', is_default=True, workflow_json=[{'type': 'package'}])
    data = payload(basis)
    response = client.post(reverse('dj_admin:price_calculate'), data=json.dumps(data), content_type='application/json')
    assert response.status_code == 200
    assert response.json()['gross_total'] == '736.00'


def test_submission_uses_same_workflow_and_included_items_are_not_charged_twice(client, basis):
    from datetime import timedelta
    cache.clear()
    data = payload(basis, event_date=(timezone.now().date() + timedelta(days=65)).isoformat())
    selected = list(basis.included_items.values_list('pk', flat=True))
    extra = PriceItem.objects.create(name='Test-Zusatzleistung', price='25', is_public=True)
    selected.append(extra.pk)
    expected = calculate_with_mode({**data, 'selected_item_ids': selected}, public=True).visible_result.gross_total
    with patch('wishlist.booking_distance.resolve_booking_distance', return_value=Decimal('60')), patch('wishlist.views.GoogleService.check_date_available', return_value=(True, [])), patch('wishlist.views.threading.Thread'):
        response = client.post('/api/submit-event/', data=json.dumps(dict(
            date=data['event_date'], time_start=data['start_time'], time_end=data['end_time'],
            guest_count=70, with_sound=False, distance_km='60', package_id=basis.pk, selected_items=selected,
            address_street='Teststraße 1', address_zip='45127', address_city='Essen',
            name='Test', client_name='Gast', client_email='test@example.com', location='Test', privacy_consent=True)),
            content_type='application/json')
    assert response.status_code == 200
    event = Event.objects.get(pk=response.json()['event_id'])
    assert event.total_price == expected == Decimal('736')
    assert not event.price_calculation.selected_items.exists()
    assert event.price_calculation.workflow_id == basis.workflow_id
