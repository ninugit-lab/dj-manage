from datetime import date, time
from decimal import Decimal
import json

import pytest
from django.core.management import call_command
from django.utils import timezone

from wishlist.models import (
    AppConfig, Event, EventPriceCalculation, EventOffer, PriceCalculationRevision,
    PriceItem, PricingPackage, PricingProfile, PricingWorkflow,
)


@pytest.fixture
def comparison_data(db):
    package = PricingPackage.objects.create(name="Party", base_price=Decimal("100"))
    item = PriceItem.objects.create(name="Light", price=Decimal("30"))
    workflow = PricingWorkflow.objects.create(
        name="Default", is_default=True,
        workflow_json=[{"type": "package", "config": {"package_id": package.pk}}, {"type": "items"}],
    )
    profile = PricingProfile.objects.create(
        name="Live", version=1, status=PricingProfile.Status.ACTIVE,
        valid_from=timezone.now(), config_json={
            "included_hours": "3", "extra_hour_rate": "0", "setup_minutes": 0,
            "teardown_minutes": 0, "setup_teardown_hour_rate": "0", "included_km": "0",
            "km_rate": "0", "saturday_percent": "0", "sunday_percent": "0",
            "holiday_percent": "0", "holiday_region": "BY", "seasons": [],
            "lead_time_tiers": [], "guest_tiers": [{"lower_bound": 0, "upper_bound": None, "value": "0"}],
            "minimum_price": "0", "rounding_mode": "nearest", "rounding_step": "0.01",
        },
    )
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X",
                                 time_start=time(18), time_end=time(21), total_price=Decimal("777"))
    calc = EventPriceCalculation.objects.create(event=event, package=package)
    calc.selected_items.add(item)
    return event, profile, workflow


def test_command_dry_run_reports_decimal_values_without_writes(comparison_data, capsys):
    event, _, _ = comparison_data
    call_command("pricing_v2_compare", "--event-id", str(event.pk))
    report = json.loads(capsys.readouterr().out)
    assert report["rows"][0]["v1_gross"] == "130.00"
    assert report["rows"][0]["v2_gross"] == "130.00"
    assert report["rows"][0]["difference"] == "0.00"
    assert not PriceCalculationRevision.objects.exists()
    event.refresh_from_db()
    assert event.total_price == Decimal("777.00")


def test_apply_is_idempotent_and_does_not_change_offer(comparison_data):
    event, _, _ = comparison_data
    offer = EventOffer.objects.create(event=event, guest_count=10, rate_1=Decimal("2"))
    call_command("pricing_v2_compare", "--event-id", str(event.pk), "--apply")
    call_command("pricing_v2_compare", "--event-id", str(event.pk), "--apply")
    assert PriceCalculationRevision.objects.filter(
        event=event, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE,
    ).count() == 1
    offer.refresh_from_db()
    event.refresh_from_db()
    assert offer.rate_1 == Decimal("2.00")
    assert event.total_price == Decimal("777.00")


def test_malformed_event_does_not_abort_batch(comparison_data, capsys):
    event, _, _ = comparison_data
    Event.objects.create(name="Broken", date=date(2026, 12, 3), location="X")
    call_command("pricing_v2_compare")
    report = json.loads(capsys.readouterr().out)
    assert [row["event_id"] for row in report["rows"]] == sorted(row["event_id"] for row in report["rows"])
    assert any(row["event_id"] == event.pk and row["status"] == "ok" for row in report["rows"])
    assert any(row["status"] == "error" for row in report["rows"])
