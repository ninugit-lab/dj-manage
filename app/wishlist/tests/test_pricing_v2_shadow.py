"""Mode selection must never let a shadow calculation replace a V1 price."""
from datetime import date, time
from decimal import Decimal
import logging
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

from wishlist.models import (AppConfig, Event, EventPriceCalculation, PriceCalculationRevision,
                             PriceItem, PricingPackage, PricingProfile, PricingWorkflow)
from wishlist.price_engine import PriceEngine
from wishlist.pricing_v2.input import PricingValidationError
from wishlist.pricing_v2.services import calculate_with_mode


@pytest.fixture
def catalog(db):
    package = PricingPackage.objects.create(name="Party", base_price=Decimal("100"))
    extra = PriceItem.objects.create(name="Light", price=Decimal("30"))
    PricingWorkflow.objects.create(name="Default", is_default=True,
        workflow_json=[{"type": "package", "config": {"package_id": package.pk}},
                       {"type": "items"}])
    return package, extra


def request(catalog):
    return dict(event_date="2026-12-02", start_time="18:00", end_time="21:00",
                package_id=str(catalog[0].pk), selected_item_ids=[str(catalog[1].pk)])


def event_with_calculation(catalog):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X",
                                 time_start=time(18), time_end=time(21), total_price=Decimal("777"))
    calc = EventPriceCalculation.objects.create(event=event, package=catalog[0])
    calc.selected_items.add(catalog[1])
    return event


def enable_v2():
    PricingProfile.objects.create(name="Live", status="active", valid_from=timezone.now(),
        config_json=dict(included_hours="3", extra_hour_rate="0", setup_minutes=0,
                         teardown_minutes=0, setup_teardown_hour_rate="0", included_km="0",
                         km_rate="0", saturday_percent="0", sunday_percent="0",
                         holiday_percent="0", holiday_region="BY", seasons=[],
                         lead_time_tiers=[], guest_tiers=[{"lower_bound": 0, "upper_bound": None, "value": "0"}],
                         minimum_price="0", rounding_mode="nearest", rounding_step="0.01"))


def test_default_legacy_uses_actual_v1_without_v2_configuration(catalog):
    event = event_with_calculation(catalog)
    expected = PriceEngine.calculate_workflow(event, workflow_id=PricingWorkflow.objects.get(is_default=True).pk,
                                               package_id=catalog[0].pk, selected_item_ids=[catalog[1].pk])
    decision = calculate_with_mode(event=event)
    assert AppConfig.load().pricing_engine_mode == "legacy"
    assert decision.visible_result.gross_total == expected["grand_total"] == Decimal("130")
    assert decision.v2_result is None
    assert decision.comparison_revision is None
    assert decision.visible_result.to_dict()["gross_total"] == "130.00"
    assert decision.visible_result.to_dict()["lines"][0]["amount"] == "100.00"
    assert not PriceCalculationRevision.objects.exists()


def test_shadow_keeps_v1_visible_and_comparison_is_opt_in_and_idempotent(catalog):
    enable_v2()
    event = event_with_calculation(catalog)
    AppConfig.objects.update(pricing_engine_mode="shadow") if AppConfig.objects.exists() else AppConfig.objects.create(pricing_engine_mode="shadow")
    preview = calculate_with_mode(request(catalog), public=True, event=event)
    assert preview.visible_result.gross_total == Decimal("130")
    assert preview.v2_result.gross_total == Decimal("130.00")
    assert preview.comparison_revision is None
    assert not PriceCalculationRevision.objects.exists()
    first = calculate_with_mode(request(catalog), public=True, event=event, persist_comparison=True)
    second = calculate_with_mode(request(catalog), public=True, event=event, persist_comparison=True)
    assert first.comparison_revision.pk == second.comparison_revision.pk
    assert (first.comparison_revision.kind, first.comparison_revision.legacy_total,
            first.comparison_revision.gross_total, first.comparison_revision.difference) == (
                "migration_compare", Decimal("130"), Decimal("130"), Decimal("0"))
    assert PriceCalculationRevision.objects.filter(event=event).count() == 1
    event.refresh_from_db()
    assert event.total_price == Decimal("777")


def test_shadow_unavailable_v2_is_diagnostic_only_even_on_submit(catalog):
    event = event_with_calculation(catalog)
    AppConfig.load()
    AppConfig.objects.update(pricing_engine_mode="shadow")
    decision = calculate_with_mode(event=event, persist_comparison=True)
    assert decision.visible_result.gross_total == Decimal("130")
    assert decision.v2_result is None
    assert decision.comparison_revision.legacy_total == Decimal("130")
    assert decision.comparison_revision.difference is None
    assert decision.comparison_revision.result_json["diagnostic"]["code"] == "reference_error"
    assert "diagnostic" not in decision.visible_result.to_dict()
    assert calculate_with_mode(event=event, persist_comparison=True).comparison_revision.pk == decision.comparison_revision.pk
    event.refresh_from_db()
    assert event.total_price == Decimal("777")


def test_unsaved_preview_never_writes_a_comparison(catalog):
    enable_v2()
    AppConfig.load()
    AppConfig.objects.update(pricing_engine_mode="shadow")
    decision = calculate_with_mode(request(catalog), public=True, persist_comparison=True)
    assert decision.comparison_revision is None
    assert not Event.objects.exists()
    assert not PriceCalculationRevision.objects.exists()


def test_v2_mode_is_authoritative_on_payload_and_event_paths(catalog):
    enable_v2()
    event = event_with_calculation(catalog)
    AppConfig.load()
    AppConfig.objects.update(pricing_engine_mode="v2")
    for kwargs in ({"payload": request(catalog), "public": True}, {"event": event}):
        decision = calculate_with_mode(**kwargs)
        assert decision.visible_result is decision.v2_result
        assert decision.visible_result.gross_total == Decimal("130.00")
    with pytest.raises(PricingValidationError):
        calculate_with_mode(payload=request(catalog) | {"package_id": "999999"}, public=True)
    assert not PriceCalculationRevision.objects.exists()


def test_legacy_payload_preserves_v1_discount_custom_and_rule_paths(catalog):
    PricingWorkflow.objects.filter(is_default=True).update(workflow_json=[])
    payload = request(catalog) | {"discount_percent": "10", "custom_items": [{"name": "Extra", "price": "10"}]}
    decision = calculate_with_mode(payload, public=False)
    assert decision.visible_result.gross_total == Decimal("126")
    assert Decimal(decision.visible_result.to_dict()["gross_total"]) == Decimal("126")


@pytest.mark.parametrize("kwargs", [{}, {"payload": None}, {"payload": None, "event": None},
                                  {"payload": {}}, {"event": Event()}])
def test_no_usable_input_is_a_typed_error(catalog, kwargs):
    with pytest.raises(PricingValidationError) as error:
        calculate_with_mode(**kwargs)
    assert error.value.code == "missing_input"


@pytest.mark.parametrize("mode", ["legacy", "shadow", "v2"])
def test_missing_input_is_typed_in_every_mode(catalog, mode):
    config = AppConfig.load()
    config.pricing_engine_mode = mode
    config.save(update_fields=["pricing_engine_mode"])
    with pytest.raises(PricingValidationError) as error:
        calculate_with_mode()
    assert error.value.code == "missing_input"


def test_invalid_mode_is_rejected_before_any_engine_or_bad_input(catalog):
    AppConfig.load()
    AppConfig.objects.update(pricing_engine_mode="bogus")
    with patch("wishlist.pricing_v2.services.PriceEngine.calculate", side_effect=AssertionError("V1 called")), \
         patch("wishlist.pricing_v2.services.PriceEngine.calculate_workflow", side_effect=AssertionError("V1 called")), \
         patch("wishlist.pricing_v2.services.calculate_payload", side_effect=AssertionError("V2 called")):
        with pytest.raises(PricingValidationError) as error:
            calculate_with_mode(payload=request(catalog))
    assert (error.value.code, error.value.field) == ("invalid_input", "pricing_engine_mode")


def test_non_mapping_payload_is_typed(catalog):
    with pytest.raises(PricingValidationError) as error:
        calculate_with_mode(payload="not a mapping")
    assert error.value.code == "invalid_input"


@pytest.mark.parametrize("profile_enabled", [False, True], ids=["diagnostic", "successful"])
def test_shadow_comparison_retry_ignores_actor_but_preserves_first_creator(catalog, profile_enabled):
    if profile_enabled:
        enable_v2()
    event = event_with_calculation(catalog)
    AppConfig.objects.create(pricing_engine_mode="shadow")
    first_actor = get_user_model().objects.create_user(username="first")
    later_actor = get_user_model().objects.create_user(username="later")
    first = calculate_with_mode(event=event, actor=first_actor, persist_comparison=True)
    retried = calculate_with_mode(event=event, actor=later_actor, persist_comparison=True)
    assert retried.visible_result.gross_total == Decimal("130")
    assert retried.comparison_revision.pk == first.comparison_revision.pk
    assert retried.comparison_revision.created_by_id == first_actor.pk
    assert PriceCalculationRevision.objects.filter(event=event).count() == 1


@pytest.mark.parametrize("profile_enabled", [False, True], ids=["diagnostic", "successful"])
def test_shadow_comparison_write_failure_is_internal_and_never_interrupts_v1(catalog, profile_enabled, caplog):
    if profile_enabled:
        enable_v2()
    event = event_with_calculation(catalog)
    AppConfig.objects.create(pricing_engine_mode="shadow")
    target = "_save_comparison" if profile_enabled else "_save_failed_comparison"
    service_logger = logging.getLogger("wishlist.pricing_v2.services")
    service_logger.addHandler(caplog.handler)
    try:
        with patch(f"wishlist.pricing_v2.services.{target}", side_effect=RuntimeError("database unavailable")):
            decision = calculate_with_mode(event=event, persist_comparison=True)
    finally:
        service_logger.removeHandler(caplog.handler)
    assert decision.visible_result.gross_total == Decimal("130")
    assert decision.comparison_revision is None
    assert (decision.v2_result is not None) == profile_enabled
    assert "database unavailable" in caplog.text
    assert any(record.name == "wishlist.pricing_v2.services"
               and "could not be persisted" in record.getMessage()
               and record.exc_info
               and "database unavailable" in str(record.exc_info[1])
               for record in caplog.records)
    assert "database unavailable" not in str(decision.visible_result.to_dict())
    event.refresh_from_db()
    assert event.total_price == Decimal("777")
    assert not PriceCalculationRevision.objects.exists()


def test_mode_migration_applies_after_default_workflow_constraint(db):
    loader = MigrationLoader(None)
    plan = loader.graph.forwards_plan(("wishlist", "0015_pricing_engine_mode"))
    assert plan.index(("wishlist", "0014_pricing_workflow_default_constraint")) < plan.index(
        ("wishlist", "0015_pricing_engine_mode"))
    field = AppConfig._meta.get_field("pricing_engine_mode")
    assert field.default == "legacy"
    assert [value for value, _ in field.choices] == ["legacy", "shadow", "v2"]
