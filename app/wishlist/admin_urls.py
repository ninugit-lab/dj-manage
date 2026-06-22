from django.urls import path
from . import admin_views

app_name = 'dj_admin'

urlpatterns = [
    path('', admin_views.admin_dashboard, name='dashboard'),

    # Wishlist Live View
    path('wishlist/', admin_views.admin_wishlist_view, name='wishlist_live'),

    # Events
    path('events/new/', admin_views.event_create, name='event_create'),
    path('events/<int:pk>/edit/', admin_views.event_edit, name='event_edit'),
    path('events/<int:pk>/delete/', admin_views.event_delete, name='event_delete'),
    path('events/<int:pk>/confirm/', admin_views.event_confirm, name='event_confirm'),
    path('events/<int:pk>/set-active/', admin_views.event_set_active, name='event_set_active'),

    # Wishlist Admin (Turbo Frame)
    path('events/<int:pk>/wishlist/', admin_views.admin_wishlist_frame, name='wishlist_frame'),
    path('wishes/<int:wish_id>/toggle/', admin_views.toggle_wish_played, name='toggle_wish'),
    path('wishes/<int:wish_id>/delete/', admin_views.delete_wish, name='delete_wish'),

    # Config
    path('config/', admin_views.config_page, name='config'),

    # Calendar
    path('calendar/', admin_views.calendar_view, name='calendar'),
    path('api/calendar-events/', admin_views.calendar_events_api, name='calendar_events_api'),

    # Spotify
    path('api/devices/', admin_views.api_devices, name='devices'),
    path('wishes/<int:wish_id>/audio-features/', admin_views.api_audio_features, name='audio_features'),

    # Client Blocking
    path('api/blocked-frame/', admin_views.blocked_clients_frame, name='blocked_frame'),
    path('api/block/', admin_views.block_client, name='block_client'),
    path('api/unblock/<int:block_id>/', admin_views.unblock_client, name='unblock_client'),
    path('wishes/<int:wish_id>/block/', admin_views.block_wish_client, name='block_wish_client'),

    # Pricing API — Preis-Posten
    path('api/price/items/', admin_views.api_price_items, name='price_items'),
    path('api/price/items/<int:pk>/', admin_views.api_price_item_detail, name='price_item_detail'),

    # Pricing API
    path('api/price/calculate/', admin_views.api_price_calculate, name='price_calculate'),
    path('api/price/rules/', admin_views.api_pricing_rules, name='pricing_rules'),
    path('api/price/rules/<int:pk>/', admin_views.api_pricing_rule_detail, name='pricing_rule_detail'),
    path('api/price/packages/', admin_views.api_pricing_packages, name='pricing_packages'),
    path('api/price/packages/<int:pk>/', admin_views.api_pricing_package_detail, name='pricing_package_detail'),
    path('api/price/formulas/', admin_views.api_pricing_formulas, name='pricing_formulas'),
    path('api/price/formulas/<int:pk>/', admin_views.api_pricing_formula_detail, name='pricing_formula_detail'),

    # Workflow Builder
    path('workflow/', admin_views.workflow_builder, name='workflow_builder'),
    path('api/price/workflows/', admin_views.api_pricing_workflows, name='pricing_workflows'),
    path('api/price/workflows/<int:pk>/', admin_views.api_pricing_workflow_detail, name='pricing_workflow_detail'),
    path('api/price/workflow-context/', admin_views.api_workflow_context, name='workflow_context'),
]
