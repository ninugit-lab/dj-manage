"""APScheduler-Setup für wiederkehrende Hintergrundjobs.

Startet genau einen Scheduler pro Host, auch wenn mehrere Gunicorn-Worker
laufen: der erste Prozess, der die Lockdatei exklusiv sperrt, bekommt den Job.
Der Lock bleibt für die Lebensdauer des Prozesses gehalten.
"""
import atexit
import fcntl
import logging
import os

logger = logging.getLogger(__name__)

LOCK_PATH = os.environ.get('SCHEDULER_LOCK_PATH', '/tmp/dj-redoo-scheduler.lock')

_lock_file = None
_scheduler = None


def _acquire_lock():
    """True, wenn dieser Prozess der Scheduler-Prozess sein darf."""
    global _lock_file
    try:
        _lock_file = open(LOCK_PATH, 'w')
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        if _lock_file:
            _lock_file.close()
            _lock_file = None
        return False
    _lock_file.write(f"{os.getpid()}\n")
    _lock_file.flush()
    return True


def start():
    """Startet den Scheduler, sofern dieser Prozess den Lock bekommt."""
    global _scheduler
    if _scheduler is not None:
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("APScheduler nicht installiert — Hintergrundjobs deaktiviert.")
        return

    if not _acquire_lock():
        return

    from .review_requests import send_review_requests

    from django.conf import settings
    _scheduler = BackgroundScheduler(timezone=settings.TIME_ZONE)
    _scheduler.add_job(
        send_review_requests,
        CronTrigger(hour=10, minute=0),
        id='send_review_requests',
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    atexit.register(_shutdown)
    logger.info("Scheduler gestartet (PID %s).", os.getpid())


def _shutdown():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    if _lock_file is not None:
        _lock_file.close()
