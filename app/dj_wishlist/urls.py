from django.contrib.auth import views as auth_views
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve

urlpatterns = [
    path('dj-admin/login/', auth_views.LoginView.as_view(
        template_name='dj_admin/login.html',
        extra_context={'site_header': 'DJ Redoo', 'title': 'Anmelden'},
        redirect_authenticated_user=True,
    ), name='login'),
    path('dj-admin/', include('wishlist.admin_urls')),
    path('', include('wishlist.urls')),
]

# Serve media files in all environments (Whitenoise only handles static, not media)
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
]
