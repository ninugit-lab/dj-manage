from datetime import datetime, date, time, timedelta, timezone as dt_timezone

import pytest
from django.utils import timezone

from wishlist.models import Event, EventStatus


@pytest.mark.django_db
def test_confirmed_event_goes_live_one_hour_before_start():
    from wishlist.scheduler import activate_upcoming_wishlist

    starts_at = timezone.make_aware(datetime(2026, 11, 28, 19, 0))
    previous = Event.objects.create(name='Previous', date=date(2026, 11, 27), is_active=True)
    upcoming = Event.objects.create(name='Next', date=date(2026, 11, 28), time_start=time(19),
                                    status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=starts_at - timedelta(hours=1, minutes=1)) is None
    upcoming.refresh_from_db()
    assert not upcoming.is_active
    assert activate_upcoming_wishlist(now=starts_at - timedelta(hours=1)) == upcoming.pk
    upcoming.refresh_from_db()
    previous.refresh_from_db()
    assert upcoming.is_active
    assert not previous.is_active


@pytest.mark.django_db
def test_activation_skips_unconfirmed_unscheduled_and_long_finished():
    from wishlist.scheduler import activate_upcoming_wishlist

    now = timezone.make_aware(datetime(2026, 11, 28, 18, 0))
    Event.objects.create(name='Inquiry', date=now.date(), time_start=time(19), status=EventStatus.INQUIRY)
    Event.objects.create(name='No time', date=now.date(), status=EventStatus.CONFIRMED)
    Event.objects.create(name='Finished yesterday', date=date(2026, 11, 27), time_start=time(17), status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) is None
    assert not Event.objects.filter(is_active=True).exists()


@pytest.mark.django_db
def test_activation_repeated_run_does_not_flip_active_event():
    from wishlist.scheduler import activate_upcoming_wishlist

    now = timezone.make_aware(datetime(2026, 11, 28, 18, 0))
    upcoming = Event.objects.create(name='Next', date=now.date(), time_start=time(19),
                                    status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == upcoming.pk
    assert activate_upcoming_wishlist(now=now + timedelta(minutes=1)) is None
    assert Event.objects.get(pk=upcoming.pk).is_active


@pytest.mark.django_db
def test_activation_when_scheduler_was_down_does_not_take_over_later_event():
    from wishlist.scheduler import activate_upcoming_wishlist

    now = timezone.make_aware(datetime(2026, 11, 28, 18, 30))
    due = Event.objects.create(name='Due', date=now.date(), time_start=time(19),
                               status=EventStatus.CONFIRMED)
    later = Event.objects.create(name='Later', date=now.date(), time_start=time(19, 30),
                                 status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == due.pk
    assert activate_upcoming_wishlist(now=now + timedelta(minutes=1)) is None
    assert Event.objects.get(pk=due.pk).is_active
    assert not Event.objects.get(pk=later.pk).is_active


@pytest.mark.django_db
def test_old_confirmed_event_does_not_prevent_next_event_going_live():
    from wishlist.scheduler import activate_upcoming_wishlist

    now = timezone.make_aware(datetime(2026, 11, 28, 18, 0))
    old = Event.objects.create(name='Yesterday', date=date(2026, 11, 27), time_start=time(19),
                               status=EventStatus.CONFIRMED, is_active=True)
    upcoming = Event.objects.create(name='Today', date=now.date(), time_start=time(19),
                                    status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == upcoming.pk
    assert not Event.objects.get(pk=old.pk).is_active


@pytest.mark.django_db
def test_activation_deactivates_future_dated_manual_wishlist():
    from wishlist.scheduler import activate_upcoming_wishlist

    now = timezone.make_aware(datetime(2026, 11, 28, 18, 0))
    future = Event.objects.create(name='Tomorrow', date=date(2026, 11, 29), time_start=time(20),
                                  status=EventStatus.CONFIRMED, is_active=True)
    due = Event.objects.create(name='Today', date=now.date(), time_start=time(19),
                               status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == due.pk
    assert not Event.objects.get(pk=future.pk).is_active


@pytest.mark.django_db
def test_later_event_takes_over_after_earlier_event_has_started():
    from wishlist.scheduler import activate_upcoming_wishlist

    now = timezone.make_aware(datetime(2026, 11, 28, 22, 0))
    earlier = Event.objects.create(name='Earlier', date=now.date(), time_start=time(19),
                                   status=EventStatus.CONFIRMED, is_active=True)
    later = Event.objects.create(name='Later', date=now.date(), time_start=time(23),
                                 status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == later.pk
    assert not Event.objects.get(pk=earlier.pk).is_active
    assert Event.objects.get(pk=later.pk).is_active


@pytest.mark.django_db
def test_active_event_does_not_block_an_earlier_upcoming_start():
    from wishlist.scheduler import activate_upcoming_wishlist

    now = timezone.make_aware(datetime(2026, 11, 28, 18, 0))
    future = Event.objects.create(name='Future', date=now.date(), time_start=time(21),
                                  status=EventStatus.CONFIRMED, is_active=True)
    due = Event.objects.create(name='Due', date=now.date(), time_start=time(19),
                               status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == due.pk
    assert not Event.objects.get(pk=future.pk).is_active


@pytest.mark.django_db
def test_delayed_scheduler_activates_event_even_after_start():
    from wishlist.scheduler import activate_upcoming_wishlist

    now = timezone.make_aware(datetime(2026, 11, 28, 19, 3))
    event = Event.objects.create(name='Started', date=now.date(), time_start=time(19),
                                 status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == event.pk
    assert Event.objects.get(pk=event.pk).is_active


@pytest.mark.django_db
def test_activation_checks_local_date_when_utc_day_differs():
    from wishlist.scheduler import activate_upcoming_wishlist

    # 23:30 UTC = 00:30 the next day in Berlin (winter).
    now = datetime(2026, 11, 27, 23, 30, tzinfo=dt_timezone.utc)
    event = Event.objects.create(name='Midnight', date=date(2026, 11, 28), time_start=time(1, 30),
                                 status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == event.pk


@pytest.mark.django_db
def test_activation_checks_local_date_when_utc_day_is_ahead():
    from wishlist.scheduler import activate_upcoming_wishlist

    # 00:30 UTC = 02:30 on the same day in Berlin (summer).
    now = datetime(2027, 7, 12, 0, 30, tzinfo=dt_timezone.utc)
    event = Event.objects.create(name='Summer night', date=date(2027, 7, 12), time_start=time(3, 30),
                                 status=EventStatus.CONFIRMED)
    assert activate_upcoming_wishlist(now=now) == event.pk
