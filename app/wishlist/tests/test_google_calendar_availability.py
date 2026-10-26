import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from wishlist.models import AppConfig, Event
from wishlist.google_services import GoogleService


@pytest.fixture
def booking_date(db):
    cache.clear()
    config = AppConfig.load()
    config.event_form_enabled = True
    config.save(update_fields=['event_form_enabled'])
    return (timezone.localdate() + timedelta(days=15)).isoformat()


def _booking_payload(date):
    return dict(name='Feier', date=date, location='Duisburg', client_name='Gast',
                client_email='gast@example.com', privacy_consent=True)


def test_existing_google_event_blocks_date_preview(client, booking_date):
    with patch.object(GoogleService, 'check_date_available', return_value=(False, [{'id': 'busy'}])):
        response = client.get(reverse('check_date'), {'date': booking_date})
    assert response.json()['available'] is False


def test_existing_google_event_blocks_post_even_if_preview_was_skipped(client, booking_date):
    with patch.object(GoogleService, 'check_date_available', return_value=(False, [{'id': 'busy'}])):
        response = client.post(reverse('submit_event'), data=json.dumps(_booking_payload(booking_date)),
                               content_type='application/json')
    assert response.status_code == 409
    assert Event.objects.filter(date=booking_date).count() == 0


def test_internal_confirmed_event_blocks_submission(client, booking_date):
    Event.objects.create(name='Already booked', date=date.fromisoformat(booking_date), status='confirmed')
    response = client.post(reverse('submit_event'), data=json.dumps(_booking_payload(booking_date)),
                           content_type='application/json')
    assert response.status_code == 409


def test_calendar_failure_does_not_report_date_as_free(client, booking_date):
    with patch.object(GoogleService, 'check_date_available', side_effect=RuntimeError('offline')):
        preview = client.get(reverse('check_date'), {'date': booking_date})
        submit = client.post(reverse('submit_event'), data=json.dumps(_booking_payload(booking_date)),
                             content_type='application/json')
    assert preview.json()['available'] is False
    assert submit.status_code == 503
    assert Event.objects.filter(date=booking_date).count() == 0


def test_calendar_day_boundaries_follow_berlin_timezone(booking_date):
    with patch.object(GoogleService, 'get_calendar_events', return_value=[]) as events:
        available, _ = GoogleService.check_date_available(booking_date)
    assert available
    assert events.call_args.kwargs['time_min'].startswith(booking_date + 'T00:00:00+')
    assert not events.call_args.kwargs['time_max'].startswith(booking_date + 'T23:59:59Z')


def test_calendar_api_failure_is_not_empty_calendar(booking_date):
    with patch.object(GoogleService, '_api_call', return_value=None):
        with pytest.raises(RuntimeError):
            GoogleService.check_date_available(booking_date)


def test_google_calendar_result_beyond_first_page_blocks_date(booking_date):
    class Response:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    first = Response({'items': [{'transparency': 'transparent'}], 'nextPageToken': 'page-2'})
    second = Response({'items': [{'id': 'busy'}]})
    with patch.object(GoogleService, '_api_call', side_effect=[first, second]) as fetch:
        available, blocks = GoogleService.check_date_available(booking_date)
    assert available is False
    assert blocks[0]['id'] == 'busy'
    assert fetch.call_count == 2
    assert fetch.call_args.kwargs['params']['pageToken'] == 'page-2'


def test_booking_form_does_not_enable_continue_when_calendar_request_fails(client, booking_date):
    response = client.get(reverse('event_form'))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'Prüfung nicht möglich – bitte erneut versuchen' in content
    assert 'dateAvailable = false' in content


def test_admin_calendar_shows_container_before_render(client, django_user_model):
    user = django_user_model.objects.create_user(username='calendar-admin', password='test', is_staff=True)
    client.force_login(user)
    response = client.get(reverse('dj_admin:calendar'))
    script = response.content.decode().split('function initCalendar()', 1)[1]
    render_position = script.index('cal.render()')
    show_position = script.index("el.classList.remove('hidden')")
    assert show_position < render_position
