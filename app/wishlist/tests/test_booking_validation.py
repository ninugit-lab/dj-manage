import json
from datetime import timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.core.cache import cache
from django.utils import timezone

from wishlist.models import AppConfig


def _valid_payload(**overrides):
    """Basis-Payload, der alle Pflichtfelder erfüllt."""
    payload = {
        'name': 'Hochzeit Mustermann',
        'date': (timezone.now().date() + timedelta(days=14)).isoformat(),
        'location': 'Duisburg',
        'client_name': 'Max Mustermann',
        'client_email': 'max@example.com',
        'privacy_consent': True,
    }
    payload.update(overrides)
    return payload


class BookingDateValidationTest(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()
        cfg = AppConfig.load()
        cfg.event_form_enabled = True
        cfg.save()

    def _post(self, payload):
        return self.client.post(
            reverse('submit_event'),
            json.dumps(payload),
            content_type='application/json',
        )

    def test_past_date_rejected(self):
        yesterday = (timezone.now().date() - timedelta(days=1)).isoformat()
        resp = self._post(_valid_payload(date=yesterday))
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertFalse(body['success'])
        self.assertIn('Vergangene Daten', body['error'])

    def test_future_date_passes_date_check(self):
        resp = self._post(_valid_payload())
        body = resp.json()
        if resp.status_code == 400:
            self.assertNotIn('Vergangene Daten', body.get('error', ''))

    def test_check_date_past_unavailable(self):
        yesterday = (timezone.now().date() - timedelta(days=1)).isoformat()
        resp = self.client.get(reverse('check_date'), {'date': yesterday})
        body = resp.json()
        self.assertFalse(body['available'])
        self.assertIn('Vergangene Daten', body.get('reason', ''))


class BookingTimeStepValidationTest(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()
        cfg = AppConfig.load()
        cfg.event_form_enabled = True
        cfg.save()

    def _post(self, payload):
        return self.client.post(
            reverse('submit_event'),
            json.dumps(payload),
            content_type='application/json',
        )

    def test_time_start_offgrid_rejected(self):
        resp = self._post(_valid_payload(time_start='14:15'))
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertFalse(body['success'])
        self.assertIn('30-Minuten-Schritten', body['error'])

    def test_time_end_offgrid_rejected(self):
        resp = self._post(_valid_payload(time_start='14:00', time_end='23:45'))
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertFalse(body['success'])
        self.assertIn('30-Minuten-Schritten', body['error'])

    def test_time_on_grid_passes(self):
        for t in ('00:00', '14:00', '14:30', '23:30'):
            cache.clear()
            resp = self._post(_valid_payload(time_start=t, time_end='23:30'))
            body = resp.json()
            if resp.status_code == 400:
                self.assertNotIn('30-Minuten-Schritten', body.get('error', ''),
                                 msg=f'{t} wurde fälschlich abgelehnt')
