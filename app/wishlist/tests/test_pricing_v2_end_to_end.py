"""Customer-to-offer and cutover boundaries against a single persisted fixture."""

import json
from datetime import timedelta, time
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from wishlist.models import (AppConfig, EmailLog, Event, EventStatus,
                             ManualPriceAdjustment, PriceCalculationRevision,
                             PricingReferenceCase)
from wishlist.pricing_v2.activation import activate_v2, rollback_to_legacy, validate_activation
from wishlist.pricing_v2.offers import add_adjustment, build_draft, release_offer, send_offer
from wishlist.pricing_v2.activation import _references
from wishlist.tests.test_pricing_v2_submission import payload, post, stock
from wishlist.tests.test_pricing_v2_parity import preview, staff
from wishlist.tests.test_pricing_v2_compare_command import comparison_data


def estimate(client, body):
    return client.post('/api/price-estimate/', data=json.dumps({
        **body, 'selected_item_ids': body['selected_items'],
    }), content_type='application/json', secure=True).json()


def test_activation_reference_scan_keeps_optional_ids_optional(stock):
    workflow = stock[6]
    refs = _references(workflow)
    assert refs['packages'] == {str(stock[1].pk)}
    assert refs['items'] == set()
    workflow.workflow_json.append({'type': 'rules', 'config': {'rule_ids': [123]}})
    assert _references(workflow)['rules'] == {'123'}


def test_sending_offer_keeps_event_total_during_admin_recalculation(staff, stock):
    config, package, _, extra, _, _, _, _ = stock
    config.pricing_engine_mode = 'v2'
    config.save(update_fields=['pricing_engine_mode'])
    event = Event.objects.create(name='Gig', date=timezone.localdate() + timedelta(days=40),
                                 location='X', time_start=time(18), time_end=time(21),
                                 status=EventStatus.INQUIRY)
    from wishlist.tests.test_pricing_v2_parity import save
    assert save(staff, event, package, [str(extra.pk)]).status_code == 302
    actor = get_user_model().objects.get(username='admin')
    assert build_draft(event, actor)
    offer = release_offer(event, actor)
    original = Event.objects.get(pk=event.pk).total_price
    PriceCalculationRevision.objects.filter(pk=offer.pk).update(status='sending')
    assert save(staff, event, package, [str(extra.pk)], distance_km='').status_code == 302
    assert Event.objects.get(pk=event.pk).total_price == original


def test_public_inquiry_admin_adjustment_offer_failed_send_and_retry(
        client, staff, stock, monkeypatch):
    config, package, required, extra, _, _, _, _ = stock
    config.pricing_engine_mode = 'v2'
    config.save(update_fields=['pricing_engine_mode'])
    body = payload(stock, client_email='original@example.test')
    public = estimate(client, body)
    submitted = post(client, body)
    assert submitted.status_code == 200
    event = Event.objects.get(pk=submitted.json()['event_id'])
    estimate_revision = event.price_revisions.get(kind='estimate')
    admin = preview(staff, package, [extra.pk], event_date=body['date'])
    assert public['gross_total'] == estimate_revision.result_json['gross_total'] == admin.json()['gross_total'] == '150.00'
    assert {required.pk, extra.pk} == set(event.price_calculation.selected_items.values_list('pk', flat=True))
    assert event.status == EventStatus.INQUIRY

    actor = get_user_model().objects.get(username='admin')
    adjustment = add_adjustment(event, 'Rabatt', '-10.00', 'Kulanz', actor)
    assert adjustment.reason == 'Kulanz' and adjustment.amount == Decimal('-10.00')
    draft = build_draft(event, actor)
    assert draft.gross_total == Decimal('140.00')
    released = release_offer(event, actor)
    assert released.pk == release_offer(event, actor).pk
    frozen = (released.input_json, released.result_json, released.email_recipient,
              released.email_subject, released.email_body)
    assert frozen[2] == 'original@example.test'
    assert Event.objects.get(pk=event.pk).status == EventStatus.INQUIRY

    event.client_email = 'changed@example.test'
    event.client_name = 'Changed'
    event.save(update_fields=['client_email', 'client_name'])
    config.offer_email_body = 'CHANGED {event_name}'
    config.save(update_fields=['offer_email_body'])
    deliveries = []
    def deliver(to, subject, message, **kwargs):
        deliveries.append((to, subject, message))
        if len(deliveries) == 1:
            raise RuntimeError('token=PRIVATE')
        return True, 'ok'
    monkeypatch.setattr('wishlist.google_services.GoogleService.send_email', deliver)
    failed = send_offer(released)
    assert failed.error == 'EMAIL_DELIVERY_FAILED'
    assert failed.revision.status == 'send_failed'
    assert send_offer(released, retry=True).sent is True
    released.refresh_from_db()
    assert released.status == 'sent'
    assert (released.input_json, released.result_json, released.email_recipient,
            released.email_subject, released.email_body) == frozen
    assert len(deliveries) == 2 and all(d == deliveries[0] for d in deliveries)
    assert EmailLog.objects.filter(revision=released).count() == 2
    assert 'PRIVATE' not in json.dumps(list(EmailLog.objects.values_list('error_message', flat=True)))
    assert event.price_revisions.filter(kind='offer').count() == 1
    assert ManualPriceAdjustment.objects.filter(event=event).count() == 1
    assert Event.objects.get(pk=event.pk).status == EventStatus.INQUIRY


def test_shadow_activation_and_rollback_keep_offers_and_switch_public_and_admin(
        client, staff, stock):
    config, package, _, extra, _, _, _, _ = stock
    body = payload(stock)
    legacy = estimate(client, body)
    assert legacy['engine_version'] == '1'
    assert post(client, body).status_code == 200
    assert not PriceCalculationRevision.objects.exists()
    config.pricing_engine_mode = 'shadow'
    config.save(update_fields=['pricing_engine_mode'])
    assert estimate(client, body)['engine_version'] == '1'
    assert not PriceCalculationRevision.objects.exists()
    submitted = post(client, body)
    shadow = Event.objects.get(pk=submitted.json()['event_id'])
    comparison = shadow.price_revisions.get(kind='migration_compare')
    assert comparison.result_json['gross_total'] == '150.00'
    assert comparison.difference == Decimal('0.00')

    PricingReferenceCase.objects.create(
        name='same-economics', result_json={'event_id': shadow.pk, 'v1_gross': '150.00', 'v2_gross': '150.00', 'difference': '0.00'},
        expected_v1_total=Decimal('150'), expected_v2_total=Decimal('150'), expected_difference=Decimal('0'),
    )
    report = validate_activation()
    assert report.ok, report.errors
    actor = get_user_model().objects.get(username='admin')
    activate_v2(actor, expected_reference_case_stamp=report.reference_case_stamp)
    assert estimate(client, body)['engine_version'] == '2'
    assert post(client, body).status_code == 200
    v2_event = Event.objects.order_by('-pk').first()
    assert v2_event.price_revisions.filter(kind='estimate').exists()
    assert preview(staff, package, [extra.pk], event_date=body['date']).json()['engine_version'] == '2'
    offer = release_offer(v2_event, actor) if build_draft(v2_event, actor) else None
    rollback_to_legacy(actor)
    assert estimate(client, body)['engine_version'] == '1'
    assert preview(staff, package, [extra.pk], event_date=body['date']).json()['engine_version'] == '1'
    assert comparison.pk == shadow.price_revisions.get(kind='migration_compare').pk
    assert v2_event.price_revisions.filter(pk=offer.pk, kind='offer').exists()
    assert AppConfig.load().pricing_engine_mode == 'legacy'


def test_activation_recalculates_linked_reference_after_catalog_change(stock):
    config, _, _, extra, _, _, _, _ = stock
    config.pricing_engine_mode = 'shadow'
    config.save(update_fields=['pricing_engine_mode'])
    from datetime import time
    from wishlist.models import EventPriceCalculation
    event = Event.objects.create(name='Case', date=timezone.localdate() + timedelta(days=40),
                                 location='X', time_start=time(18), time_end=time(21), distance_km=12)
    calc = EventPriceCalculation.objects.create(event=event, package=stock[1])
    calc.selected_items.add(extra)
    from wishlist.pricing_v2.migration import compare_event
    expected = compare_event(event, stock[7], stock[6])
    assert expected.status == 'ok', expected.diagnostics
    PricingReferenceCase.objects.create(name='baseline', result_json={
        'event_id': event.pk, 'v1_gross': str(expected.v1_gross),
        'v2_gross': str(expected.v2_gross), 'difference': str(expected.difference),
    }, expected_v1_total=expected.v1_gross, expected_v2_total=expected.v2_gross,
       expected_difference=expected.difference, tolerance_threshold=Decimal('100'))
    assert validate_activation().ok
    extra.price = Decimal('40')
    extra.save(update_fields=['price'])
    report = validate_activation()
    assert any(e['check'] == 'reference_case_0' for e in report.errors)


def test_stored_unlinked_reference_cannot_activate(stock):
    PricingReferenceCase.objects.create(name='invented', result_json={
        'v1_gross': '150.00', 'v2_gross': '150.00', 'difference': '0.00',
    }, expected_v1_total=Decimal('150'), expected_v2_total=Decimal('150'),
       expected_difference=Decimal('0'))
    assert any(e['check'] == 'reference_case_0' for e in validate_activation().errors)


def test_rollout_commands_dry_run_and_allowlisted_apply_preserve_event_and_offer(
        comparison_data, monkeypatch, capsys):
    event, _, _ = comparison_data
    event.date = timezone.localdate() + timedelta(days=40)
    event.status = EventStatus.CONFIRMED
    event.save(update_fields=['date', 'status'])
    released = PriceCalculationRevision.objects.create(
        event=event, kind='offer', status='released', engine_version='2', revision_number=1,
        offer_number=1, gross_total=Decimal('777.00'), result_json={'gross_total': '777.00'}, input_json={},
    )
    past = Event.objects.create(name='Past', date=timezone.localdate() - timedelta(days=1),
                                status=EventStatus.CONFIRMED, location='X', total_price=Decimal('42.00'))
    cancelled = Event.objects.create(name='Cancelled', date=event.date,
                                     status=EventStatus.CANCELLED, location='X', total_price=Decimal('43.00'))
    before = (event.total_price, released.result_json.copy())
    call_command('pricing_v2_compare')
    compare = json.loads(capsys.readouterr().out)
    call_command('pricing_v2_reprice')
    reprice = json.loads(capsys.readouterr().out)
    assert compare['summary']['applied'] is False
    assert reprice['summary']['applied'] is False
    assert not event.price_revisions.exclude(pk=released.pk).exists()

    with override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver']):
        monkeypatch.setenv('PRICING_V2_APPLY_DB_NAME', 'production.sqlite3')
        with pytest.raises(CommandError):
            call_command('pricing_v2_reprice', '--apply', '--confirm-local-apply')
        monkeypatch.setenv('PRICING_V2_APPLY_DB_NAME', str(connection.settings_dict['NAME']))
        call_command('pricing_v2_reprice', '--apply', '--confirm-local-apply')
        capsys.readouterr()
        call_command('pricing_v2_reprice', '--apply', '--confirm-local-apply')
        assert json.loads(capsys.readouterr().out)['rows'][0]['status'] == 'already_applied'
    event.refresh_from_db()
    released.refresh_from_db()
    assert (event.total_price, released.result_json) == before
    assert event.price_revisions.filter(kind='draft').count() == 1
    assert not past.price_revisions.exists() and not cancelled.price_revisions.exists()
    assert Event.objects.get(pk=past.pk).total_price == Decimal('42.00')
    assert Event.objects.get(pk=cancelled.pk).total_price == Decimal('43.00')
