from datetime import date, time
from decimal import Decimal
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
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


def test_changed_profile_content_creates_immutable_revision(comparison_data):
    event, profile, workflow = comparison_data
    from wishlist.pricing_v2.migration import compare_event
    first = compare_event(event, profile, workflow, persist=True)
    profile.config_json["minimum_price"] = "130"
    profile.save(update_fields=["config_json"])
    second = compare_event(event, profile, workflow, persist=True)
    assert second.comparison_key != first.comparison_key
    assert PriceCalculationRevision.objects.filter(event=event, kind="migration_compare").count() == 2
    assert PriceCalculationRevision.objects.get(pk=first.revision.pk).input_json["comparison_key"] == first.comparison_key


def test_changed_workflow_version_creates_new_revision_even_same_total(comparison_data):
    event, profile, workflow = comparison_data
    from wishlist.pricing_v2.migration import compare_event
    first = compare_event(event, profile, workflow, persist=True)
    workflow.version = 2
    workflow.save(update_fields=["version"])
    second = compare_event(event, profile, workflow, persist=True)
    assert second.comparison_key != first.comparison_key
    assert PriceCalculationRevision.objects.filter(event=event, kind="migration_compare").count() == 2


def test_catalog_content_change_creates_new_revision_even_same_total(comparison_data):
    event, profile, workflow = comparison_data
    item = event.price_calculation.selected_items.first()
    from wishlist.pricing_v2.migration import compare_event
    first = compare_event(event, profile, workflow, persist=True)
    item.description = "same price, new frozen catalog content"
    item.save(update_fields=["description"])
    second = compare_event(event, profile, workflow, persist=True)
    assert second.v2_gross == first.v2_gross
    assert second.comparison_key != first.comparison_key
    assert PriceCalculationRevision.objects.filter(event=event, kind="migration_compare").count() == 2


def test_same_frozen_inputs_are_idempotent_despite_result_request_timestamp(comparison_data, monkeypatch):
    event, profile, workflow = comparison_data
    from wishlist.pricing_v2 import migration
    first = migration.compare_event(event, profile, workflow)
    monkeypatch.setattr(migration, "FROZEN_REQUESTED_AT", timezone.now())
    second = migration.compare_event(event, profile, workflow)
    assert second.comparison_key == first.comparison_key


def test_report_contains_snapshots_and_no_exception_text(comparison_data):
    event, profile, workflow = comparison_data
    from wishlist.pricing_v2.migration import compare_event
    result = compare_event(event, profile, workflow, persist=True)
    revision = result.revision
    assert revision.profile_id_snapshot == profile.pk
    assert revision.profile_version == profile.version
    assert revision.workflow_id_snapshot == workflow.pk
    assert revision.workflow_version == workflow.version
    assert revision.difference == Decimal("0.00")
    assert "requested_at" not in revision.input_json


def test_status_validation_output_file_and_invalid_event_id(comparison_data, tmp_path):
    event, _, _ = comparison_data
    output = tmp_path / "comparison.json"
    call_command("pricing_v2_compare", "--status", "inquiry", "--output", str(output))
    assert json.loads(output.read_text())["rows"][0]["event_id"] == event.pk
    with pytest.raises(CommandError):
        call_command("pricing_v2_compare", "--event-id", "not-an-int")


def test_supplied_references_must_be_current_active_defaults(comparison_data):
    event, profile, workflow = comparison_data
    from wishlist.pricing_v2.migration import compare_event
    profile.status = PricingProfile.Status.RETIRED
    profile.save(update_fields=["status"])
    result = compare_event(event, profile, workflow)
    assert result.status == "error"
    assert result.diagnostics["code"] == "stale_reference"
