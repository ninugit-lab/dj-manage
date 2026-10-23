from datetime import date, time
from decimal import Decimal
import json

import pytest
from django.core.management import call_command
from django.utils import timezone

from wishlist.models import Event, EventPriceCalculation, EventStatus, PriceCalculationRevision
from wishlist.pricing_v2.migration import eligible_for_reprice


def test_eligibility_matrix_uses_supplied_local_date():
    today = date(2026, 10, 23)
    base = Event(date=today, status=EventStatus.CONFIRMED)
    assert eligible_for_reprice(Event(date=date(2020, 1, 1), status=EventStatus.INQUIRY), today)
    assert eligible_for_reprice(base, today)
    assert eligible_for_reprice(Event(date=date(2026, 10, 24), status=EventStatus.CONFIRMED), today)
    assert not eligible_for_reprice(Event(date=date(2026, 10, 22), status=EventStatus.CONFIRMED), today)
    assert not eligible_for_reprice(Event(date=today, status=EventStatus.PAST), today)
    assert not eligible_for_reprice(Event(date=today, status=EventStatus.CANCELLED), today)


@pytest.fixture
def reprice_event_data(db):
    from wishlist.models import PriceItem, PricingPackage, PricingProfile, PricingWorkflow
    package = PricingPackage.objects.create(name="Party", base_price=Decimal("100"))
    item = PriceItem.objects.create(name="Light", price=Decimal("30"))
    PricingWorkflow.objects.create(name="Default", is_default=True,
        workflow_json=[{"type": "package", "config": {"package_id": package.pk}}, {"type": "items"}])
    PricingProfile.objects.create(name="Live", version=1, status="active", valid_from=timezone.now(),
        config_json={"included_hours": "3", "extra_hour_rate": "0", "setup_minutes": 0,
        "teardown_minutes": 0, "setup_teardown_hour_rate": "0", "included_km": "0", "km_rate": "0",
        "saturday_percent": "0", "sunday_percent": "0", "holiday_percent": "0", "holiday_region": "BY",
        "seasons": [], "lead_time_tiers": [], "guest_tiers": [{"lower_bound": 0, "upper_bound": None, "value": "0"}],
        "minimum_price": "0", "rounding_mode": "nearest", "rounding_step": "0.01"})
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), status=EventStatus.INQUIRY,
        location="X", time_start=time(18), time_end=time(21), total_price=Decimal("777"))
    calc = EventPriceCalculation.objects.create(event=event, package=package)
    calc.selected_items.add(item)
    return event


def test_dry_run_is_machine_readable_and_does_not_write(reprice_event_data, capsys):
    call_command("pricing_v2_reprice")
    report = json.loads(capsys.readouterr().out)
    assert report["rows"][0]["new_gross"] == "130.00"
    assert report["rows"][0]["difference"] == "-647.00"
    assert not PriceCalculationRevision.objects.exists()
    reprice_event_data.refresh_from_db()
    assert reprice_event_data.total_price == Decimal("777.00")


def test_apply_is_idempotent_and_keeps_event_total(reprice_event_data):
    call_command("pricing_v2_reprice", "--apply")
    call_command("pricing_v2_reprice", "--apply")
    assert PriceCalculationRevision.objects.filter(event=reprice_event_data, kind="draft").count() == 1
    reprice_event_data.refresh_from_db()
    assert reprice_event_data.total_price == Decimal("777.00")
