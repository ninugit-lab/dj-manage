"""Booking submissions persist server-authoritative, public pricing."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone
from django.urls import reverse

from wishlist.models import (AppConfig, Event, EventPriceCalculation, PriceCalculationRevision,
                             PriceItem, PricingPackage, PricingProfile, PricingWorkflow)


@pytest.fixture
def stock(db):
    cache.clear()
    config = AppConfig.load()
    package = PricingPackage.objects.create(name="Party", version=2, base_price=Decimal("100"))
    required = PriceItem.objects.create(name="Required", category="extra", price=Decimal("20"), is_required=True)
    extra = PriceItem.objects.create(name="Extra", category="extra", price=Decimal("30"))
    private = PriceItem.objects.create(name="Private", category="extra", price=Decimal("999"), is_public=False)
    inactive = PriceItem.objects.create(name="Inactive", category="extra", price=Decimal("999"), is_active=False)
    workflow = PricingWorkflow.objects.create(name="Default", version=4, is_default=True,
        workflow_json=[{"type": "package", "config": {"package_id": package.pk}}, {"type": "items"}])
    profile = PricingProfile.objects.create(name="Live", version=3, status="active", valid_from=timezone.now(),
        config_json=dict(included_hours="3", extra_hour_rate="0", setup_minutes=0, teardown_minutes=0,
                         setup_teardown_hour_rate="0", included_km="0", km_rate="0", saturday_percent="0",
                         sunday_percent="0", holiday_percent="0", holiday_region="BY", seasons=[],
                         lead_time_tiers=[], guest_tiers=[{"lower_bound": 0, "upper_bound": None, "value": "0"}],
                         minimum_price="0", rounding_mode="nearest", rounding_step="0.01"))
    return config, package, required, extra, private, inactive, workflow, profile


def payload(stock, **changes):
    body = dict(name="Party", date=(timezone.now().date() + timedelta(days=40)).isoformat(),
                time_start="18:00", time_end="21:00", location="Duisburg", client_name="Max",
                client_email="max@example.com", privacy_consent=True, distance_km="12",
                package_id=stock[1].pk, selected_items=[stock[3].pk], guest_count=10,
                grand_total="0.01", total_price="0.01")
    body.update(changes)
    return body


def post(client, body):
    with patch('wishlist.views.GoogleService.check_date_available', return_value=(True, [])), patch('wishlist.views.threading.Thread'):
        return client.post(reverse('submit_event'), data=json.dumps(body), content_type='application/json')


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'v2'])
def test_submission_ignores_browser_total_restores_required_and_respects_mode(client, stock, mode):
    stock[0].pricing_engine_mode = mode
    stock[0].save()
    response = post(client, payload(stock))
    assert response.status_code == 200
    assert response.json()['success'] is True
    event = Event.objects.get(pk=response.json()['event_id'])
    calc = EventPriceCalculation.objects.get(event=event)
    assert set(calc.selected_items.values_list('pk', flat=True)) == {stock[2].pk, stock[3].pk}
    assert event.total_price == Decimal('150.00')
    if mode == 'legacy':
        assert not event.price_revisions.exists()
    else:
        revision = event.price_revisions.get()
        assert revision.kind == ('estimate' if mode == 'v2' else 'migration_compare')
        assert revision.revision_number == 1
        assert revision.status == 'draft'
        assert revision.engine_version == '2'
        assert (revision.profile_id_snapshot, revision.profile_version) == (stock[7].pk, 3)
        assert (revision.package_id_snapshot, revision.package_version) == (stock[1].pk, 2)
        assert (revision.workflow_id_snapshot, revision.workflow_version) == (stock[6].pk, 4)
        assert revision.result_json['inputs'] == revision.input_json
        assert revision.input_json['selected_item_ids'] == [str(stock[3].pk), str(stock[2].pk)]
        assert revision.result_json['gross_total'] == '150.00'
        assert 'grand_total' not in revision.input_json
        assert revision.result_json['gross_total'] != '0.01'
        assert revision.legacy_total == (Decimal('150.00') if mode == 'shadow' else None)
    if mode != 'v2':
        assert calc.calculation_snapshot['grand_total'] == '150.00'


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'v2'])
@pytest.mark.parametrize('selection', ['private', 'inactive', 'package'])
def test_private_or_inactive_selection_is_rejected_before_event_creation(client, stock, mode, selection):
    stock[0].pricing_engine_mode = mode
    stock[0].save()
    if selection == 'package':
        stock[1].is_public = False
        stock[1].save()
        body = payload(stock)
    else:
        body = payload(stock, selected_items=[stock[4 if selection == 'private' else 5].pk])
    response = post(client, body)
    assert response.status_code == 400
    assert response.json()['success'] is False
    assert not Event.objects.exists()


@pytest.mark.parametrize('value', ['-1', 'not-a-number', 'NaN', True])
def test_invalid_distance_is_public_safe_and_creates_no_event(client, stock, value):
    response = post(client, payload(stock, distance_km=value))
    assert response.status_code == 400
    assert not Event.objects.exists()
    assert 'Traceback' not in response.content.decode()


def test_v2_preview_and_submission_have_same_frozen_economics(client, stock):
    stock[0].pricing_engine_mode = 'v2'
    stock[0].save()
    body = payload(stock)
    preview = client.post('/api/price-estimate/', data=json.dumps({
        **body, 'selected_item_ids': body['selected_items']}), content_type='application/json').json()
    response = post(client, body)
    event = Event.objects.get(pk=response.json()['event_id'])
    revision = event.price_revisions.get()
    assert preview['gross_total'] == revision.result_json['gross_total'] == '150.00'
    assert [(line['code'], line['amount']) for line in preview['lines']] == [
        (line['code'], line['amount']) for line in revision.result_json['lines']]
    assert revision.input_json['start_time'] == '18:00:00'
    assert Decimal(revision.input_json['distance_km']) == Decimal('12')
    assert isinstance(revision.result_json['gross_total'], str)
    def no_float(value):
        if isinstance(value, dict):
            return all(no_float(item) for item in value.values())
        if isinstance(value, list):
            return all(no_float(item) for item in value)
        return not isinstance(value, float)
    assert no_float(revision.result_json)
    assert no_float(revision.input_json)


def test_v2_pricing_failure_preserves_inquiry_without_price(client, stock):
    stock[0].pricing_engine_mode = 'v2'
    stock[0].save()
    with patch('wishlist.pricing_v2.services.calculate_with_mode', side_effect=RuntimeError('secret internals')):
        response = post(client, payload(stock))
    assert response.status_code == 200
    assert response.json()['success'] is True
    assert 'secret' not in response.content.decode()
    event = Event.objects.get(pk=response.json()['event_id'])
    assert event.status == 'inquiry'
    assert event.total_price is None
    assert not event.price_revisions.exists()


def test_separate_valid_posts_are_separate_inquiries_not_revisions_of_one(client, stock):
    stock[0].pricing_engine_mode = 'v2'
    stock[0].save()
    first = post(client, payload(stock)).json()['event_id']
    second = post(client, payload(stock)).json()['event_id']
    assert first != second
    assert [Event.objects.get(pk=pk).price_revisions.count() for pk in (first, second)] == [1, 1]


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'v2'])
def test_individual_items_without_package_keep_required_and_no_workflow_package(client, stock, mode):
    stock[0].pricing_engine_mode = mode
    stock[0].save()
    response = post(client, payload(stock, package_id=None))
    event = Event.objects.get(pk=response.json()['event_id'])
    assert event.total_price == Decimal('50.00')
    assert event.price_calculation.package is None
    assert set(event.price_calculation.selected_items.values_list('pk', flat=True)) == {stock[2].pk, stock[3].pk}
    assert event.price_revisions.count() == (0 if mode == 'legacy' else 1)


def test_shadow_pricing_failure_does_not_replace_v1_snapshot_or_total(client, stock):
    stock[0].pricing_engine_mode = 'shadow'
    stock[0].save()
    with patch('wishlist.pricing_v2.services.calculate_payload', side_effect=RuntimeError('internal')):
        response = post(client, payload(stock))
    event = Event.objects.get(pk=response.json()['event_id'])
    assert event.total_price == Decimal('150.00')
    assert event.price_calculation.calculation_snapshot['grand_total'] == '150.00'
    assert event.price_revisions.count() == 1
    assert event.price_revisions.get().kind == 'migration_compare'


def test_same_event_shadow_retry_reuses_existing_comparison(client, stock):
    from wishlist.pricing_v2.services import calculate_with_mode
    stock[0].pricing_engine_mode = 'shadow'
    stock[0].save()
    event = Event.objects.get(pk=post(client, payload(stock)).json()['event_id'])
    chosen = [str(stock[3].pk), str(stock[2].pk)]
    for _ in range(2):
        calculate_with_mode(dict(event_date=event.date, start_time=event.time_start,
            end_time=event.time_end, guest_count=event.guest_count, event_type=event.event_type,
            distance_km=event.distance_km, package_id=str(stock[1].pk),
            selected_item_ids=chosen), event=event, public=True, persist_comparison=True,
            comparison_scope='submission')
    assert event.price_revisions.count() == 1


@pytest.mark.parametrize('bad', [{'date': []}, {'time_start': []}, {'time_end': {}},
                                  {'guest_count': '-3'}, {'selected_items': [True]}, {'package_id': True}])
def test_malformed_structured_inputs_do_not_leak_or_create_event(client, stock, bad):
    response = post(client, payload(stock, **bad))
    assert response.status_code == 400
    assert not Event.objects.exists()


def test_google_task_runs_only_after_saved_pricing(client, stock):
    stock[0].pricing_engine_mode = 'v2'
    stock[0].save()
    observed = []

    def immediate_thread(*, target, daemon):
        class Thread:
            def start(self):
                event = Event.objects.get()
                observed.append((event.total_price, event.price_revisions.count()))
        return Thread()

    with patch('wishlist.views.GoogleService.check_date_available', return_value=(True, [])), patch('wishlist.views.threading.Thread', side_effect=immediate_thread):
        response = client.post(reverse('submit_event'), data=json.dumps(payload(stock)), content_type='application/json')
    assert response.status_code == 200
    assert observed == [(Decimal('150.00'), 1)]


def test_same_event_estimate_retry_ignores_timestamp_but_not_changed_economics(client, stock):
    from wishlist.pricing_v2.services import calculate_with_mode, save_revision
    stock[0].pricing_engine_mode = 'v2'
    stock[0].save()
    event = Event.objects.get(pk=post(client, payload(stock)).json()['event_id'])
    first = event.price_revisions.get()
    chosen = [str(stock[3].pk), str(stock[2].pk)]
    request = dict(event_date=event.date, start_time=event.time_start, end_time=event.time_end,
                   guest_count=event.guest_count, event_type=event.event_type,
                   distance_km=event.distance_km, package_id=str(stock[1].pk), selected_item_ids=chosen)
    later = calculate_with_mode(request, event=event, public=True).visible_result
    assert first.input_json['requested_at'] != later.to_dict()['inputs']['requested_at']
    retry = save_revision(event, later, kind=PriceCalculationRevision.Kind.ESTIMATE, update_event_total=True)
    assert retry.pk == first.pk
    assert event.price_revisions.count() == 1
    stock[3].price = Decimal('35')
    stock[3].save(update_fields=['price'])
    changed = calculate_with_mode(request, event=event, public=True).visible_result
    new_revision = save_revision(event, changed, kind=PriceCalculationRevision.Kind.ESTIMATE, update_event_total=True)
    assert new_revision.pk != first.pk
    assert new_revision.revision_number == 2


def test_shadow_submission_retry_with_changed_catalog_reuses_original_comparison(client, stock):
    from wishlist.pricing_v2.services import calculate_with_mode
    stock[0].pricing_engine_mode = 'shadow'
    stock[0].save()
    event = Event.objects.get(pk=post(client, payload(stock)).json()['event_id'])
    first = event.price_revisions.get()
    stock[3].price = Decimal('35')
    stock[3].save(update_fields=['price'])
    request = dict(event_date=event.date, start_time=event.time_start, end_time=event.time_end,
                   guest_count=event.guest_count, event_type=event.event_type,
                   distance_km=event.distance_km, package_id=str(stock[1].pk),
                   selected_item_ids=[str(stock[3].pk), str(stock[2].pk)])
    changed = calculate_with_mode(request, event=event, public=True,
                                  persist_comparison=True, comparison_scope='submission')
    assert changed.v2_result.gross_total == Decimal('155')
    assert changed.comparison_revision.pk == first.pk
    assert event.price_revisions.count() == 1


def test_shadow_submission_success_then_failure_retry_reuses_original_comparison(client, stock):
    from wishlist.pricing_v2.services import calculate_with_mode
    stock[0].pricing_engine_mode = 'shadow'
    stock[0].save()
    event = Event.objects.get(pk=post(client, payload(stock)).json()['event_id'])
    first = event.price_revisions.get()
    request = dict(event_date=event.date, start_time=event.time_start, end_time=event.time_end,
                   guest_count=event.guest_count, event_type=event.event_type,
                   distance_km=event.distance_km, package_id=str(stock[1].pk),
                   selected_item_ids=[str(stock[3].pk), str(stock[2].pk)])
    with patch('wishlist.pricing_v2.services.calculate_payload', side_effect=RuntimeError('internal')):
        retried = calculate_with_mode(request, event=event, public=True,
                                      persist_comparison=True, comparison_scope='submission')
    assert retried.v2_result is None
    assert retried.comparison_revision.pk == first.pk
    assert event.price_revisions.count() == 1


def test_explicit_shadow_compare_is_separate_from_submission_sample(client, stock):
    from wishlist.pricing_v2.services import calculate_with_mode
    stock[0].pricing_engine_mode = 'shadow'
    stock[0].save()
    event = Event.objects.get(pk=post(client, payload(stock)).json()['event_id'])
    initial = event.price_revisions.get()
    request = dict(event_date=event.date, start_time=event.time_start, end_time=event.time_end,
                   guest_count=event.guest_count, event_type=event.event_type,
                   distance_km=event.distance_km, package_id=str(stock[1].pk),
                   selected_item_ids=[str(stock[3].pk), str(stock[2].pk)])
    explicit = calculate_with_mode(request, event=event, public=True, persist_comparison=True)
    assert explicit.comparison_revision.pk != initial.pk
    assert event.price_revisions.count() == 2


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'v2'])
def test_missing_distance_submission_remains_individual_review_like_preview(client, stock, mode):
    stock[0].pricing_engine_mode = mode
    stock[0].save()
    body = payload(stock, distance_km=None)
    preview = client.post('/api/price-estimate/', data=json.dumps({
        **body, 'selected_item_ids': body['selected_items']}), content_type='application/json').json()
    assert preview['status'] == 'individual_review'
    assert preview['gross_total'] is None
    response = post(client, body)
    assert response.status_code == 200
    assert response.json()['success'] is True
    assert 'gross_total' not in response.json()
    event = Event.objects.get(pk=response.json()['event_id'])
    assert event.status == 'inquiry'
    assert event.total_price is None
    assert not event.price_revisions.filter(kind='estimate').exists()
    assert 'grand_total' not in event.price_calculation.calculation_snapshot


@pytest.mark.parametrize('field', ['selected_items', 'package_id'])
def test_huge_digit_identifier_is_generic_400_without_event(client, stock, field):
    huge = '9' * 5000
    body = payload(stock, **{field: [huge] if field == 'selected_items' else huge})
    response = post(client, body)
    assert response.status_code == 400
    assert response.json() == {'success': False, 'error': 'Ungültige Anfrage'}
    assert not Event.objects.exists()
