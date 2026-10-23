"""V2 workflow builder and read-only comparison contracts."""
import copy
import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from wishlist.models import (AppConfig, Event, PriceCalculationRevision, PriceItem,
                             PricingFormula, PricingPackage, PricingProfile, PricingRule,
                             PricingWorkflow)
from wishlist.tests.test_pricing_v2_profile_admin import CONFIG


@pytest.fixture
def catalog(db):
    PricingProfile.objects.create(name='Global', version=1, status='active',
                                  valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG))
    package = PricingPackage.objects.create(name='Basis', base_price=100)
    item = PriceItem.objects.create(name='Extra', price=20)
    rule = PricingRule.objects.create(name='Zuschlag', effect_type='flat_add', effect_value=5)
    formula = PricingFormula.objects.create(name='Formel', expression='1')
    return package, item, rule, formula


@pytest.fixture
def staff(db):
    user = get_user_model().objects.create_user('workflow-staff', password='secret', is_staff=True)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    client.get(reverse('dj_admin:pricing_v2_workflows'))
    token = client.cookies['csrftoken'].value
    return client, token


def post(staff, name, body, *, pk=None):
    client, token = staff
    return client.post(reverse('dj_admin:' + name, kwargs={'pk': pk} if pk else {}),
                       data=json.dumps(body), content_type='application/json',
                       HTTP_X_CSRFTOKEN=token)


def blocks(catalog):
    package, item, rule, formula = catalog
    return [{'type': 'package', 'config': {'package_id': package.pk}},
            {'type': 'items', 'config': {'item_ids': [item.pk]}},
            *[{'type': name} for name in ('duration', 'setup_teardown', 'travel', 'guest_tiers',
                                         'calendar_factors')],
            {'type': 'rules', 'config': {'rule_ids': [rule.pk]}},
            {'type': 'formula', 'config': {'formula_id': formula.pk}},
            *[{'type': name} for name in ('discount', 'minimum_price', 'rounding')]]


@pytest.mark.django_db
def test_staff_only_and_csrf_protected(catalog, staff):
    url = reverse('dj_admin:pricing_v2_workflows')
    assert Client().get(url).status_code == 302
    normal = Client()
    normal.force_login(get_user_model().objects.create_user('workflow-normal'))
    assert normal.get(url).status_code == 302
    assert Client(enforce_csrf_checks=True).post(url, data='{}', content_type='application/json').status_code == 403
    assert staff[0].post(url, data='{}', content_type='application/json').status_code == 403
    assert staff[0].get(url).status_code == 200


@pytest.mark.django_db
def test_create_and_validate_all_blocks_and_order(catalog, staff):
    response = post(staff, 'pricing_v2_workflows', {'name': 'Alle', 'workflow_json': blocks(catalog)})
    assert response.status_code == 201
    workflow = PricingWorkflow.objects.get(pk=response.json()['id'])
    assert workflow.is_active is False and workflow.is_default is False
    assert workflow.workflow_json == blocks(catalog)
    assert post(staff, 'pricing_v2_workflow_validate', {'workflow_json': blocks(catalog)}).json()['valid'] is True
    page = staff[0].get(reverse('dj_admin:pricing_v2_workflows') + f'?workflow_id={workflow.pk}')
    assert page.status_code == 200
    assert [b['type'] for b in page.context['blocks']] == [b['type'] for b in blocks(catalog)]


@pytest.mark.django_db
def test_invalid_order_references_and_inactive_components_rejected(catalog, staff):
    variants = [blocks(catalog) + [{'type': 'other'}], blocks(catalog) + [{'type': 'items'}],
                [{'type': 'rounding'}, *blocks(catalog)],
                [{**blocks(catalog)[0], 'config': {'package_id': 999999}}, *blocks(catalog)[1:]],
                [{'type': 'discount'}], [{'type': 'package'}]]
    for candidate in variants:
        assert post(staff, 'pricing_v2_workflow_validate', {'workflow_json': candidate}).status_code == 400
        assert post(staff, 'pricing_v2_workflows', {'name': 'Bad', 'workflow_json': candidate}).status_code == 400
    catalog[2].is_active = False
    catalog[2].save(update_fields=['is_active'])
    assert post(staff, 'pricing_v2_workflow_validate', {'workflow_json': blocks(catalog)}).status_code == 400
    assert not PricingWorkflow.objects.exists()


@pytest.mark.django_db
def test_clone_version_stale_edit_and_activation_gate(catalog, staff):
    original = post(staff, 'pricing_v2_workflows', {'name': 'Standard', 'workflow_json': blocks(catalog)}).json()
    pk = original['id']
    bad = PricingWorkflow.objects.create(name='Bad', workflow_json=[{'type': 'unknown'}], is_active=False)
    assert post(staff, 'pricing_v2_workflow_activate', {'expected_version': bad.version}, pk=bad.pk).status_code == 400
    assert post(staff, 'pricing_v2_workflow_activate', {'expected_version': 1}, pk=pk).status_code == 200
    baseline = PricingWorkflow.objects.get(pk=pk)
    changed = blocks(catalog)[:-1]
    result = post(staff, 'pricing_v2_workflow_detail', {'expected_version': 1, 'name': 'Standard',
                  'workflow_json': changed}, pk=pk)
    assert result.status_code == 201
    next_row = PricingWorkflow.objects.get(pk=result.json()['id'])
    assert next_row.pk != pk and next_row.version == 2 and not next_row.is_active
    assert post(staff, 'pricing_v2_workflow_detail', {'expected_version': 1,
                'workflow_json': changed}, pk=pk).status_code == 409
    baseline.refresh_from_db()
    assert baseline.workflow_json == blocks(catalog) and baseline.is_active
    clone = post(staff, 'pricing_v2_workflow_clone', {'expected_version': 2}, pk=next_row.pk)
    assert clone.status_code == 201 and clone.json()['version'] == 3
    assert post(staff, 'pricing_v2_workflow_activate', {'expected_version': 2}, pk=next_row.pk).status_code == 409
    assert post(staff, 'pricing_v2_workflow_activate', {'expected_version': 3},
                pk=clone.json()['id']).status_code == 200
    baseline.refresh_from_db()
    assert not baseline.is_default
    assert PricingWorkflow.objects.filter(is_active=True, is_default=True).count() == 1


@pytest.mark.django_db
def test_two_editors_old_activation_is_stale_but_latest_can_activate(catalog, staff):
    """Simulate two editor tabs; SQLite cannot provide a useful row-lock race."""
    old = PricingWorkflow.objects.create(name='Concurrent', version=1, is_active=True,
        is_default=True, workflow_json=blocks(catalog))
    latest = PricingWorkflow.objects.create(name='Concurrent', version=2, is_active=False,
        is_default=False, workflow_json=blocks(catalog))

    # Editor A opened V1, while editor B created V2 from the same family.
    stale = post(staff, 'pricing_v2_workflow_activate', {'expected_version': 1}, pk=old.pk)
    assert stale.status_code == 409
    assert stale.json()['error'] == 'Die Workflow-Version ist veraltet. Bitte neu laden.'
    old.refresh_from_db()
    latest.refresh_from_db()
    assert old.is_active and old.is_default
    assert not latest.is_active and not latest.is_default

    # The editor holding the family head can still activate it successfully.
    activated = post(staff, 'pricing_v2_workflow_activate', {'expected_version': 2}, pk=latest.pk)
    assert activated.status_code == 200
    old.refresh_from_db()
    latest.refresh_from_db()
    assert old.is_active and not old.is_default
    assert latest.is_active and latest.is_default
    assert PricingWorkflow.objects.filter(is_active=True, is_default=True).count() == 1


@pytest.mark.django_db
def test_test_lab_comparison_is_read_only_and_pinned_package_is_not_substituted(catalog, staff):
    package, item, _, _ = catalog
    other = PricingPackage.objects.create(name='Other', base_price=200)
    workflow = PricingWorkflow.objects.create(name='Pinned', workflow_json=blocks(catalog), is_active=True)
    payload = {'workflow_id': workflow.pk, 'event_date': '2026-12-02', 'start_time': '18:00',
               'end_time': '21:00', 'package_id': other.pk, 'selected_item_ids': [item.pk],
               'guest_count': 5, 'distance_km': '12'}
    response = post(staff, 'pricing_v2_workflow_test', payload)
    assert response.status_code == 200
    assert response.json()['v2']['diagnostic']
    payload['package_id'] = package.pk
    response = post(staff, 'pricing_v2_workflow_test', payload)
    assert response.status_code == 200
    assert response.json()['v2'].get('gross_total'), response.json()
    assert isinstance(response.json()['v2']['lines'], list)
    assert PriceCalculationRevision.objects.count() == 0
    assert Event.objects.count() == 0
    assert not AppConfig.objects.filter(pk=1).exists()
    workflow.workflow_json[0] = {'type': 'package'}
    workflow.save(update_fields=['workflow_json'])
    payload['package_id'] = other.pk
    assert post(staff, 'pricing_v2_workflow_test', payload).json()['v2']['gross_total']


@pytest.mark.django_db
def test_page_escapes_labels_and_provides_reorder_controls(catalog, staff):
    catalog[0].name = '<img src=x onerror=alert(1)>'
    catalog[0].save(update_fields=['name'])
    workflow = PricingWorkflow.objects.create(name='Safe', workflow_json=[{'type': 'package'}, {'type': 'items'}], is_active=False)
    page = staff[0].get(reverse('dj_admin:pricing_v2_workflows') + f'?workflow_id={workflow.pk}')
    html = page.content.decode()
    assert '&lt;img' in html and '<img src=x' not in html
    assert 'data-action="up"' in html and 'data-action="down"' in html
    assert 'textContent' in html and 'innerHTML' not in html


@pytest.mark.django_db
def test_inactive_package_and_items_and_missing_formula_are_rejected(catalog, staff):
    candidate = blocks(catalog)
    catalog[0].is_active = False
    catalog[0].save(update_fields=['is_active'])
    assert post(staff, 'pricing_v2_workflow_validate', {'workflow_json': candidate}).status_code == 400
    catalog[0].is_active = True
    catalog[0].save(update_fields=['is_active'])
    catalog[1].is_active = False
    catalog[1].save(update_fields=['is_active'])
    assert post(staff, 'pricing_v2_workflow_validate', {'workflow_json': candidate}).status_code == 400
    catalog[1].is_active = True
    catalog[1].save(update_fields=['is_active'])
    candidate[8]['config']['formula_id'] = 999999
    assert post(staff, 'pricing_v2_workflow_validate', {'workflow_json': candidate}).status_code == 400


@pytest.mark.django_db
def test_draft_lab_returns_ordered_subtotals_and_difference(catalog, staff):
    package, item, _, _ = catalog
    draft = PricingWorkflow.objects.create(name='Draft', is_active=True, is_default=False,
        workflow_json=[{'type': 'package'}, {'type': 'items'}, {'type': 'minimum_price'}, {'type': 'rounding'}])
    result = post(staff, 'pricing_v2_workflow_test', {
        'workflow_id': draft.pk, 'event_date': '2026-12-02', 'start_time': '18:00',
        'end_time': '21:00', 'package_id': package.pk, 'selected_item_ids': [item.pk],
        'guest_count': 5, 'distance_km': '12'}).json()
    assert result['v2'].get('gross_total'), result
    assert result['v2']['subtotals']
    assert result['difference'] is not None


@pytest.mark.django_db
def test_explicit_package_refuses_individual_selection(catalog, staff):
    workflow = PricingWorkflow.objects.create(name='Pinned', workflow_json=blocks(catalog), is_active=True)
    result = post(staff, 'pricing_v2_workflow_test', {
        'workflow_id': workflow.pk, 'event_date': '2026-12-02', 'start_time': '18:00',
        'end_time': '21:00', 'package_id': '', 'selected_item_ids': [], 'guest_count': 1}).json()
    assert result['v2']['code'] == 'reference_error', result


@pytest.mark.django_db
def test_test_lab_does_not_leak_exception_text(catalog, staff):
    workflow = PricingWorkflow.objects.create(name='Draft', is_active=True,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    result = post(staff, 'pricing_v2_workflow_test', {'workflow_id': workflow.pk,
        'event_date': 'bad', 'start_time': '18:00', 'end_time': '21:00'}).json()
    assert 'diagnostic' in result['v2']
    assert 'detail' not in result['v2']


@pytest.mark.django_db
def test_editor_rejects_unsupported_config_keys(catalog, staff):
    candidate = blocks(catalog)
    candidate[0]['config']['unexpected'] = 1
    assert post(staff, 'pricing_v2_workflow_validate', {'workflow_json': candidate}).status_code == 400


@pytest.mark.django_db
def test_draft_activation_preserves_existing_active_workflow(catalog, staff):
    old = PricingWorkflow.objects.create(name='Old', is_active=True, is_default=True,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    candidate = PricingWorkflow.objects.create(name='New', is_active=False,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    assert post(staff, 'pricing_v2_workflow_activate', {'expected_version': 1}, pk=candidate.pk).status_code == 200
    old.refresh_from_db()
    assert old.is_active and not old.is_default
    assert PricingWorkflow.objects.get(pk=candidate.pk).is_default


@pytest.mark.django_db
def test_lab_requires_selected_workflow_instead_of_falling_back_to_default(catalog, staff):
    PricingWorkflow.objects.create(name='Default', is_active=True, is_default=True,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    response = post(staff, 'pricing_v2_workflow_test', {'event_date': '2026-12-02',
        'start_time': '18:00', 'end_time': '21:00', 'package_id': catalog[0].pk,
        'selected_item_ids': []})
    assert response.status_code == 400


@pytest.mark.django_db
def test_active_nondefault_cannot_be_activated_in_place(catalog, staff):
    workflow = PricingWorkflow.objects.create(name='Already active', is_active=True,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    response = post(staff, 'pricing_v2_workflow_activate', {'expected_version': 1}, pk=workflow.pk)
    assert response.status_code == 400
    workflow.refresh_from_db()
    assert not workflow.is_default


@pytest.mark.django_db
def test_config_cannot_override_block_identity(catalog, staff):
    candidate = blocks(catalog)
    candidate[0]['config']['type'] = 'items'
    assert post(staff, 'pricing_v2_workflow_validate', {'workflow_json': candidate}).status_code == 400


@pytest.mark.django_db
def test_versioned_editor_rejects_stale_successor_from_other_name(catalog, staff):
    source = PricingWorkflow.objects.create(name='A', version=1, is_active=False,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    PricingWorkflow.objects.create(name='B', version=2, is_active=False,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    response = post(staff, 'pricing_v2_workflow_detail', {'expected_version': 1,
        'workflow_json': [{'type': 'package'}, {'type': 'items'}]}, pk=source.pk)
    assert response.status_code == 201


@pytest.mark.django_db
def test_editor_cannot_rename_version_family(catalog, staff):
    source = PricingWorkflow.objects.create(name='A', is_active=False,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    response = post(staff, 'pricing_v2_workflow_detail', {'expected_version': 1,
        'name': 'B', 'workflow_json': source.workflow_json}, pk=source.pk)
    assert response.status_code == 400


@pytest.mark.django_db
def test_create_rejects_existing_family_name(catalog, staff):
    PricingWorkflow.objects.create(name='A', is_active=True,
        workflow_json=[{'type': 'package'}, {'type': 'items'}])
    response = post(staff, 'pricing_v2_workflows', {'name': 'A',
        'workflow_json': [{'type': 'package'}, {'type': 'items'}]})
    assert response.status_code == 400


@pytest.mark.django_db
def test_create_and_clone_reject_inactive_rule(catalog, staff):
    source = PricingWorkflow.objects.create(name='Previous', is_active=False,
        workflow_json=blocks(catalog))
    catalog[2].is_active = False
    catalog[2].save(update_fields=['is_active'])
    assert post(staff, 'pricing_v2_workflow_clone', {'expected_version': 1}, pk=source.pk).status_code == 400
    assert PricingWorkflow.objects.count() == 1
