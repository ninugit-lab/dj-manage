"""Behavioral contracts for staff-owned global profile versions."""
import copy
import json

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from wishlist.models import AppConfig, PricingProfile


CONFIG = {
    'included_hours': '4', 'extra_hour_rate': '125.50',
    'setup_minutes': 30, 'teardown_minutes': 20,
    'setup_teardown_hour_rate': '80', 'included_km': '25', 'km_rate': '0.65',
    'saturday_percent': '10', 'sunday_percent': '20', 'holiday_percent': '30',
    'holiday_region': 'NW', 'seasons': [
        {'identifier': 'winter', 'label': 'Winter', 'start': '12-01', 'end': '02-28', 'percent': '15'},
    ], 'lead_time_tiers': [
        {'lower_bound': 0, 'upper_bound': 6, 'value': '25'},
        {'lower_bound': 7, 'upper_bound': None, 'value': '0'},
    ], 'guest_tiers': [
        {'lower_bound': 0, 'upper_bound': 49, 'value': '0'},
        {'lower_bound': 50, 'upper_bound': None, 'value': '10'},
    ], 'minimum_price': '500', 'rounding_mode': 'nearest', 'rounding_step': '0.05',
}


@pytest.fixture
def staff(client):
    user = get_user_model().objects.create_user('staff', password='secret', is_staff=True)
    client.force_login(user)
    return client


def profile(**kwargs):
    return PricingProfile.objects.create(
        name=kwargs.pop('name', 'Global'), version=kwargs.pop('version', 1),
        status=kwargs.pop('status', PricingProfile.Status.ACTIVE),
        valid_from=timezone.now(), config_json=kwargs.pop('config_json', copy.deepcopy(CONFIG)),
        **kwargs,
    )


def payload(base=None, **changes):
    data = {'name': 'Global', 'config_json': copy.deepcopy(CONFIG),
            'expected_profile_id': base.pk if base else None,
            'expected_version': base.version if base else None}
    data.update(changes)
    return data


def post(client, route, data):
    return client.post(reverse('dj_admin:' + route), data=json.dumps(data), content_type='application/json')


@pytest.mark.django_db
def test_staff_page_shows_active_draft_versions_and_escaped_config(staff):
    active = profile(config_json={**CONFIG, 'holiday_region': '<script>alert(1)</script>'})
    draft = profile(version=2, status='draft')
    response = staff.get(reverse('dj_admin:pricing_v2_profile'))
    assert response.status_code == 200
    content = response.content.decode()
    assert str(active.version) in content and str(draft.version) in content
    assert '<script>alert(1)</script>' not in content
    for key in CONFIG:
        assert f'name="{key}"' in content


@pytest.mark.django_db
def test_empty_profile_page_shows_complete_valid_editable_defaults(staff):
    from wishlist.pricing_v2.config import _FIELDS, validate_profile_config

    response = staff.get(reverse('dj_admin:pricing_v2_profile'))
    assert response.status_code == 200
    defaults = response.context['initial_config']
    assert set(defaults) == set(_FIELDS)
    normalized = validate_profile_config(defaults)
    assert normalized.holiday_region == 'NW'
    assert normalized.included_hours == 0
    assert normalized.rounding_mode == 'nearest'
    assert normalized.rounding_step > 0
    assert normalized.seasons == normalized.lead_time_tiers == normalized.guest_tiers == ()
    assert normalized.setup_minutes == 30 and normalized.teardown_minutes == 30
    assert 'editierbare startwerte' in response.content.decode().lower()
    assert PricingProfile.objects.count() == 0


@pytest.mark.django_db
def test_staff_can_submit_first_draft_using_rendered_defaults(staff):
    response = staff.get(reverse('dj_admin:pricing_v2_profile'))
    config = response.context['initial_config']
    created = post(staff, 'pricing_v2_profile', {
        'name': 'Global', 'config_json': config,
        'expected_profile_id': None, 'expected_version': None,
    })
    assert created.status_code == 201
    first = PricingProfile.objects.get()
    assert first.version == 1 and first.status == 'draft'


@pytest.mark.django_db
def test_save_clones_active_as_draft_without_mutating_it(staff):
    active = profile()
    config = copy.deepcopy(CONFIG)
    config['included_hours'] = '6'
    response = post(staff, 'pricing_v2_profile', payload(active, config_json=config))
    assert response.status_code == 201
    active.refresh_from_db()
    draft = PricingProfile.objects.get(version=2)
    assert active.status == 'active' and active.config_json['included_hours'] == '4'
    assert draft.status == 'draft' and draft.config_json['included_hours'] == '6'
    assert draft.created_by == response.wsgi_request.user


@pytest.mark.django_db
def test_two_editors_stale_save_is_conflict(staff):
    active = profile()
    first = post(staff, 'pricing_v2_profile', payload(active))
    assert first.status_code == 201
    second = post(staff, 'pricing_v2_profile', payload(active))
    assert second.status_code == 409
    assert 'veraltet' in second.json()['error'].lower()
    assert PricingProfile.objects.count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize('key,value', [
    ('rounding_step', '0'), ('rounding_mode', 'ceil'),
    ('guest_tiers', [{'lower_bound': 0, 'upper_bound': 4, 'value': '1'},
                     {'lower_bound': 4, 'upper_bound': None, 'value': '2'}]),
    ('seasons', [{'identifier': 'a', 'label': 'A', 'start': '12-01', 'end': '02-01', 'percent': '1'},
                 {'identifier': 'b', 'label': 'B', 'start': '02-01', 'end': '03-01', 'percent': '2'}]),
])
def test_invalid_config_returns_field_error_without_writing(staff, key, value):
    config = copy.deepcopy(CONFIG)
    config[key] = value
    response = post(staff, 'pricing_v2_validate', {'config_json': config})
    assert response.status_code == 400
    assert key in response.json()['errors']
    response = post(staff, 'pricing_v2_profile', payload(config_json=config))
    assert response.status_code == 400
    assert key in response.json()['errors']
    assert PricingProfile.objects.count() == 0


@pytest.mark.django_db
def test_preview_valid_config_and_activate_draft_without_changing_rollout(staff):
    active = profile()
    draft = profile(version=2, status='draft')
    mode = AppConfig.load().pricing_engine_mode
    preview = post(staff, 'pricing_v2_validate', {'config_json': CONFIG})
    assert preview.status_code == 200 and preview.json()['valid'] is True
    response = post(staff, 'pricing_v2_activate', {
        'profile_id': draft.pk, 'expected_profile_id': active.pk,
        'expected_version': active.version,
    })
    assert response.status_code == 200
    active.refresh_from_db()
    draft.refresh_from_db()
    assert active.status == 'retired' and draft.status == 'active'
    assert AppConfig.load().pricing_engine_mode == mode


@pytest.mark.django_db
def test_activation_rejects_stale_token_and_invalid_draft_without_retiring_active(staff):
    active = profile()
    draft = profile(version=2, status='draft')
    request = {'profile_id': draft.pk, 'expected_profile_id': active.pk,
               'expected_version': active.version}
    assert post(staff, 'pricing_v2_activate', {**request, 'expected_version': 0}).status_code == 409
    draft.config_json = {**CONFIG, 'rounding_step': '0'}
    draft.save()
    response = post(staff, 'pricing_v2_activate', request)
    assert response.status_code == 400 and 'rounding_step' in response.json()['errors']
    active.refresh_from_db()
    assert active.status == 'active'


@pytest.mark.django_db
def test_database_guards_single_active_even_when_second_insert_fails():
    profile()
    with pytest.raises(IntegrityError), transaction.atomic():
        profile(version=2)
    assert PricingProfile.objects.filter(status='active').count() == 1


@pytest.mark.django_db
def test_only_staff_can_access_page_and_api(client):
    urls = ['pricing_v2_profile', 'pricing_v2_validate', 'pricing_v2_activate']
    for route in urls:
        response = client.get(reverse('dj_admin:' + route)) if route == urls[0] else post(client, route, {})
        assert response.status_code == 302 and '/dj-admin/login/' in response.url
    user = get_user_model().objects.create_user('ordinary', password='secret')
    client.force_login(user)
    for route in urls:
        response = client.get(reverse('dj_admin:' + route)) if route == urls[0] else post(client, route, {})
        assert response.status_code == 302


@pytest.mark.django_db
def test_csrf_required_for_all_writes():
    from django.test import Client
    user = get_user_model().objects.create_user('staff', password='secret', is_staff=True)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    for route in ('pricing_v2_profile', 'pricing_v2_validate', 'pricing_v2_activate'):
        assert post(client, route, {}).status_code == 403


@pytest.mark.django_db
def test_post_rejects_malformed_json_and_missing_keys_without_traceback(staff):
    for route in ('pricing_v2_profile', 'pricing_v2_validate', 'pricing_v2_activate'):
        response = staff.post(reverse('dj_admin:' + route), data='{broken', content_type='application/json')
        assert response.status_code == 400 and response.json()['errors']
    response = post(staff, 'pricing_v2_validate', {'config_json': {'rounding_step': '1'}})
    assert response.status_code == 400 and 'config_json' in response.json()['errors']


@pytest.mark.django_db
def test_form_post_can_create_first_draft(staff):
    data = {key: json.dumps(value) if isinstance(value, (list, int)) else value
            for key, value in CONFIG.items()}
    data.update(name='Global', expected_profile_id='null', expected_version='null')
    response = staff.post(reverse('dj_admin:pricing_v2_profile'), data=data)
    assert response.status_code == 201
    assert PricingProfile.objects.get().status == 'draft'


@pytest.mark.django_db
def test_activate_only_draft_never_retired_or_active(staff):
    active = profile()
    retired = profile(version=2, status='retired')
    response = post(staff, 'pricing_v2_activate', {
        'profile_id': retired.pk, 'expected_profile_id': active.pk,
        'expected_version': active.version,
    })
    assert response.status_code == 400
    assert PricingProfile.objects.get(pk=active.pk).status == 'active'


@pytest.mark.django_db
def test_activation_rolls_back_retirement_if_active_constraint_rejects_new_active(staff):
    active = profile()
    draft = profile(version=2, status='draft')
    # An independent competing active row inserted after the initial status
    # update makes the target's promotion violate the DB's unique constraint.
    from unittest.mock import patch
    original_save = PricingProfile.save

    def competing_save(instance, *args, **kwargs):
        if instance.pk == draft.pk and instance.status == 'active':
            PricingProfile.objects.create(
                name='Competitor', version=3, status='active',
                valid_from=timezone.now(), config_json=copy.deepcopy(CONFIG),
            )
        return original_save(instance, *args, **kwargs)

    with patch.object(PricingProfile, 'save', competing_save):
        response = post(staff, 'pricing_v2_activate', {
            'profile_id': draft.pk, 'expected_profile_id': active.pk,
            'expected_version': active.version,
        })
    assert response.status_code == 409
    active.refresh_from_db()
    draft.refresh_from_db()
    assert active.status == 'active' and draft.status == 'draft'
    assert PricingProfile.objects.filter(status='active').count() == 1
