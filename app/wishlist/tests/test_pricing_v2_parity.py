"""Admin preview and persisted drafts use the same authoritative catalog as public estimates."""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from wishlist.models import (AppConfig, Event, EventPriceCalculation, PriceCalculationRevision,
                             PriceItem, PricingFormula, PricingPackage, PricingProfile, PricingWorkflow)
from wishlist.pricing_v2.services import calculate_with_mode


@pytest.fixture
def catalog(db):
    config = AppConfig.load()
    package = PricingPackage.objects.create(name='Party', base_price=Decimal('100'))
    required = PriceItem.objects.create(name='Required', price=Decimal('20'), is_required=True)
    extra = PriceItem.objects.create(name='Extra', price=Decimal('30'))
    private = PriceItem.objects.create(name='<img src=x onerror=alert(1)>', price=Decimal('40'), is_public=False)
    PricingWorkflow.objects.create(name='Default', is_default=True, workflow_json=[
        {'type': 'package', 'config': {'package_id': package.pk}}, {'type': 'items'}])
    PricingProfile.objects.create(name='Live', status='active', valid_from=timezone.now(), config_json=dict(
        included_hours='3', extra_hour_rate='0', setup_minutes=0, teardown_minutes=0,
        setup_teardown_hour_rate='0', included_km='0', km_rate='0', saturday_percent='0',
        sunday_percent='0', holiday_percent='0', holiday_region='BY', seasons=[],
        lead_time_tiers=[], guest_tiers=[{'lower_bound': 0, 'upper_bound': None, 'value': '0'}],
        minimum_price='0', rounding_mode='nearest', rounding_step='0.01'))
    return config, package, required, extra, private


@pytest.fixture
def staff(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username='admin', password='x', is_staff=True))
    return client


def preview(staff, package, selected, **kwargs):
    body = dict(package_id=package.pk if package else None, selected_item_ids=selected,
                event_date='2999-06-20', start_time='18:00', end_time='21:00',
                distance_km='12', guest_count=10)
    body.update(kwargs)
    return staff.post(reverse('dj_admin:price_calculate'),
                      data=json.dumps(body), content_type='application/json', secure=True)


def save(staff, event, package, selected=None, **kwargs):
    body = dict(name='Gig', date='2999-06-20', time_start='18:00', time_end='21:00',
                location='X', guest_count='10', distance_km='12', status='inquiry',
                pricing_package_id=str(package.pk) if package else '',
                selected_items=selected or [], total_price='0.01')
    body.update(kwargs)
    with patch('wishlist.admin_views.GoogleService.backup_event_to_sheets'):
        return staff.post(reverse('dj_admin:event_edit', args=[event.pk]), body, secure=True)


@pytest.mark.parametrize('mode,version', [('legacy', '1'), ('shadow', '1'), ('v2', '2')])
def test_mode_switch_preview_and_save(staff, catalog, mode, version):
    config, package, required, extra, _ = catalog
    config.pricing_engine_mode = mode
    config.save()
    event = Event.objects.create(name='Gig', date=date(2999, 6, 20), location='X')
    response = preview(staff, package, [extra.pk])
    assert response.status_code == 200
    assert response.json()['engine_version'] == version
    assert response.json()['gross_total'] == ('150.00' if mode == 'v2' else '130.00')
    assert save(staff, event, package, [str(extra.pk)]).status_code == 302
    event.refresh_from_db()
    assert event.total_price == (Decimal('150.00') if mode == 'v2' else Decimal('0.01'))
    if mode == 'v2':
        revision = event.price_revisions.get(kind='draft')
        assert revision.result_json['gross_total'] == response.json()['gross_total']
        assert set(event.price_calculation.selected_items.values_list('pk', flat=True)) == {required.pk, extra.pk}
        assert calculate_with_mode(event=event).visible_result.gross_total == Decimal('150.00')
        assert save(staff, event, package, [str(extra.pk)]).status_code == 302
        assert event.price_revisions.filter(kind='draft').count() == 1
        revision.refresh_from_db()
        assert revision.result_json['gross_total'] == '150.00'
    elif mode == 'shadow':
        assert event.price_revisions.filter(kind='migration_compare').count() == 1
        save(staff, event, package, [str(extra.pk)])
        assert event.price_revisions.filter(kind='migration_compare').count() == 1


def test_public_admin_and_service_match_and_private_item_is_admin_only(staff, client, catalog):
    config, package, _, extra, private = catalog
    config.pricing_engine_mode = 'v2'
    config.save()
    public = client.post('/api/price-estimate/', data=json.dumps(dict(
        date='2999-06-20', time_start='18:00', time_end='21:00', distance_km='12',
        package_id=package.pk, selected_item_ids=[extra.pk], guest_count=10)), content_type='application/json', secure=True)
    assert public.json()['gross_total'] == preview(staff, package, [extra.pk]).json()['gross_total'] == '150.00'
    assert calculate_with_mode(dict(event_date='2999-06-20', start_time='18:00', end_time='21:00',
        distance_km='12', package_id=package.pk, selected_item_ids=[extra.pk], guest_count=10),
        public=True).visible_result.gross_total == Decimal('150.00')
    assert preview(staff, package, [private.pk]).json()['gross_total'] == '160.00'
    assert client.post('/api/price-estimate/', data=json.dumps(dict(date='2999-06-20',
        time_start='18:00', time_end='21:00', distance_km='12', package_id=package.pk,
        selected_item_ids=[private.pk])), content_type='application/json', secure=True).status_code == 400


def test_v2_missing_distance_invalid_selection_and_no_package_are_controlled(staff, catalog):
    config, package, _, extra, _ = catalog
    config.pricing_engine_mode = 'v2'
    config.save()
    assert preview(staff, package, [extra.pk], distance_km=None).json()['status'] == 'individual_review'
    assert preview(staff, package, [999999]).status_code == 400
    assert preview(staff, None, [extra.pk]).json()['gross_total'] == '50.00'
    event = Event.objects.create(name='Gig', date=date(2999, 6, 20), location='X')
    assert save(staff, event, None, [str(extra.pk)], distance_km='').status_code == 302
    event.refresh_from_db()
    assert event.total_price is None
    assert not event.price_revisions.filter(kind='draft').exists()
    assert save(staff, event, None, [str(extra.pk)]).status_code == 302
    event.refresh_from_db()
    assert event.total_price == Decimal('50.00')
    assert event.price_revisions.get(kind='draft').package_id_snapshot is None


def test_v2_draft_does_not_change_released_offer_or_event_status(staff, catalog):
    config, package, _, extra, _ = catalog
    config.pricing_engine_mode = 'v2'
    config.save()
    event = Event.objects.create(name='Gig', date=date(2999, 6, 20), location='X',
                                 total_price=Decimal('777'), status='confirmed')
    PriceCalculationRevision.objects.create(event=event, kind='offer', status='released',
        revision_number=1, engine_version='2', gross_total=Decimal('777'),
        input_json={}, result_json={'gross_total': '777.00'})
    assert save(staff, event, package, [str(extra.pk)], status='confirmed').status_code == 302
    event.refresh_from_db()
    assert event.total_price == Decimal('777')
    assert event.status == 'confirmed'
    assert event.price_revisions.get(kind='offer').gross_total == Decimal('777')
    assert event.price_revisions.filter(kind='draft').count() == 1


def test_released_total_survives_review_on_missing_distance(staff, catalog):
    config, package, _, extra, _ = catalog
    config.pricing_engine_mode = 'v2'
    config.save()
    event = Event.objects.create(name='Gig', date=date(2999, 6, 20), location='X',
                                 total_price=Decimal('777'))
    PriceCalculationRevision.objects.create(event=event, kind='offer', status='sent',
        revision_number=1, engine_version='2', gross_total=Decimal('777'),
        input_json={}, result_json={'gross_total': '777.00'})
    assert save(staff, event, package, [str(extra.pk)], distance_km='').status_code == 302
    event.refresh_from_db()
    assert event.total_price == Decimal('777')


def test_v2_invalid_saved_item_is_review_not_server_error(staff, catalog):
    config, package, _, _, _ = catalog
    config.pricing_engine_mode = 'v2'
    config.save()
    event = Event.objects.create(name='Gig', date=date(2999, 6, 20), location='X')
    assert save(staff, event, package, ['not-an-id']).status_code == 302
    event.refresh_from_db()
    assert event.total_price is None
    assert not event.price_revisions.exists()


def test_admin_custom_line_and_untrusted_catalog_label_are_not_public(staff, client, catalog):
    config, package, _, _, private = catalog
    config.pricing_engine_mode = 'v2'
    config.save()
    custom = [{'code': 'transport', 'label': 'Transport', 'amount': '12.50'}]
    response = preview(staff, package, [private.pk], custom_items=custom)
    assert response.status_code == 200
    assert response.json()['gross_total'] == '172.50'
    assert any(line['label'] == private.name for line in response.json()['lines'])
    public = client.post('/api/price-estimate/', data=json.dumps(dict(date='2999-06-20',
        time_start='18:00', time_end='21:00', distance_km='12', package_id=package.pk,
        selected_item_ids=[], custom_items=custom)), content_type='application/json', secure=True)
    assert public.json()['gross_total'] == '120.00'
    assert not any(line['label'] == 'Transport' for line in public.json()['lines'])


def test_v2_test_context_rejected_with_validation(staff, catalog):
    config, package, _, _, _ = catalog
    config.pricing_engine_mode = 'v2'
    config.save()
    response = preview(staff, package, [], test_context={'guest_count': 500})
    assert response.status_code == 400
    assert 'test_context' in response.json()['error']


def test_v2_valid_retry_after_review_restores_total_without_rewriting_draft(staff, catalog):
    config, package, _, extra, _ = catalog
    config.pricing_engine_mode = 'v2'
    config.save()
    event = Event.objects.create(name='Gig', date=date(2999, 6, 20), location='X')
    assert save(staff, event, package, [str(extra.pk)]).status_code == 302
    first = event.price_revisions.get(kind='draft')
    snapshot = first.result_json.copy()
    event.refresh_from_db()
    assert event.total_price == Decimal('150.00')

    assert save(staff, event, package, [str(extra.pk)], distance_km='').status_code == 302
    event.refresh_from_db()
    assert event.total_price is None
    assert save(staff, event, package, [str(extra.pk)]).status_code == 302
    event.refresh_from_db()
    first.refresh_from_db()
    assert event.total_price == Decimal('150.00')
    assert event.price_revisions.filter(kind='draft').count() == 1
    assert first.result_json == snapshot


@pytest.mark.parametrize('mode', ['legacy', 'shadow'])
def test_existing_event_preview_prices_edited_fields_without_persisting(staff, catalog, mode):
    config, package, _, extra, _ = catalog
    config.pricing_engine_mode = mode
    config.save()
    formula = PricingFormula.objects.create(name='Form fields',
        expression='guests + hours + distance + base', mode='replace')
    workflow = PricingWorkflow.objects.get(is_default=True)
    workflow.workflow_json.append({'type': 'formula'})
    workflow.save()
    event = Event.objects.create(name='Gig', date=date(2999, 6, 19),
        time_start='10:00', time_end='11:00', guest_count=1,
        distance_km=Decimal('1'), event_type='old', location='X')
    response = preview(staff, package, [extra.pk], event_id=event.pk, formula_id=formula.pk,
        event_date='2999-06-20', start_time='18:00', end_time='21:00',
        guest_count=10, distance_km='12', event_type='new')
    assert response.status_code == 200
    assert response.json()['engine_version'] == '1'
    assert response.json()['gross_total'] == '155.00'  # V1: 130 package/items + 10 + 3 + 12
    event.refresh_from_db()
    assert event.date == date(2999, 6, 19)
    assert event.guest_count == 1
    assert event.distance_km == Decimal('1')
    assert event.event_type == 'old'
    assert event.total_price is None
    assert not event.price_revisions.exists()


@pytest.mark.parametrize('mode', ['legacy', 'shadow'])
def test_existing_event_preview_applies_edited_type_and_date_rules(staff, catalog, mode):
    from wishlist.models import PricingRule

    config, package, _, extra, _ = catalog
    config.pricing_engine_mode = mode
    config.save()
    workflow = PricingWorkflow.objects.get(is_default=True)
    workflow.workflow_json.append({'type': 'rules'})
    workflow.save()
    PricingRule.objects.create(name='Thursday new type', effect_type='flat_add',
        effect_value=Decimal('7'), condition_json=[
            {'field': 'date_weekday', 'op': 'eq', 'value': 3},
            {'field': 'event_type', 'op': 'eq', 'value': 'new'}])
    event = Event.objects.create(name='Gig', date=date(2999, 6, 19),
        time_start='10:00', time_end='11:00', guest_count=1,
        distance_km=Decimal('1'), event_type='old', location='X')
    response = preview(staff, package, [extra.pk], event_id=event.pk,
        event_date='2999-06-20', event_type='new')
    assert response.status_code == 200
    assert response.json()['gross_total'] == '137.00'
    event.refresh_from_db()
    assert event.date == date(2999, 6, 19)
    assert event.event_type == 'old'
    assert not event.price_revisions.exists()


@pytest.mark.parametrize('mode', ['legacy', 'shadow'])
def test_existing_event_preview_retains_test_context_override(staff, catalog, mode):
    config, package, _, extra, _ = catalog
    config.pricing_engine_mode = mode
    config.save()
    formula = PricingFormula.objects.create(name='Test guest override', expression='guests + base')
    workflow = PricingWorkflow.objects.get(is_default=True)
    workflow.workflow_json.append({'type': 'formula'})
    workflow.save()
    event = Event.objects.create(name='Gig', date=date(2999, 6, 19),
        time_start='10:00', time_end='11:00', guest_count=1, distance_km=Decimal('1'), location='X')
    response = preview(staff, package, [extra.pk], event_id=event.pk, formula_id=formula.pk,
        guest_count=10, test_context={'guest_count': 25})
    assert response.status_code == 200
    assert response.json()['gross_total'] == '155.00'
    event.refresh_from_db()
    assert event.guest_count == 1
    assert not event.price_revisions.exists()
