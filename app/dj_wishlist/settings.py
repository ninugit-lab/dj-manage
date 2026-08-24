import os
import shutil as _shutil
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'dev-secret-key-change-in-production'
    else:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY muss gesetzt sein, wenn DEBUG=False '
            '(Sessions laufen ueber signierte Cookies).'
        )

LOGIN_REDIRECT_URL = '/dj-admin/'
LOGIN_URL = '/admin/login/'

_secure_cookies = os.environ.get('SECURE_COOKIES', 'False') == 'True'
CSRF_COOKIE_SECURE = _secure_cookies
SESSION_COOKIE_SECURE = _secure_cookies

# Hinter Reverse-Proxy/Cloudflare: Original-Schema aus Header lesen
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'False') == 'True'
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0

# Zusaetzliche Origins via Env (kommagetrennt), z.B. fuer lokale Dev-Ports
_csrf_env = os.environ.get('CSRF_TRUSTED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = ["https://app.dj-redoo.de"] + [
    o.strip() for o in _csrf_env.split(',') if o.strip()
]
if DEBUG:
    CSRF_TRUSTED_ORIGINS += ["http://localhost:8500", "http://127.0.0.1:8500"]

_hosts_env = os.environ.get('ALLOWED_HOSTS', '')
if _hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in _hosts_env.split(',') if h.strip()]
else:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver', '100.74.102.46'] if DEBUG else []

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'tailwind',
    'theme',
    'wishlist',
]

TAILWIND_APP_NAME = 'theme'
INTERNAL_IPS = ['127.0.0.1']
NPM_BIN_PATH = _shutil.which('npm') or '/usr/bin/npm'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'dj_wishlist.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'dj_wishlist.wsgi.application'

# ── Database ─────────────────────────────────────────────────────────────────
# SQLite with WAL mode for concurrent reads (set via pragmas)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        
    }
}

# Workaround: SQLite pragmas via connection signal since OPTIONS.init_command
# is not supported by Django's SQLite backend
from django.db.backends.signals import connection_created
def _set_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA cache_size=-20000;")
        cursor.execute("PRAGMA temp_store=MEMORY;")
connection_created.connect(_set_sqlite_pragmas)

# ── Static & Media ───────────────────────────────────────────────────────────
# Django-Default wäre America/Chicago — die App rechnet aber durchgehend
# mit deutschen Terminen (Eventdatum, Bewertungsanfragen, Anzeige im Admin).
LANGUAGE_CODE = 'de-de'
TIME_ZONE = os.environ.get('TZ', 'Europe/Berlin')
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
_static_dir = BASE_DIR / 'static'
STATICFILES_DIRS = [_static_dir] if _static_dir.exists() and _static_dir != STATIC_ROOT else []

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Whitenoise: serve static with far-future cache headers
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# ── Sessions ─────────────────────────────────────────────────────────────────
# Signed-cookie sessions: NO database writes per request
SESSION_ENGINE = 'django.contrib.sessions.backends.signed_cookies'
SESSION_COOKIE_AGE = 86400
SESSION_COOKIE_HTTPONLY = True

# ── Cache ────────────────────────────────────────────────────────────────────
# In-memory cache for Spotify tokens, AppConfig, etc.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'dj-wishlist-cache',
        'TIMEOUT': 300,  # 5 min default
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Spotify ──────────────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID', '')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI', 'http://localhost:8000/spotify/callback/')

# ── Google ───────────────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:8000/google/callback/')

# ── Logging ──────────────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {'class': 'logging.StreamHandler'},
    },
    'root': {'handlers': ['console'], 'level': 'INFO'},
    'loggers': {
        'wishlist': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}
