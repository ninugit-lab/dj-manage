from datetime import date, time, timedelta
from decimal import Decimal
import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from wishlist.models import (
    AppConfig, Event, EventPriceCalculation, EventOffer, PriceCalculationRevision,
    PriceItem, PricingPackage, PricingProfile, PricingWorkflow,
    PricingPackageOverride,
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


def test_compare_apply_requires_exact_local_database_allowlist(comparison_data, monkeypatch):
    from django.db import connection
    from django.test import override_settings
    event, _, _ = comparison_data
    with pytest.raises(CommandError):
        call_command('pricing_v2_compare', '--apply', '--event-id', str(event.pk))
    assert not event.price_revisions.exists()
    with override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver']):
        monkeypatch.setenv('PRICING_V2_APPLY_DB_NAME', 'production.sqlite3')
        with pytest.raises(CommandError):
            call_command('pricing_v2_compare', '--apply', '--confirm-local-apply')
        monkeypatch.setenv('PRICING_V2_APPLY_DB_NAME', str(connection.settings_dict['NAME']))
        call_command('pricing_v2_compare', '--apply', '--confirm-local-apply', '--event-id', str(event.pk))
    assert event.price_revisions.filter(kind='migration_compare').count() == 1


def test_comparison_uses_saved_inquiry_booking_date_for_lead_time(comparison_data):
    from wishlist.pricing_v2.migration import compare_event
    from datetime import timedelta
    event, profile, workflow = comparison_data
    event.date = timezone.localdate() + timedelta(days=4)
    event.save(update_fields=['date'])
    event.created_at = timezone.now() - timedelta(days=1)
    Event.objects.filter(pk=event.pk).update(created_at=event.created_at)
    # Distinguish the historical year-2000 clock from a real booking date:
    # the former falls in the unrestricted tier and must NOT be chosen.
    profile.config_json['lead_time_tiers'] = [
        {'lower_bound': 0, 'upper_bound': 7, 'value': '10'},
        {'lower_bound': 8, 'upper_bound': None, 'value': '0'},
    ]
    workflow.workflow_json.insert(2, {'type': 'calendar_factors'})
    workflow.save(update_fields=['workflow_json'])
    profile.save(update_fields=['config_json'])
    result = compare_event(event, profile, workflow)
    assert result.status == 'ok', result.diagnostics
    assert result.v2_gross > Decimal('130.00'), result


def test_comparison_booking_time_change_creates_distinct_audit_key(comparison_data):
    from wishlist.pricing_v2.migration import compare_event
    event, profile, workflow = comparison_data
    first = compare_event(event, profile, workflow, persist=True)
    Event.objects.filter(pk=event.pk).update(created_at=event.created_at - timedelta(days=1))
    second = compare_event(event, profile, workflow, persist=True)
    assert first.comparison_key != second.comparison_key
    assert event.price_revisions.filter(kind='migration_compare').count() == 2


def test_apply_is_idempotent_and_does_not_change_offer(comparison_data, monkeypatch):
    event, _, _ = comparison_data
    offer = EventOffer.objects.create(event=event, guest_count=10, rate_1=Decimal("2"))
    from django.db import connection
    from django.test import override_settings
    monkeypatch.setenv('PRICING_V2_APPLY_DB_NAME', str(connection.settings_dict['NAME']))
    with override_settings(DEBUG=True, ALLOWED_HOSTS=['testserver']):
        call_command("pricing_v2_compare", "--event-id", str(event.pk), "--apply", "--confirm-local-apply")
        call_command("pricing_v2_compare", "--event-id", str(event.pk), "--apply", "--confirm-local-apply")
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
    clock = timezone.now()
    monkeypatch.setattr(migration.timezone, 'now', lambda: clock + timedelta(days=2))
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


def test_failed_comparison_persists_typed_frozen_snapshots(comparison_data, monkeypatch):
    event, profile, workflow = comparison_data
    from wishlist.pricing_v2 import migration
    from wishlist.pricing_v2.input import PricingValidationError
    monkeypatch.setattr(migration, "calculate_payload",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            PricingValidationError("invalid_input", "bad catalog", "catalog")))
    result = migration.compare_event(event, profile, workflow, persist=True)
    revision = result.revision
    assert result.status == "error"
    assert revision.profile_id_snapshot == profile.pk
    assert revision.profile_version == profile.version
    assert revision.workflow_id_snapshot == workflow.pk
    assert revision.workflow_version == workflow.version
    assert revision.package_id_snapshot == event.price_calculation.package_id
    assert revision.package_version is not None
    assert revision.result_json == {"diagnostic": {"code": "invalid_input", "field": "catalog"}}
    assert revision.input_json["profile_config"] == profile.config_json
    assert revision.input_json["workflow"] == workflow.workflow_json
    assert revision.input_json["event"]["event_id"] == event.pk
    assert "message" not in revision.result_json["diagnostic"]


def test_failed_comparison_content_change_creates_distinct_diagnostic(comparison_data, monkeypatch):
    event, profile, workflow = comparison_data
    from wishlist.pricing_v2 import migration
    from wishlist.pricing_v2.input import PricingValidationError
    monkeypatch.setattr(migration, "calculate_payload",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            PricingValidationError("invalid_input", "bad catalog", "catalog")))
    first = migration.compare_event(event, profile, workflow, persist=True)
    profile.config_json["minimum_price"] = "999"
    profile.save(update_fields=["config_json"])
    second = migration.compare_event(event, profile, workflow, persist=True)
    assert first.diagnostics == second.diagnostics
    assert first.comparison_key != second.comparison_key
    assert PriceCalculationRevision.objects.filter(
        event=event, kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE).count() == 2


def _force_v2_failure(monkeypatch):
    from wishlist.pricing_v2 import migration
    from wishlist.pricing_v2.input import PricingValidationError
    monkeypatch.setattr(migration, "calculate_payload",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            PricingValidationError("invalid_input", "bad catalog", "catalog")))
    return migration


def test_failed_key_changes_when_required_active_item_changes(comparison_data, monkeypatch):
    event, profile, workflow = comparison_data
    migration = _force_v2_failure(monkeypatch)
    required = PriceItem.objects.create(name="Required", price=Decimal("5"), is_required=True)
    first = migration.compare_event(event, profile, workflow, persist=True)
    required.price = Decimal("6")
    required.save(update_fields=["price"])
    second = migration.compare_event(event, profile, workflow, persist=True)
    assert first.comparison_key != second.comparison_key
    assert first.revision.pk != second.revision.pk


def test_failed_key_changes_when_package_override_changes(comparison_data, monkeypatch):
    event, profile, workflow = comparison_data
    migration = _force_v2_failure(monkeypatch)
    override = PricingPackageOverride.objects.create(
        profile=profile, package=event.price_calculation.package, overrides_json={"minimum_price": "1"},
    )
    first = migration.compare_event(event, profile, workflow, persist=True)
    override.overrides_json = {"minimum_price": "2"}
    override.save(update_fields=["overrides_json"])
    second = migration.compare_event(event, profile, workflow, persist=True)
    assert first.comparison_key != second.comparison_key
    assert first.revision.pk != second.revision.pk


def test_failed_key_changes_when_package_membership_changes(comparison_data, monkeypatch):
    event, profile, workflow = comparison_data
    migration = _force_v2_failure(monkeypatch)
    extra = PriceItem.objects.create(name="Included later", price=Decimal("30"))
    package = event.price_calculation.package
    first = migration.compare_event(event, profile, workflow, persist=True)
    package.included_items.add(extra)
    second = migration.compare_event(event, profile, workflow, persist=True)
    assert first.comparison_key != second.comparison_key
    assert first.revision.pk != second.revision.pk
