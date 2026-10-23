from datetime import date, time
from decimal import Decimal
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
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


def test_apply_is_idempotent_and_keeps_event_total(reprice_event_data, capsys):
    with override_settings(DEBUG=True, ALLOWED_HOSTS=["testserver"]):
        call_command("pricing_v2_reprice", "--apply", "--confirm-local-apply")
        call_command("pricing_v2_reprice", "--apply", "--confirm-local-apply")
    assert PriceCalculationRevision.objects.filter(event=reprice_event_data, kind="draft").count() == 1
    output = capsys.readouterr().out
    assert json.loads(output[output.rfind("{\n  \"rows\""):])["rows"][0]["status"] == "already_applied"
    reprice_event_data.refresh_from_db()
    assert reprice_event_data.total_price == Decimal("777.00")


def test_apply_requires_explicit_local_confirmation(reprice_event_data):
    with pytest.raises(CommandError, match="confirm-local-apply"):
        call_command("pricing_v2_reprice", "--apply")


def test_apply_refuses_unknown_or_production_database(reprice_event_data, monkeypatch):
    from django.db import connection
    monkeypatch.setitem(connection.settings_dict, "NAME", "production.sqlite3")
    with override_settings(DEBUG=True, ALLOWED_HOSTS=["testserver"]):
        with pytest.raises(CommandError, match="temporary test database"):
            call_command("pricing_v2_reprice", "--apply", "--confirm-local-apply")


def test_malformed_event_is_reported_and_later_event_continues(reprice_event_data, capsys):
    broken = Event.objects.create(name="Broken", date=date(2026, 12, 3), status=EventStatus.INQUIRY,
                                  location="X")
    later = Event.objects.create(name="Later", date=date(2026, 12, 4), status=EventStatus.PAST,
                                 location="X")
    call_command("pricing_v2_reprice")
    rows = json.loads(capsys.readouterr().out)["rows"]
    assert rows[-1]["event_id"] == later.pk
    assert rows[-1]["status"] == "skipped"
    assert next(row for row in rows if row["event_id"] == broken.pk)["status"] == "error"
    assert all("Broken" not in json.dumps(row) for row in rows)


@pytest.mark.parametrize("offer_status", ["released", "sent", "send_failed"])
def test_apply_preserves_released_offer_and_audits_old_new_difference(reprice_event_data, offer_status):
    from wishlist.models import EventOffer
    offer = EventOffer.objects.create(event=reprice_event_data, rate_1=Decimal("2"))
    released = PriceCalculationRevision.objects.create(
        event=reprice_event_data, kind="offer", status=offer_status, revision_number=1,
        engine_version="legacy", gross_total=Decimal("777"))
    with override_settings(DEBUG=True, ALLOWED_HOSTS=["testserver"]):
        call_command("pricing_v2_reprice", "--apply", "--confirm-local-apply")
    revision = PriceCalculationRevision.objects.get(event=reprice_event_data, kind="draft")
    assert revision.legacy_total == Decimal("777.00")
    assert revision.gross_total == Decimal("130.00")
    assert revision.difference == Decimal("-647.00")
    assert revision.status == "draft"
    assert PriceCalculationRevision.objects.get(pk=released.pk).gross_total == Decimal("777.00")
    offer.refresh_from_db()
    reprice_event_data.refresh_from_db()
    assert offer.rate_1 == Decimal("2.00")
    assert reprice_event_data.total_price == Decimal("777.00")


def test_output_path_and_changed_config_create_new_draft(reprice_event_data, tmp_path):
    from wishlist.models import PricingProfile
    output = tmp_path / "reprice.json"
    with override_settings(DEBUG=True, ALLOWED_HOSTS=["testserver"]):
        call_command("pricing_v2_reprice", "--apply", "--confirm-local-apply", "--output", str(output))
    profile = PricingProfile.objects.get()
    profile.config_json["minimum_price"] = "131"
    profile.save(update_fields=["config_json"])
    with override_settings(DEBUG=True, ALLOWED_HOSTS=["testserver"]):
        call_command("pricing_v2_reprice", "--apply", "--confirm-local-apply")
    assert PriceCalculationRevision.objects.filter(event=reprice_event_data, kind="draft").count() == 2
    report = json.loads(output.read_text())
    assert report["rows"][0]["old_gross"] == "777.00"
    assert report["rows"][0]["new_gross"] == "130.00"


def test_run_freezes_requested_at(monkeypatch, reprice_event_data):
    from datetime import datetime, timezone as dt_timezone
    from wishlist.pricing_v2 import migration
    frozen = datetime(2026, 10, 23, tzinfo=dt_timezone.utc)
    seen = []
    original = migration.calculate_event
    monkeypatch.setattr(migration.timezone, "now", lambda: frozen)
    monkeypatch.setattr(migration, "calculate_event", lambda event, actor=None: (
        seen.append(migration.timezone.now()) or original(event, actor=actor)))
    migration.reprice_event(reprice_event_data, apply=False, requested_at=frozen)
    assert seen == [frozen]


def test_clock_shift_does_not_duplicate_same_frozen_economics(reprice_event_data):
    from datetime import datetime, timezone as dt_timezone
    from wishlist.pricing_v2.migration import reprice_event
    first = reprice_event(reprice_event_data, apply=True,
                          requested_at=datetime(2026, 10, 23, tzinfo=dt_timezone.utc))
    second = reprice_event(reprice_event_data, apply=True,
                           requested_at=datetime(2026, 10, 24, tzinfo=dt_timezone.utc))
    assert first["revision_id"] == second["revision_id"]
    assert second["status"] == "already_applied"
