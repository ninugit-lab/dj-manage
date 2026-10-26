import base64
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest
from django.test import override_settings

from wishlist.models import AppConfig
from wishlist.spotify import SpotifyService


class FakeResponse:
    status_code = 200
    text = ''

    def json(self):
        return {'access_token': 'test-token', 'expires_in': 3600}


@pytest.mark.django_db
@override_settings(SPOTIFY_CLIENT_ID='old-client', SPOTIFY_CLIENT_SECRET='old-secret',
                   SPOTIFY_REDIRECT_URI='https://old.example/callback/')
def test_authorization_uses_saved_web_credentials():
    config = AppConfig.load()
    config.spotify_client_id = 'new-client'
    config.spotify_client_secret = 'new-secret'
    config.spotify_redirect_uri = 'https://app.dj-redoo.de/spotify/callback/'
    config.save()
    params = parse_qs(urlsplit(SpotifyService.get_auth_url(state='state')).query)
    assert params['client_id'] == ['new-client']
    assert params['redirect_uri'] == ['https://app.dj-redoo.de/spotify/callback/']


@pytest.mark.django_db
@override_settings(SPOTIFY_CLIENT_ID='old-client', SPOTIFY_CLIENT_SECRET='old-secret',
                   SPOTIFY_REDIRECT_URI='https://old.example/callback/')
def test_token_exchange_uses_same_saved_credentials():
    config = AppConfig.load()
    config.spotify_client_id = 'new-client'
    config.spotify_client_secret = 'new-secret'
    config.spotify_redirect_uri = 'https://app.dj-redoo.de/spotify/callback/'
    config.save()
    with patch('wishlist.spotify._request_with_retry', return_value=FakeResponse()) as request:
        assert SpotifyService.exchange_code('code')['access_token'] == 'test-token'
    assert request.call_args.kwargs['data']['redirect_uri'] == 'https://app.dj-redoo.de/spotify/callback/'
    auth = request.call_args.kwargs['headers']['Authorization'].removeprefix('Basic ')
    assert base64.b64decode(auth).decode() == 'new-client:new-secret'


@pytest.mark.django_db
@override_settings(SPOTIFY_CLIENT_ID='old-client', SPOTIFY_CLIENT_SECRET='old-secret',
                   SPOTIFY_REDIRECT_URI='https://old.example/callback/')
def test_empty_web_config_falls_back_to_environment():
    params = parse_qs(urlsplit(SpotifyService.get_auth_url()).query)
    assert params['client_id'] == ['old-client']
    assert params['redirect_uri'] == ['https://old.example/callback/']


@pytest.mark.django_db
@override_settings(SPOTIFY_CLIENT_ID='old-client', SPOTIFY_CLIENT_SECRET='old-secret',
                   SPOTIFY_REDIRECT_URI='https://old.example/callback/')
def test_partial_web_config_must_not_mix_environment_credentials():
    config = AppConfig.load()
    config.spotify_client_id = 'new-client'
    config.save()
    with pytest.raises(ValueError, match='Spotify'):
        SpotifyService.get_auth_url()


@pytest.mark.django_db
@override_settings(SPOTIFY_CLIENT_ID='', SPOTIFY_CLIENT_SECRET='')
def test_search_accepts_saved_web_credentials_without_environment():
    config = AppConfig.load()
    config.spotify_client_id = 'new-client'
    config.spotify_client_secret = 'new-secret'
    config.spotify_redirect_uri = 'https://app.dj-redoo.de/spotify/callback/'
    config.save()
    SpotifyService._cc_token = None
    SpotifyService._cc_expires_at = 0
    with patch('wishlist.spotify._request_with_retry', return_value=FakeResponse()) as request:
        assert SpotifyService.search_tracks('test') == []
    assert request.call_count == 2  # token and search
    auth = request.call_args_list[0].kwargs['headers']['Authorization'].removeprefix('Basic ')
    assert base64.b64decode(auth).decode() == 'new-client:new-secret'


@pytest.mark.django_db
@override_settings(SPOTIFY_CLIENT_ID='old-client', SPOTIFY_CLIENT_SECRET='old-secret',
                   SPOTIFY_REDIRECT_URI='https://old.example/callback/')
def test_search_client_token_rotates_after_web_config_changes():
    config = AppConfig.load()
    config.spotify_client_id = 'new-client'
    config.spotify_client_secret = 'new-secret'
    config.spotify_redirect_uri = 'https://app.dj-redoo.de/spotify/callback/'
    config.save()
    SpotifyService._cc_token = 'old-token'
    SpotifyService._cc_expires_at = 9999999999
    SpotifyService._cc_credentials_hash = 'old-config-hash'
    try:
        with patch('wishlist.spotify._request_with_retry', return_value=FakeResponse()) as request:
            assert SpotifyService._get_client_credentials_token() == 'test-token'
        auth = request.call_args.kwargs['headers']['Authorization'].removeprefix('Basic ')
        assert base64.b64decode(auth).decode() == 'new-client:new-secret'
    finally:
        SpotifyService._cc_token = None
        SpotifyService._cc_expires_at = 0
        SpotifyService._cc_credentials_hash = None
