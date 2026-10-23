"""Staff package and component administration contracts."""
import copy
import json
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from wishlist.models import (PriceCalculationRevision, PriceItem, PricingFormula,
                             PricingPackage, PricingPackageOverride, PricingProfile,
                             PricingRule, PricingWorkflow, Event)
from wishlist.tests.test_pricing_v2_profile_admin import CONFIG


@pytest.fixture
def staff(client):
    client.force_login(get_user_model().objects.create_user('staff-v2', is_staff=True))
    return client


def call(client, route, data, pk=None, method='post'):
    url = reverse('dj_admin:' + route, kwargs={'pk': pk} if pk else {})
    return getattr(client, method)(url, data=json.dumps(data), content_type='application/json')


def profile(status='draft'):
    return PricingProfile.objects.create(name='Global', version=1, status=status,
                                         valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))


def package_payload(p, pkg=None, **changes):
    data = {'profile_id': p.pk, 'expected_profile_version': p.version,
            'expected_package_version': pkg.version if pkg else None,
            'name': 'Paket', 'base_price': '100', 'included_item_ids': [],
            'overrides_json': {}}
    data.update(changes)
    return data


@pytest.mark.django_db
def test_override_modes_display_effective_sources_and_save(staff):
    p = profile()
    pkg = PricingPackage.objects.create(name='Paket', base_price=100)
    data = package_payload(p, pkg, overrides_json={
        'included_hours': {'mode': 'inherit'},
        'km_rate': {'mode': 'override', 'value': '1.25'},
        'holiday_percent': {'mode': 'disabled'},
    })
    response = call(staff, 'pricing_v2_package_detail', data, pkg.pk, 'put')
    assert response.status_code == 200
    pkg.refresh_from_db()
    assert pkg.version == 2
    assert PricingPackageOverride.objects.get(profile=p, package=pkg).overrides_json == data['overrides_json']
    page = staff.get(reverse('dj_admin:pricing_v2_packages') + f'?profile_id={p.pk}&package_id={pkg.pk}')
    assert page.status_code == 200
    factors = {f['key']: f for f in page.context['factors']}
    assert (factors['included_hours']['mode'], factors['included_hours']['source']) == ('inherit', 'global')
    assert (factors['km_rate']['mode'], factors['km_rate']['source'], factors['km_rate']['value']) == ('override', 'package_override', '1.25')
    assert (factors['holiday_percent']['mode'], factors['holiday_percent']['source'], factors['holiday_percent']['value']) == ('disabled', 'package_disabled', None)
    assert 'name="km_rate_mode"' in page.content.decode()


@pytest.mark.django_db
def test_two_editors_stale_package_and_profile_conflict(staff):
    p = profile()
    pkg = PricingPackage.objects.create(name='Paket', base_price=100, is_public=False)
    data = package_payload(p, pkg, base_price='120')
    assert call(staff, 'pricing_v2_package_detail', data, pkg.pk, 'put').status_code == 200
    assert call(staff, 'pricing_v2_package_detail', data, pkg.pk, 'put').status_code == 409
    pkg.refresh_from_db()
    assert pkg.base_price == 120 and pkg.version == 2
    p.version = 2
    p.save(update_fields=['version'])
    assert call(staff, 'pricing_v2_package_detail', package_payload(p, pkg, expected_profile_version=1), pkg.pk, 'put').status_code == 409


@pytest.mark.django_db
def test_active_profile_override_creates_draft_without_mutating_active(staff):
    p = profile('active')
    pkg = PricingPackage.objects.create(name='Paket', base_price=100)
    response = call(staff, 'pricing_v2_package_detail', package_payload(p, pkg, overrides_json={'km_rate': {'mode': 'disabled'}}), pkg.pk, 'put')
    assert response.status_code == 200
    draft = PricingProfile.objects.get(pk=response.json()['profile_id'])
    assert draft.status == 'draft' and draft.version == 2
    assert not PricingPackageOverride.objects.filter(profile=p, package=pkg).exists()
    assert PricingPackageOverride.objects.get(profile=draft, package=pkg).overrides_json['km_rate']['mode'] == 'disabled'


@pytest.mark.django_db
def test_active_profile_package_price_change_clones_package_and_keeps_released_snapshot(staff):
    p = profile('active')
    pkg = PricingPackage.objects.create(name='Original', base_price=100)
    event = Event.objects.create(name='Event', date=timezone.localdate())
    revision = PriceCalculationRevision.objects.create(event=event, kind='offer', status='released',
        revision_number=1, engine_version='2', package_id_snapshot=pkg.pk, package_version=1,
        input_json={}, result_json={'gross_total': '100.00'}, gross_total=100)
    response = call(staff, 'pricing_v2_package_detail', package_payload(p, pkg, name='Changed', base_price='200'), pkg.pk, 'put')
    assert response.status_code == 200
    pkg.refresh_from_db()
    new_pkg = PricingPackage.objects.get(pk=response.json()['id'])
    assert pkg.name == 'Original' and pkg.base_price == 100 and pkg.version == 1
    assert new_pkg.name == 'Changed' and new_pkg.base_price == 200 and new_pkg.pk != pkg.pk
    assert PricingProfile.objects.get(pk=response.json()['profile_id']).status == 'draft'
    revision.refresh_from_db()
    assert (revision.package_id_snapshot, revision.result_json) == (pkg.pk, {'gross_total': '100.00'})


@pytest.mark.django_db
@pytest.mark.parametrize('changes,field', [
    ({'base_price': '-1'}, 'base_price'),
    ({'overrides_json': {'km_rate': {'mode': 'override', 'value': '-1'}}}, 'km_rate'),
    ({'overrides_json': {'saturday_percent': {'mode': 'override', 'value': '101'}}}, 'saturday_percent'),
    ({'overrides_json': {'km_rate': {'mode': 'broken'}}}, 'overrides_json'),
    ({'overrides_json': {'km_rate': {'mode': 'disabled', 'value': '2'}}}, 'overrides_json'),
    ({'overrides_json': ['bad']}, 'overrides_json'),
    ({'overrides_json': {'km_rate': None}}, 'overrides_json'),
    ({'included_item_ids': [999999]}, 'included_item_ids'),
])
def test_package_invalid_inputs_do_not_write(staff, changes, field):
    p = profile()
    response = call(staff, 'pricing_v2_packages', package_payload(p, **changes))
    assert response.status_code == 400 and field in response.json()['errors']
    assert PricingPackage.objects.count() == 0


@pytest.mark.django_db
def test_package_items_preserve_required_default_optional_and_visibility(staff):
    p = profile()
    required = PriceItem.objects.create(name='Required', category='extra', price=0, is_required=True)
    selected = PriceItem.objects.create(name='Selected', category='extra', price=0, is_default=True)
    optional = PriceItem.objects.create(name='Optional', category='extra', price=0, is_public=False)
    response = call(staff, 'pricing_v2_packages', package_payload(p, included_item_ids=[required.pk, selected.pk, optional.pk], is_public=False))
    assert response.status_code == 201
    pkg = PricingPackage.objects.get(pk=response.json()['id'])
    assert not pkg.is_public
    assert set(pkg.included_items.values_list('pk', flat=True)) == {required.pk, selected.pk, optional.pk}
    page = staff.get(reverse('dj_admin:pricing_v2_packages') + f'?profile_id={p.pk}&package_id={pkg.pk}')
    assert {i.pk for i in page.context['items']} == {required.pk, selected.pk, optional.pk}
    assert 'Pflicht' in page.content.decode() and 'Standard' in page.content.decode() and 'Optional' in page.content.decode()


@pytest.mark.django_db
def test_component_crud_validates_modes_conditions_and_formula(staff):
    for kind, invalid, field in [
        ('items', {'name': 'Bad', 'category': 'extra', 'price': '-1'}, 'price'),
        ('rules', {'name': 'Bad', 'effect_type': 'flat_add', 'effect_value': '1', 'condition_json': [{'field': 'guest_count', 'op': 'unknown', 'value': 2}]}, 'condition_json'),
        ('formulas', {'name': 'Bad', 'expression': 'base + 1', 'mode': 'unknown'}, 'mode'),
        ('formulas', {'name': 'Bad', 'expression': '__import__("os")', 'mode': 'add'}, 'expression'),
    ]:
        r = call(staff, 'pricing_v2_components', {'kind': kind, **invalid})
        assert r.status_code == 400 and field in r.json()['errors']
    formula = call(staff, 'pricing_v2_components', {'kind': 'formulas', 'name': 'Bonus', 'expression': 'base + 1', 'mode': 'subtract'})
    assert formula.status_code == 201
    f = PricingFormula.objects.get(pk=formula.json()['id'])
    assert f.mode == 'subtract'
    rule = call(staff, 'pricing_v2_components', {'kind': 'rules', 'name': 'Guests', 'effect_type': 'flat_add', 'effect_value': '10', 'condition_json': [{'field': 'guest_count', 'op': 'gte', 'value': 10}, {'field': 'date_weekday', 'op': 'in', 'value': [5, 6]}]})
    assert rule.status_code == 201 and len(PricingRule.objects.get(pk=rule.json()['id']).condition_json) == 2
    assert call(staff, 'pricing_v2_component_detail', {'kind': 'formulas', 'mode': 'replace', 'is_active': False}, f.pk, 'put').status_code == 200
    f.refresh_from_db()
    assert f.mode == 'replace' and not f.is_active


@pytest.mark.django_db
def test_references_reported_and_delete_blocked_while_retirement_preserves_revision(staff):
    item = PriceItem.objects.create(name='Item', category='extra', price=Decimal('10'))
    pkg = PricingPackage.objects.create(name='Paket', base_price=100)
    pkg.included_items.add(item)
    wf = PricingWorkflow.objects.create(name='Live', workflow_json=[{'type': 'items', 'config': {'item_ids': [item.pk]}}])
    event = Event.objects.create(name='Test', date=timezone.localdate())
    revision = PriceCalculationRevision.objects.create(event=event, kind='offer', status='released', revision_number=1,
        engine_version='2', input_json={'selected_item_ids': [item.pk]}, result_json={'gross_total': '110.00'}, gross_total=110)
    detail = staff.get(reverse('dj_admin:pricing_v2_component_detail', kwargs={'pk': item.pk}) + '?kind=items')
    assert detail.status_code == 200
    assert wf.pk in detail.json()['references']['workflows'] and revision.pk in detail.json()['references']['revisions']
    assert call(staff, 'pricing_v2_component_detail', {'kind': 'items'}, item.pk, 'delete').status_code == 409
    assert call(staff, 'pricing_v2_component_detail', {'kind': 'items', 'is_active': False}, item.pk, 'put').status_code == 409
    assert PriceItem.objects.get(pk=item.pk).is_active is True
    assert PricingWorkflow.objects.get(pk=wf.pk).workflow_json[0]['config']['item_ids'] == [item.pk]
    assert PriceCalculationRevision.objects.get(pk=revision.pk).result_json == {'gross_total': '110.00'}


@pytest.mark.django_db
def test_staff_csrf_and_escaped_pages(client, staff):
    p = profile()
    PricingPackage.objects.create(name='<script>alert(1)</script>', base_price=100)
    PriceItem.objects.create(name='<script>alert(2)</script>', category='extra', price=10)
    for route in ('pricing_v2_packages', 'pricing_v2_components'):
        assert '<script>alert(' not in staff.get(reverse('dj_admin:' + route)).content.decode()
    staff.logout()
    for route in ('pricing_v2_packages', 'pricing_v2_components'):
        assert staff.get(reverse('dj_admin:' + route)).status_code == 302
    ordinary = get_user_model().objects.create_user('ordinary-v2')
    staff.force_login(ordinary)
    assert call(staff, 'pricing_v2_packages', package_payload(p)).status_code == 302
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(get_user_model().objects.create_user('secure-v2', is_staff=True))
    for route in ('pricing_v2_packages', 'pricing_v2_components'):
        assert call(secure, route, {}).status_code == 403


@pytest.mark.django_db
def test_form_post_selected_items_and_bad_json(staff):
    p = profile()
    item = PriceItem.objects.create(name='Extra', category='extra', price=10)
    url = reverse('dj_admin:pricing_v2_packages')
    data = {'profile_id': str(p.pk), 'expected_profile_version': '1',
            'expected_package_version': 'null', 'name': 'Paket', 'base_price': '100',
            'included_item_ids': [str(item.pk)], 'km_rate_mode': 'override', 'km_rate_value': '1.25'}
    response = staff.post(url, data=data)
    assert response.status_code == 201
    pkg = PricingPackage.objects.get(pk=response.json()['id'])
    assert list(pkg.included_items.values_list('pk', flat=True)) == [item.pk]
    assert PricingPackageOverride.objects.get(profile=p, package=pkg).overrides_json['km_rate']['value'] == '1.25'
    assert staff.post(url, data={**data, 'overrides_json': '{oops'}).status_code == 400


@pytest.mark.django_db
def test_malformed_package_identity_does_not_raise(staff):
    assert call(staff, 'pricing_v2_packages', {'profile_id': [], 'expected_profile_version': 1}).status_code == 400


@pytest.mark.django_db
def test_active_rule_and_formula_references_block_delete_without_rewriting_workflow(staff):
    rule = PricingRule.objects.create(name='Rule', effect_type='flat_add', effect_value=10)
    formula = PricingFormula.objects.create(name='Formula', expression='base', mode='add')
    workflow = PricingWorkflow.objects.create(name='Live', workflow_json=[
        {'type': 'rules', 'config': {'rule_ids': [rule.pk]}},
        {'type': 'formula', 'config': {'formula_id': formula.pk}},
    ])
    for kind, obj in [('rules', rule), ('formulas', formula)]:
        response = call(staff, 'pricing_v2_component_detail', {'kind': kind}, obj.pk, 'delete')
        assert response.status_code == 409 and workflow.pk in response.json()['references']['workflows']
        edited = call(staff, 'pricing_v2_component_detail', {'kind': kind, 'is_active': False}, obj.pk, 'put')
        assert edited.status_code == 409, edited.json()
        obj.refresh_from_db()
        assert obj.is_active is True
    workflow.refresh_from_db()
    assert workflow.workflow_json[0]['config']['rule_ids'] == [rule.pk]
    assert workflow.workflow_json[1]['config']['formula_id'] == formula.pk


@pytest.mark.django_db
@pytest.mark.parametrize('kind,payload,field', [
    ('rules', {'name': 'Broken', 'effect_type': 'percent_add', 'effect_value': '101'}, 'effect_value'),
    ('rules', {'name': 'Broken', 'effect_type': 'flat_add', 'effect_value': '1',
               'condition_json': [{'field': [], 'op': 'eq', 'value': 1}]}, 'condition_json'),
    ('formulas', {'name': 'Broken', 'mode': 'replace', 'expression': '1/0'}, 'expression'),
])
def test_component_bad_pricing_inputs_return_field_errors(staff, kind, payload, field):
    response = call(staff, 'pricing_v2_components', {'kind': kind, **payload})
    assert response.status_code == 400 and field in response.json()['errors']


@pytest.mark.django_db
def test_active_profile_override_rejects_stale_latest_version(staff):
    active = profile('active')
    PricingProfile.objects.create(name='Global', version=2, status='draft',
                                  valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    pkg = PricingPackage.objects.create(name='Paket', base_price=100)
    response = call(staff, 'pricing_v2_package_detail', package_payload(active, pkg,
        overrides_json={'km_rate': {'mode': 'disabled'}}), pkg.pk, 'put')
    assert response.status_code == 409
    assert PricingProfile.objects.count() == 2
    assert not PricingPackageOverride.objects.exists()


@pytest.mark.django_db
def test_advanced_rule_form_rejects_malformed_json(staff):
    response = staff.post(reverse('dj_admin:pricing_v2_components'), {
        'kind': 'rules', 'name': 'Bad', 'effect_type': 'flat_add', 'effect_value': '1',
        'condition_json': '{broken',
    })
    assert response.status_code == 400
    assert PricingRule.objects.count() == 0


@pytest.mark.django_db
def test_draft_edit_of_public_active_package_clones_without_touching_live_catalog(staff):
    active = profile('active')
    draft = PricingProfile.objects.create(name='Global', version=2, status='draft',
                                          valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    original = PricingPackage.objects.create(name='Live', base_price=100)
    PricingPackageOverride.objects.create(profile=active, package=original,
                                          overrides_json={'km_rate': {'mode': 'override', 'value': '1'}})
    workflow = PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {'package_id': original.pk}}, {'type': 'items'}])
    payload = package_payload(draft, original, name='Draft price', base_price='180')
    first = call(staff, 'pricing_v2_package_detail', payload, original.pk, 'put')
    assert first.status_code == 200, first.content
    original.refresh_from_db()
    clone = PricingPackage.objects.get(pk=first.json()['id'])
    assert clone.pk != original.pk and clone.version == original.version + 1
    assert (original.name, original.base_price, original.version, original.is_public) == ('Live', 100, 1, True)
    assert (clone.name, clone.base_price, clone.is_public) == ('Draft price', 180, False)
    assert PricingPackageOverride.objects.get(profile=active, package=original).overrides_json['km_rate']['value'] == '1'
    saved_draft = PricingProfile.objects.get(pk=first.json()['profile_id'])
    assert saved_draft.status == 'draft' and saved_draft.pk != draft.pk and saved_draft.version == draft.version + 1
    assert PricingPackageOverride.objects.filter(profile=saved_draft, package=clone).exists()
    assert call(staff, 'pricing_v2_package_detail', payload, original.pk, 'put').status_code == 409
    assert PricingPackage.objects.count() == 2
    assert PricingWorkflow.objects.get(pk=workflow.pk).workflow_json[0]['config']['package_id'] == original.pk
    from wishlist.models import AppConfig
    AppConfig.objects.create(pricing_engine_mode='v2')
    public = Client().post('/api/price-estimate/', data=json.dumps({
        'date': '2026-12-02', 'time_start': '18:00', 'time_end': '21:00',
        'package_id': clone.pk, 'selected_item_ids': [], 'distance_km': '12',
    }), content_type='application/json')
    assert public.status_code == 400 and public.json()['gross_total'] is None
    live = Client().post('/api/price-estimate/', data=json.dumps({
        'date': '2026-12-02', 'time_start': '18:00', 'time_end': '21:00',
        'package_id': original.pk, 'selected_item_ids': [], 'distance_km': '12',
    }), content_type='application/json')
    assert live.status_code == 200 and live.json()['package']['price'] == '100.00'


@pytest.mark.django_db
def test_released_catalog_snapshot_included_only_item_blocks_delete_but_allows_safe_retirement(staff):
    item = PriceItem.objects.create(name='Included', category='extra', price=10)
    event = Event.objects.create(name='Event', date=timezone.localdate())
    snapshot = {'packages': {'42': {'included_item_ids': [str(item.pk)]}},
                'items': {str(item.pk): {'label': 'Included', 'price': '10'}},
                'rules': {}, 'formulas': {}}
    revision = PriceCalculationRevision.objects.create(event=event, kind='offer', status='released',
        revision_number=1, engine_version='2', input_json={'catalog_snapshot': snapshot},
        result_json={'gross_total': '110.00'}, gross_total=110)
    detail = staff.get(reverse('dj_admin:pricing_v2_component_detail', kwargs={'pk': item.pk}) + '?kind=items')
    assert revision.pk in detail.json()['references']['revisions']
    assert call(staff, 'pricing_v2_component_detail', {'kind': 'items'}, item.pk, 'delete').status_code == 409
    assert call(staff, 'pricing_v2_component_detail', {'kind': 'items', 'is_active': False}, item.pk, 'put').status_code == 200
    assert PriceCalculationRevision.objects.get(pk=revision.pk).result_json == {'gross_total': '110.00'}


@pytest.mark.django_db
def test_rule_formula_snapshots_without_selected_ids_block_deletion(staff):
    rule = PricingRule.objects.create(name='Old', effect_type='flat_add', effect_value=10)
    formula = PricingFormula.objects.create(name='Old', expression='base')
    event = Event.objects.create(name='Event', date=timezone.localdate())
    revision = PriceCalculationRevision.objects.create(event=event, kind='offer', status='released',
        revision_number=1, engine_version='2', input_json={'catalog_snapshot': {
            'packages': {}, 'items': {}, 'rules': {str(rule.pk): {'effect_value': '10'}},
            'formulas': {str(formula.pk): {'expression': 'base'}}}},
        result_json={'gross_total': '100.00'}, gross_total=100)
    for kind, obj in [('rules', rule), ('formulas', formula)]:
        detail = staff.get(reverse('dj_admin:pricing_v2_component_detail', kwargs={'pk': obj.pk}) + '?kind=' + kind)
        assert revision.pk in detail.json()['references']['revisions']
        assert call(staff, 'pricing_v2_component_detail', {'kind': kind}, obj.pk, 'delete').status_code == 409


@pytest.mark.django_db
def test_result_catalog_snapshot_included_item_blocks_deletion(staff):
    item = PriceItem.objects.create(name='Historical', category='extra', price=10)
    event = Event.objects.create(name='Event', date=timezone.localdate())
    revision = PriceCalculationRevision.objects.create(event=event, kind='offer', status='sent',
        revision_number=1, engine_version='2', input_json={}, result_json={
            'catalog_snapshot': {'packages': {'31': {'included_item_ids': [str(item.pk)]}},
                                 'items': {}, 'rules': {}, 'formulas': {}}, 'gross_total': '10.00'}, gross_total=10)
    detail = staff.get(reverse('dj_admin:pricing_v2_component_detail', kwargs={'pk': item.pk}) + '?kind=items')
    assert revision.pk in detail.json()['references']['revisions']
    assert call(staff, 'pricing_v2_component_detail', {'kind': 'items'}, item.pk, 'delete').status_code == 409


@pytest.mark.django_db
def test_active_profile_override_only_keeps_shared_package_row_and_visibility(staff):
    active = profile('active')
    pkg = PricingPackage.objects.create(name='Live', base_price=100)
    response = call(staff, 'pricing_v2_package_detail', package_payload(active, pkg,
        name='Live', overrides_json={'km_rate': {'mode': 'disabled'}}), pkg.pk, 'put')
    assert response.status_code == 200
    pkg.refresh_from_db()
    assert pkg.version == 1 and pkg.is_public is True
    assert PricingPackage.objects.count() == 1
    assert PricingPackageOverride.objects.get(profile_id=response.json()['profile_id'], package=pkg).overrides_json['km_rate']['mode'] == 'disabled'


@pytest.mark.django_db
def test_draft_shared_override_is_versioned_without_changing_live_package(staff):
    active = profile('active')
    draft = PricingProfile.objects.create(name='Global', version=2, status='draft',
                                          valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    pkg = PricingPackage.objects.create(name='Live', base_price=100)
    payload = package_payload(draft, pkg, name='Live',
        overrides_json={'km_rate': {'mode': 'override', 'value': '2'}})
    first = call(staff, 'pricing_v2_package_detail', payload, pkg.pk, 'put')
    assert first.status_code == 200
    assert first.json()['profile_id'] != draft.pk
    pkg.refresh_from_db()
    assert pkg.version == 1 and pkg.is_public
    assert not PricingPackageOverride.objects.filter(profile=active, package=pkg).exists()
    assert call(staff, 'pricing_v2_package_detail', payload, pkg.pk, 'put').status_code == 409


@pytest.mark.django_db
def test_active_package_clone_with_items_does_not_mutate_original_inclusions(staff):
    active = profile('active')
    first = PriceItem.objects.create(name='First', category='extra', price=10)
    second = PriceItem.objects.create(name='Second', category='extra', price=20)
    pkg = PricingPackage.objects.create(name='Live', base_price=100)
    pkg.included_items.add(first)
    response = call(staff, 'pricing_v2_package_detail', package_payload(active, pkg,
        name='Live', included_item_ids=[second.pk]), pkg.pk, 'put')
    assert response.status_code == 200
    pkg.refresh_from_db()
    clone = PricingPackage.objects.get(pk=response.json()['id'])
    assert list(pkg.included_items.values_list('pk', flat=True)) == [first.pk]
    assert list(clone.included_items.values_list('pk', flat=True)) == [second.pk]
    assert not clone.is_public


@pytest.mark.django_db
def test_private_package_newly_edited_on_active_profile_stays_private(staff):
    active = profile('active')
    pkg = PricingPackage.objects.create(name='Internal', base_price=100, is_public=False)
    response = call(staff, 'pricing_v2_package_detail', package_payload(active, pkg,
        name='Internal updated', base_price='120', is_public=True), pkg.pk, 'put')
    assert response.status_code == 400
    pkg.refresh_from_db()
    assert not pkg.is_public and pkg.base_price == 100
    assert PricingPackage.objects.count() == 1


@pytest.mark.django_db
def test_shared_package_edit_does_not_drop_other_draft_override(staff):
    active = profile('active')
    draft = PricingProfile.objects.create(name='Global', version=2, status='draft',
                                          valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    pkg = PricingPackage.objects.create(name='Live', base_price=100)
    PricingPackageOverride.objects.create(profile=draft, package=pkg,
                                          overrides_json={'km_rate': {'mode': 'override', 'value': '3'}})
    response = call(staff, 'pricing_v2_package_detail', package_payload(draft, pkg,
        name='Changed', base_price='200', overrides_json={'km_rate': {'mode': 'override', 'value': '3'}}), pkg.pk, 'put')
    assert response.status_code == 200
    new_draft = PricingProfile.objects.get(pk=response.json()['profile_id'])
    assert PricingPackageOverride.objects.get(profile=new_draft, package=pkg).overrides_json['km_rate']['value'] == '3'
    assert PricingPackageOverride.objects.get(profile=new_draft, package_id=response.json()['id']).overrides_json['km_rate']['value'] == '3'


@pytest.mark.django_db
def test_complex_factor_editors_render_json_roundtrip_and_escape_html(staff):
    p = profile()
    pkg = PricingPackage.objects.create(name='Complex', base_price=100)
    seasons = [{'identifier': 'winter', 'label': '<Winter>', 'start': '12-01', 'end': '02-28', 'percent': '15'}]
    tiers = [{'lower_bound': 0, 'upper_bound': 9, 'value': '25'},
             {'lower_bound': 10, 'upper_bound': None, 'value': '0'}]
    override = {'seasons': {'mode': 'override', 'value': seasons},
                'guest_tiers': {'mode': 'override', 'value': tiers}}
    PricingPackageOverride.objects.create(profile=p, package=pkg, overrides_json=override)
    page = staff.get(reverse('dj_admin:pricing_v2_packages') + f'?profile_id={p.pk}&package_id={pkg.pk}')
    from html.parser import HTMLParser
    class Inputs(HTMLParser):
        values = {}
        def handle_starttag(self, tag, attrs):
            fields = dict(attrs)
            if tag in ('input', 'textarea') and fields.get('name') in ('seasons_value', 'guest_tiers_value'):
                self.values[fields['name']] = fields.get('value')
    parser = Inputs()
    parser.feed(page.content.decode())
    assert json.loads(parser.values['seasons_value']) == seasons
    assert json.loads(parser.values['guest_tiers_value']) == tiers
    assert '<Winter>' not in page.content.decode()


@pytest.mark.django_db
def test_active_workflow_implicit_rules_cannot_be_deactivated(staff):
    rule = PricingRule.objects.create(name='Auto rule', effect_type='flat_add', effect_value=10)
    workflow = PricingWorkflow.objects.create(name='Live', workflow_json=[{'type': 'rules'}])
    response = call(staff, 'pricing_v2_component_detail', {'kind': 'rules', 'is_active': False}, rule.pk, 'put')
    assert response.status_code == 409
    assert workflow.pk in response.json()['references']['workflows']
    rule.refresh_from_db()
    assert rule.is_active


@pytest.mark.django_db
def test_active_workflow_package_included_item_cannot_be_deactivated(staff):
    item = PriceItem.objects.create(name='Included', category='extra', price=10)
    pkg = PricingPackage.objects.create(name='Live', base_price=100)
    pkg.included_items.add(item)
    workflow = PricingWorkflow.objects.create(name='Live', workflow_json=[
        {'type': 'package', 'config': {'package_id': pkg.pk}}, {'type': 'items'}])
    response = call(staff, 'pricing_v2_component_detail', {'kind': 'items', 'is_active': False}, item.pk, 'put')
    assert response.status_code == 409
    assert workflow.pk in response.json()['references']['workflows']
    item.refresh_from_db()
    assert item.is_active


@pytest.mark.django_db
def test_active_workflow_required_item_cannot_be_deactivated(staff):
    item = PriceItem.objects.create(name='Required', category='extra', price=10, is_required=True)
    workflow = PricingWorkflow.objects.create(name='Live', workflow_json=[{'type': 'items'}])
    response = call(staff, 'pricing_v2_component_detail', {'kind': 'items', 'is_active': False}, item.pk, 'put')
    assert response.status_code == 409
    assert workflow.pk in response.json()['references']['workflows']
    item.refresh_from_db()
    assert item.is_active


@pytest.mark.django_db
@pytest.mark.parametrize('pin_package', [True, False])
def test_private_draft_clone_cannot_publish_before_activation_even_with_compatible_workflow(staff, pin_package):
    active = profile('active')
    original = PricingPackage.objects.create(name='Live', base_price=100)
    workflow = PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {'package_id': original.pk}}, {'type': 'items'}])
    created = call(staff, 'pricing_v2_package_detail', package_payload(active, original,
        name='Next', base_price='180'), original.pk, 'put')
    assert created.status_code == 200
    clone = PricingPackage.objects.get(pk=created.json()['id'])
    draft = PricingProfile.objects.get(pk=created.json()['profile_id'])
    if pin_package:
        workflow.workflow_json = [{'type': 'package', 'config': {'package_id': clone.pk}}, {'type': 'items'}]
    else:
        workflow.workflow_json = [{'type': 'package', 'config': {}}, {'type': 'items'}]
    workflow.save(update_fields=['workflow_json'])
    denied = call(staff, 'pricing_v2_package_detail', package_payload(draft, clone,
        name='Next', base_price='180', is_public=True), clone.pk, 'put')
    assert denied.status_code in (400, 409) and 'aktiv' in str(denied.json()).lower()
    clone.refresh_from_db()
    assert clone.pk == created.json()['id'] and not clone.is_public
    assert PricingPackage.objects.count() == 2
    original.refresh_from_db()
    assert original.base_price == 100 and original.is_public


@pytest.mark.django_db
@pytest.mark.parametrize('pin_package', [True, False])
def test_publish_same_clone_after_activation_when_default_workflow_compatible(staff, pin_package):
    from wishlist.models import AppConfig
    active = profile('active')
    original = PricingPackage.objects.create(name='Live', base_price=100)
    event = Event.objects.create(name='Prior offer', date=timezone.localdate())
    revision = PriceCalculationRevision.objects.create(event=event, kind='offer', status='released',
        revision_number=1, engine_version='2', package_id_snapshot=original.pk, package_version=1,
        input_json={}, result_json={'gross_total': '100.00'}, gross_total=100)
    workflow = PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {'package_id': original.pk}}, {'type': 'items'}])
    created = call(staff, 'pricing_v2_package_detail', package_payload(active, original,
        name='Next', base_price='180'), original.pk, 'put')
    assert created.status_code == 200
    clone = PricingPackage.objects.get(pk=created.json()['id'])
    draft = PricingProfile.objects.get(pk=created.json()['profile_id'])
    workflow.workflow_json = [
        {'type': 'package', 'config': {'package_id': clone.pk} if pin_package else {}}, {'type': 'items'}]
    workflow.save(update_fields=['workflow_json'])
    activation = call(staff, 'pricing_v2_activate', {'profile_id': draft.pk,
        'expected_profile_id': active.pk, 'expected_version': active.version})
    assert activation.status_code == 200
    publish = package_payload(draft, clone, name='Next', base_price='180', is_public=True)
    response = call(staff, 'pricing_v2_package_detail', publish, clone.pk, 'put')
    assert response.status_code == 200, response.content
    clone.refresh_from_db()
    original.refresh_from_db()
    assert response.json()['id'] == clone.pk and clone.is_public and clone.version == 3
    assert original.base_price == 100 and original.version == 1 and original.is_public
    assert PricingPackage.objects.count() == 2
    assert call(staff, 'pricing_v2_package_detail', publish, clone.pk, 'put').status_code == 409
    assert PriceCalculationRevision.objects.get(pk=revision.pk).result_json == {'gross_total': '100.00'}
    AppConfig.objects.create(pricing_engine_mode='v2')
    estimate = Client().post('/api/price-estimate/', data=json.dumps({
        'date': '2026-12-02', 'time_start': '18:00', 'time_end': '21:00',
        'package_id': clone.pk, 'selected_item_ids': [], 'distance_km': '12',
    }), content_type='application/json')
    assert estimate.status_code == 200 and estimate.json()['package']['price'] == '180.00'


@pytest.mark.django_db
def test_publication_rejects_mismatched_default_workflow_without_creating_another_clone(staff):
    active = profile('active')
    original = PricingPackage.objects.create(name='Live', base_price=100)
    PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {'package_id': original.pk}}, {'type': 'items'}])
    created = call(staff, 'pricing_v2_package_detail', package_payload(active, original,
        name='Next', base_price='180'), original.pk, 'put')
    clone = PricingPackage.objects.get(pk=created.json()['id'])
    draft = PricingProfile.objects.get(pk=created.json()['profile_id'])
    assert call(staff, 'pricing_v2_activate', {'profile_id': draft.pk,
        'expected_profile_id': active.pk, 'expected_version': active.version}).status_code == 200
    denied = call(staff, 'pricing_v2_package_detail', package_payload(draft, clone,
        name='Next', base_price='180', is_public=True), clone.pk, 'put')
    assert denied.status_code in (400, 409)
    assert 'workflow' in str(denied.json()).lower()
    clone.refresh_from_db()
    assert not clone.is_public and PricingPackage.objects.count() == 2
    assert PricingProfile.objects.filter(status='draft').count() == 0


@pytest.mark.django_db
def test_new_package_from_draft_cannot_be_public_before_activation(staff):
    active = profile('active')
    draft = PricingProfile.objects.create(name='Global', version=2, status='draft',
                                          valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {}}, {'type': 'items'}])
    response = call(staff, 'pricing_v2_packages', package_payload(draft,
        name='Draft only', base_price='180', is_public=True))
    assert response.status_code == 201
    pkg = PricingPackage.objects.get(pk=response.json()['id'])
    assert not pkg.is_public
    denied = call(staff, 'pricing_v2_package_detail', package_payload(draft, pkg,
        name='Draft only', base_price='180', is_public=True), pkg.pk, 'put')
    assert denied.status_code in (400, 409)
    pkg.refresh_from_db()
    assert not pkg.is_public


@pytest.mark.django_db
def test_publish_rejects_missing_default_workflow_and_price_change(staff):
    active = profile('active')
    original = PricingPackage.objects.create(name='Live', base_price=100)
    created = call(staff, 'pricing_v2_package_detail', package_payload(active, original,
        name='Next', base_price='180'), original.pk, 'put')
    clone = PricingPackage.objects.get(pk=created.json()['id'])
    draft = PricingProfile.objects.get(pk=created.json()['profile_id'])
    assert call(staff, 'pricing_v2_activate', {'profile_id': draft.pk,
        'expected_profile_id': active.pk, 'expected_version': active.version}).status_code == 200
    publish = package_payload(draft, clone, name='Next', base_price='180', is_public=True)
    missing = call(staff, 'pricing_v2_package_detail', publish, clone.pk, 'put')
    assert missing.status_code == 400 and 'Workflow' in str(missing.json())
    PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {'package_id': clone.pk}}, {'type': 'items'}])
    mixed = call(staff, 'pricing_v2_package_detail', {**publish, 'base_price': '999'}, clone.pk, 'put')
    assert mixed.status_code == 400
    clone.refresh_from_db()
    assert clone.base_price == 180 and not clone.is_public and clone.version == 2
    original.refresh_from_db()
    assert original.base_price == 100


@pytest.mark.django_db
def test_private_draft_package_cannot_publish_with_unrelated_active_profile(staff):
    draft = profile('draft')
    package = PricingPackage.objects.create(name='Draft only', base_price=180, is_public=False)
    PricingPackageOverride.objects.create(profile=draft, package=package, overrides_json={})
    PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {'package_id': package.pk}}, {'type': 'items'}])
    draft.status = 'retired'
    draft.save(update_fields=['status'])
    active = PricingProfile.objects.create(name='Global', version=2, status='active',
                                           valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    denied = call(staff, 'pricing_v2_package_detail', package_payload(active, package,
        name='Draft only', base_price='180', is_public=True), package.pk, 'put')
    assert denied.status_code in (400, 409)
    package.refresh_from_db()
    assert not package.is_public


@pytest.mark.django_db
def test_active_profile_cannot_publish_clone_owned_only_by_another_draft(staff):
    active = profile('active')
    draft = PricingProfile.objects.create(name='Global', version=2, status='draft',
                                          valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    package = PricingPackage.objects.create(name='Draft clone', base_price=180, is_public=False)
    PricingPackageOverride.objects.create(profile=draft, package=package,
                                          overrides_json={'km_rate': {'mode': 'override', 'value': '2'}})
    PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {'package_id': package.pk}}, {'type': 'items'}])
    response = call(staff, 'pricing_v2_package_detail', package_payload(active, package,
        name='Draft clone', base_price='180', is_public=True), package.pk, 'put')
    assert response.status_code in (400, 409)
    package.refresh_from_db()
    assert not package.is_public and package.version == 1
    assert PricingPackage.objects.count() == 1
    assert PricingPackageOverride.objects.get(profile=draft, package=package).overrides_json == {
        'km_rate': {'mode': 'override', 'value': '2'}}


@pytest.mark.django_db
def test_unpinned_active_default_workflow_prices_two_public_packages(staff):
    from wishlist.models import AppConfig
    active = profile('active')
    one = PricingPackage.objects.create(name='One', base_price=100)
    two = PricingPackage.objects.create(name='Two', base_price=200)
    PricingWorkflow.objects.create(name='Default', is_default=True,
        workflow_json=[{'type': 'package', 'config': {}}, {'type': 'items'}])
    AppConfig.objects.create(pricing_engine_mode='v2')
    client = Client()
    results = []
    for package in (one, two):
        response = client.post('/api/price-estimate/', data=json.dumps({
            'date': '2026-12-02', 'time_start': '18:00', 'time_end': '21:00',
            'package_id': package.pk, 'selected_item_ids': [], 'distance_km': '12',
        }), content_type='application/json')
        assert response.status_code == 200, response.content
        results.append(response.json())
    assert [result['package']['name'] for result in results] == ['One', 'Two']
    assert [result['package']['price'] for result in results] == ['100.00', '200.00']


@pytest.mark.django_db
def test_draft_cannot_publish_existing_private_package_without_change(staff):
    active = profile('active')
    draft = PricingProfile.objects.create(name='Global', version=2, status='draft',
                                          valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    package = PricingPackage.objects.create(name='Private', base_price=80, is_public=False)
    request = package_payload(draft, package, name='Private', base_price='80', is_public=True)
    response = call(staff, 'pricing_v2_package_detail', request, package.pk, 'put')
    assert response.status_code == 400
    package.refresh_from_db()
    assert not package.is_public


@pytest.mark.django_db
def test_publication_rejects_malformed_default_workflow_config(staff):
    active = profile('active')
    original = PricingPackage.objects.create(name='Live', base_price=100)
    created = call(staff, 'pricing_v2_package_detail', package_payload(active, original,
        name='Next', base_price='180'), original.pk, 'put')
    clone = PricingPackage.objects.get(pk=created.json()['id'])
    draft = PricingProfile.objects.get(pk=created.json()['profile_id'])
    assert call(staff, 'pricing_v2_activate', {'profile_id': draft.pk,
        'expected_profile_id': active.pk, 'expected_version': active.version}).status_code == 200
    PricingWorkflow.objects.create(name='Malformed', is_default=True,
        workflow_json=[{'type': 'package', 'config': None}, {'type': 'items'}])
    response = call(staff, 'pricing_v2_package_detail', package_payload(draft, clone,
        name='Next', base_price='180', is_public=True), clone.pk, 'put')
    assert response.status_code in (400, 409)
    clone.refresh_from_db()
    assert not clone.is_public and PricingPackage.objects.count() == 2
