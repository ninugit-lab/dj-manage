from datetime import date
from unittest.mock import patch

import pytest
from django.urls import reverse

from wishlist.models import BlockedClient, Event, EventStatus, SongWish
from wishlist.spotify import SpotifyService


@pytest.fixture
def staff_client(client, django_user_model):
    user = django_user_model.objects.create_user(username='dj', password='test', is_staff=True)
    client.force_login(user)
    return client


@pytest.fixture
def event(db):
    return Event.objects.create(name='Hochzeit Test', date=date(2026, 11, 28))


def test_confirm_creates_one_named_wishlist_playlist_only_once(staff_client, event):
    with patch('wishlist.spotify.SpotifyService.create_playlist', return_value=('playlist-1', 'url')) as create, \
         patch('wishlist.admin_views.GoogleService.create_calendar_event', return_value=(None, None)), \
         patch('wishlist.admin_views.GoogleService.backup_event_to_sheets', return_value=(True, 'ok')):
        url = reverse('dj_admin:event_confirm', kwargs={'pk': event.pk})
        first = staff_client.post(url)
        second = staff_client.post(url)
    event.refresh_from_db()
    assert first.json()['success'] and second.json()['success']
    assert event.spotify_playlist_id == 'playlist-1'
    assert create.call_count == 1
    assert create.call_args.args[0] == 'Hochzeit Test (28.11.2026)'


def test_failed_playlist_creation_warns_and_can_retry(staff_client, event):
    with patch('wishlist.spotify.SpotifyService.create_playlist', side_effect=[(None, 'nicht verbunden'), ('new-id', 'url')]) as create, \
         patch('wishlist.admin_views.GoogleService.create_calendar_event', return_value=(None, None)), \
         patch('wishlist.admin_views.GoogleService.backup_event_to_sheets', return_value=(True, 'ok')):
        url = reverse('dj_admin:event_confirm', kwargs={'pk': event.pk})
        first = staff_client.post(url).json()
        second = staff_client.post(url).json()
    event.refresh_from_db()
    assert any('Spotify' in warning for warning in first['warnings'])
    assert create.call_count == 2
    assert event.spotify_playlist_id == 'new-id'


def test_playlist_creation_exception_does_not_break_confirmation(staff_client, event):
    with patch('wishlist.spotify.SpotifyService.create_playlist', side_effect=RuntimeError('Spotify unavailable')), \
         patch('wishlist.admin_views.GoogleService.create_calendar_event', return_value=(None, None)), \
         patch('wishlist.admin_views.GoogleService.backup_event_to_sheets', return_value=(True, 'ok')):
        result = staff_client.post(reverse('dj_admin:event_confirm', kwargs={'pk': event.pk}))
    assert result.status_code == 200
    assert result.json()['success']
    assert Event.objects.get(pk=event.pk).status == EventStatus.CONFIRMED
    assert any('Spotify' in warning for warning in result.json()['warnings'])


def test_wishlist_frame_offers_block_per_song_without_manual_ip_form(staff_client, event):
    event.is_active = True
    event.save()
    wish = SongWish.objects.create(event=event, spotify_track_id='song-1', track_name='Song',
                                   artist_name='Artist', session_key='guest-session', ip_address='192.0.2.10')
    page = staff_client.get(reverse('dj_admin:wishlist_live')).content.decode()
    frame = staff_client.get(reverse('dj_admin:wishlist_frame', kwargs={'pk': event.pk})).content.decode()
    assert 'id="block-ip"' not in page
    assert f'blockWishlistClient({wish.pk})' in frame
    response = staff_client.post(reverse('dj_admin:block_wish_client', kwargs={'wish_id': wish.pk}),
                                 data='{"hours":24}', content_type='application/json')
    assert response.json()['success']
    assert BlockedClient.objects.filter(event=event, session_key='guest-session', is_active=True).exists()


def test_played_song_can_also_block_its_client(staff_client, event):
    wish = SongWish.objects.create(event=event, spotify_track_id='played-song', track_name='Played',
                                   artist_name='Artist', session_key='played-session', played=True)
    frame = staff_client.get(reverse('dj_admin:wishlist_frame', kwargs={'pk': event.pk})).content.decode()
    assert f'blockWishlistClient({wish.pk})' in frame


def test_expired_session_in_wishlist_frame_navigates_to_login(client, event):
    response = client.get(reverse('dj_admin:wishlist_frame', kwargs={'pk': event.pk}),
                          HTTP_TURBO_FRAME='event-wishlist')
    assert response.status_code == 302
    login = client.get(response['Location'])
    assert login.status_code == 200
    assert '<meta name="turbo-visit-control" content="reload">' in login.content.decode()


def test_playlist_creation_uses_current_spotify_endpoint():
    class Response:
        status_code = 201

        def json(self):
            return {'id': 'playlist-1', 'external_urls': {'spotify': 'https://open.spotify.com/playlist/playlist-1'}}

    with patch.object(SpotifyService, 'get_valid_token', return_value='access-token'), \
         patch.object(SpotifyService, 'get_user_id') as user_id, \
         patch('wishlist.spotify._request_with_retry', return_value=Response()) as request:
        result = SpotifyService.create_playlist('Hochzeit Test (28.11.2026)')
    assert result[0] == 'playlist-1'
    assert request.call_args.args[:2] == ('POST', 'https://api.spotify.com/v1/me/playlists')
    user_id.assert_not_called()


def test_song_block_does_not_block_other_guest_behind_same_ip(staff_client, event):
    wish = SongWish.objects.create(event=event, spotify_track_id='song-1', track_name='Song',
                                   artist_name='Artist', session_key='guest-one', ip_address='192.0.2.10')
    result = staff_client.post(reverse('dj_admin:block_wish_client', kwargs={'wish_id': wish.pk}),
                               data='{"hours":24}', content_type='application/json')
    assert result.json()['success']
    assert BlockedClient.is_blocked(session_key='guest-one', ip_address='192.0.2.10', event=event)
    assert BlockedClient.is_blocked(session_key='guest-two', ip_address='192.0.2.10', event=event) is None


def test_wishlist_frame_error_handler_is_available_for_missing_frames(staff_client, event):
    event.is_active = True
    event.save()
    page = staff_client.get(reverse('dj_admin:wishlist_live')).content.decode()
    assert 'turbo:frame-missing' in page
    assert 'event.preventDefault()' in page


def test_event_edit_has_missing_frame_handler(staff_client, event):
    page = staff_client.get(reverse('dj_admin:event_edit', kwargs={'pk': event.pk})).content.decode()
    assert 'turbo:frame-missing' in page
    assert 'event.preventDefault()' in page


def test_confirm_does_not_create_playlist_when_one_was_manually_assigned(staff_client, event):
    event.spotify_playlist_id = 'already-there'
    event.save(update_fields=['spotify_playlist_id'])
    with patch('wishlist.spotify.SpotifyService.create_playlist') as create, \
         patch('wishlist.admin_views.GoogleService.create_calendar_event', return_value=(None, None)), \
         patch('wishlist.admin_views.GoogleService.backup_event_to_sheets', return_value=(True, 'ok')):
        result = staff_client.post(reverse('dj_admin:event_confirm', kwargs={'pk': event.pk}))
    assert result.json()['success']
    create.assert_not_called()
    assert Event.objects.get(pk=event.pk).spotify_playlist_id == 'already-there'


def test_event_creation_does_not_create_spotify_playlist(staff_client):
    with patch('wishlist.spotify.SpotifyService.create_playlist') as create, \
         patch('wishlist.admin_views.GoogleService.backup_event_to_sheets', return_value=(True, 'ok')):
        result = staff_client.post(reverse('dj_admin:event_create'), {
            'name': 'Upcoming', 'date': '2026-12-01', 'status': EventStatus.INQUIRY,
        })
    assert result.status_code == 302
    create.assert_not_called()


def test_song_block_rejects_unidentifiable_guest(staff_client, event):
    wish = SongWish.objects.create(event=event, spotify_track_id='song-2', track_name='Song', artist_name='Artist')
    response = staff_client.post(reverse('dj_admin:block_wish_client', kwargs={'wish_id': wish.pk}),
                                 data='{}', content_type='application/json')
    assert response.status_code == 400
    assert not BlockedClient.objects.exists()


def test_confirmed_event_creation_does_not_bypass_playlist_confirmation(staff_client):
    with patch('wishlist.spotify.SpotifyService.create_playlist') as create, \
         patch('wishlist.admin_views.GoogleService.backup_event_to_sheets', return_value=(True, 'ok')):
        result = staff_client.post(reverse('dj_admin:event_create'), {
            'name': 'Direct confirm', 'date': '2026-12-01', 'status': EventStatus.CONFIRMED,
        })
    assert result.status_code == 302
    create.assert_not_called()
    assert Event.objects.get(name='Direct confirm').status == EventStatus.INQUIRY


def test_event_edit_cannot_confirm_without_confirmation_endpoint(staff_client, event):
    with patch('wishlist.admin_views.GoogleService.backup_event_to_sheets', return_value=(True, 'ok')):
        result = staff_client.post(reverse('dj_admin:event_edit', kwargs={'pk': event.pk}), {
            'name': event.name, 'date': '2026-11-28', 'status': EventStatus.CONFIRMED,
        })
    assert result.status_code == 302
    assert Event.objects.get(pk=event.pk).status == EventStatus.INQUIRY


def test_manual_ip_block_endpoint_is_not_exposed(staff_client):
    response = staff_client.post('/dj-admin/api/block/', data='{"ip_address":"192.0.2.10"}',
                                 content_type='application/json')
    assert response.status_code == 404


def test_wishlist_frame_marks_blocked_client_and_hides_repeat_block_button(staff_client, event):
    wish = SongWish.objects.create(event=event, spotify_track_id='song-blocked',
                                   track_name='Blocked Song', artist_name='Artist',
                                   session_key='blocked-session', ip_address='192.0.2.10')
    BlockedClient.objects.create(event=event, session_key='blocked-session', reason='Spam', is_active=True)
    response = staff_client.get(reverse('dj_admin:wishlist_frame', kwargs={'pk': event.pk}))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'Client gesperrt' in content
    assert f'blockWishlistClient({wish.pk})' not in content


def test_expired_block_does_not_hide_block_button(staff_client, event):
    from datetime import timedelta
    from django.utils import timezone

    wish = SongWish.objects.create(event=event, spotify_track_id='song-expired',
                                   track_name='Expired Block', artist_name='Artist', session_key='guest-expired')
    BlockedClient.objects.create(event=event, session_key='guest-expired', is_active=True,
                                 expires_at=timezone.now() - timedelta(minutes=1))
    response = staff_client.get(reverse('dj_admin:wishlist_frame', kwargs={'pk': event.pk}))
    assert f'blockWishlistClient({wish.pk})' in response.content.decode()


def test_event_edit_has_working_fallback_for_song_block(staff_client, event):
    wish = SongWish.objects.create(event=event, spotify_track_id='song-edit',
                                   track_name='Edit Song', artist_name='Artist', session_key='guest-edit')
    page = staff_client.get(reverse('dj_admin:event_edit', kwargs={'pk': event.pk})).content.decode()
    frame = staff_client.get(reverse('dj_admin:wishlist_frame', kwargs={'pk': event.pk})).content.decode()
    assert 'function blockWishlistClient' in page
    assert f'blockWishlistClient({wish.pk})' in frame
