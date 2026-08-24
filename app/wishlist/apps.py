import os

from django.apps import AppConfig


class WishlistConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wishlist'
    verbose_name = 'DJ Wishlist'

    def ready(self):
        # Nicht bei Management-Kommandos (migrate, collectstatic, Tests) starten.
        if os.environ.get('RUN_MAIN') == 'false':
            return
        if os.environ.get('DJANGO_DISABLE_SCHEDULER'):
            return
        import sys
        if os.path.basename(sys.argv[0]).startswith('pytest'):
            return
        if len(sys.argv) > 1 and sys.argv[1] in {
            'migrate', 'makemigrations', 'collectstatic', 'test', 'shell',
            'createsuperuser', 'clearsessions', 'loaddata', 'dumpdata',
        }:
            return
        from . import scheduler
        scheduler.start()
