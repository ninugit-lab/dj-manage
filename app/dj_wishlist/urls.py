from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve

admin.site.site_header = "DJ Wishlist Admin"
admin.site.site_title = "DJ Wishlist"
admin.site.index_title = "Event Management"

urlpatterns = [
    # Shadowt den Django-Admin-Login: ohne ?next= geht es zum DJ-Dashboard statt /admin/
    path('admin/login/', auth_views.LoginView.as_view(
        template_name='admin/login.html',
        extra_context={'site_header': admin.site.site_header, 'title': 'Anmelden'},
        redirect_authenticated_user=True,
    ), name='login'),
    path('admin/', admin.site.urls),
    path('dj-admin/', include('wishlist.admin_urls')),
    path('', include('wishlist.urls')),
]

# Serve media files in all environments (Whitenoise only handles static, not media)
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
