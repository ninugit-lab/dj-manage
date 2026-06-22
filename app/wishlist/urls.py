from django.urls import path
from . import views

urlpatterns = [
    # Public Wishlist
    path('', views.wishlist_view, name='wishlist'),
    path('api/search/', views.search_tracks, name='search_tracks'),
    path('api/wish/', views.add_wish, name='add_wish'),
    path('api/wishes-stream/', views.wishes_stream, name='wishes_stream'),

    # Public Event Form
    path('buchen/', views.event_form_view, name='event_form'),
    path('api/check-date/', views.check_date_availability, name='check_date'),
    path('api/submit-event/', views.submit_event_form, name='submit_event'),
    path('api/price-estimate/', views.api_price_estimate, name='price_estimate'),

    # Spotify: Now Playing + Artist Top Tracks
    path('api/now-playing/', views.api_now_playing, name='now_playing'),
    path('api/artist-top-tracks/', views.artist_top_tracks, name='artist_top_tracks'),

    # Spotify OAuth
    path('spotify/login/', views.spotify_login, name='spotify_login'),
    path('spotify/callback/', views.spotify_callback, name='spotify_callback'),
    path('spotify/status/', views.spotify_status, name='spotify_status'),

    # Google OAuth
    path('google/login/', views.google_login, name='google_login'),
    path('google/callback/', views.google_callback, name='google_callback'),
    path('google/status/', views.google_status, name='google_status'),
]
