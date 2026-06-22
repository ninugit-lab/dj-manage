from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Event, SongWish, SpotifyToken, GoogleToken,
    AppConfig, PriceItem, EventPriceCalculation, EmailLog,
)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['name', 'date', 'status_badge', 'location', 'client_name', 'is_active', 'wish_count']
    list_filter = ['status', 'is_active', 'date', 'event_type']
    search_fields = ['name', 'location', 'client_name', 'client_email']
    list_editable = ['is_active']
    readonly_fields = ['admin_token', 'google_calendar_event_id', 'created_at', 'updated_at']
    fieldsets = (
        ('Event', {'fields': ('name', 'date', 'time_start', 'time_end', 'location', 'address', 'event_type', 'description', 'cover_image_url', 'status', 'is_active')}),
        ('Auftraggeber', {'fields': ('client_name', 'client_email', 'client_phone', 'client_company', 'client_notes')}),
        ('Wishlist', {'fields': ('spotify_playlist_id', 'max_wishes_per_session', 'wishlist_show_cover', 'wishlist_show_artist', 'wishlist_show_preview', 'wishlist_show_duration', 'wishlist_require_name', 'wishlist_show_played', 'wishlist_custom_message')}),
        ('Preis', {'fields': ('total_price', 'price_notes')}),
        ('Details', {'fields': ('guest_count', 'special_requests', 'admin_token', 'google_calendar_event_id', 'created_at', 'updated_at')}),
    )

    def wish_count(self, obj):
        c = obj.wishes.count()
        return format_html('<b>{}</b>', c)
    wish_count.short_description = 'Wünsche'

    def status_badge(self, obj):
        colors = {'inquiry': '#a855f7', 'confirmed': '#1db954', 'past': '#5a5a7a', 'cancelled': '#ff3b5c'}
        color = colors.get(obj.status, '#5a5a7a')
        return format_html('<span style="color:{};font-weight:600">{}</span>', color, obj.get_status_display())
    status_badge.short_description = 'Status'


@admin.register(SongWish)
class SongWishAdmin(admin.ModelAdmin):
    list_display = ['cover_thumb', 'track_name', 'artist_name', 'guest_name', 'event', 'played', 'created_at']
    list_filter = ['event', 'played']
    search_fields = ['track_name', 'artist_name', 'guest_name']
    list_editable = ['played']
    readonly_fields = ['spotify_track_id', 'album_cover_url', 'duration_ms', 'preview_url', 'session_key', 'created_at']

    def cover_thumb(self, obj):
        if obj.album_cover_url:
            return format_html('<img src="{}" style="width:40px;height:40px;border-radius:4px;object-fit:cover;" />', obj.album_cover_url)
        return '🎵'
    cover_thumb.short_description = ''


@admin.register(SpotifyToken)
class SpotifyTokenAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'expires_at', 'is_valid']
    readonly_fields = ['updated_at']
    def is_valid(self, obj):
        return format_html('<span style="color:{}">{}</span>', 'green' if not obj.is_expired else 'red', '✅' if not obj.is_expired else '❌')
    is_valid.short_description = 'Status'


@admin.register(GoogleToken)
class GoogleTokenAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'expires_at', 'is_valid']
    readonly_fields = ['updated_at']
    def is_valid(self, obj):
        return format_html('<span style="color:{}">{}</span>', 'green' if not obj.is_expired else 'red', '✅' if not obj.is_expired else '❌')
    is_valid.short_description = 'Status'


@admin.register(AppConfig)
class AppConfigAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'dj_name', 'updated_at']

    def has_add_permission(self, request):
        return not AppConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PriceItem)
class PriceItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'is_default', 'is_required', 'is_active', 'sort_order']
    list_filter = ['category', 'is_active', 'is_required']
    list_editable = ['price', 'is_default', 'is_required', 'is_active', 'sort_order']


@admin.register(EventPriceCalculation)
class EventPriceCalculationAdmin(admin.ModelAdmin):
    list_display = ['event', 'subtotal', 'discount_percent', 'total']
    readonly_fields = ['created_at']


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ['sent_at', 'recipient', 'subject', 'success']
    list_filter = ['success']
    readonly_fields = ['event', 'recipient', 'subject', 'body', 'sent_at', 'success', 'error_message']

    def has_add_permission(self, request):
        return False
