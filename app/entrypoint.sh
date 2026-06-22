#!/bin/sh
set -e

echo "→ Running migrations..."
python manage.py migrate --noinput

echo "→ Collecting static files..."
python manage.py collectstatic --noinput

echo "→ Cleaning expired sessions..."
python manage.py clearsessions 2>/dev/null || true

echo "→ Starting Gunicorn (optimized for 200+ concurrent users)..."
exec gunicorn dj_wishlist.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers ${GUNICORN_WORKERS:-4} \
    --threads ${GUNICORN_THREADS:-4} \
    --worker-class gthread \
    --worker-tmp-dir /dev/shm \
    --timeout 30 \
    --keep-alive 5 \
    --max-requests 1000 \
    --max-requests-jitter 100 \
    --access-logfile - \
    --error-logfile -
