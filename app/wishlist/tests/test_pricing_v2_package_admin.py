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
    pkg = PricingPackage.objects.create(name='Paket', base_price=100)
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
    assert call(staff, 'pricing_v2_component_detail', {'kind': 'items', 'is_active': False}, item.pk, 'put').status_code == 200
    assert PriceItem.objects.get(pk=item.pk).is_active is False
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
        assert edited.status_code == 200, edited.json()
        obj.refresh_from_db()
        assert obj.is_active is False
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
