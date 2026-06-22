import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG = os.environ.get('DEBUG', 'True') == 'True'

LOGIN_REDIRECT_URL = '/dj-admin/'
LOGIN_URL = '/admin/login/'

_secure_cookies = os.environ.get('SECURE_COOKIES', 'False') == 'True'
CSRF_COOKIE_SECURE = _secure_cookies
SESSION_COOKIE_SECURE = _secure_cookies
CSRF_TRUSTED_ORIGINS = [
    "https://*.ngrok.io",
    "https://*.ngrok-free.dev",
    "http://100.74.102.46:8500",
    "http://localhost:8500",
]

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
    'wishlist',
]

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
