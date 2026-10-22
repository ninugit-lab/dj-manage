"""The service, not browser totals or live ORM rows, owns V2 prices."""
import json
from datetime import date, time
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from wishlist.models import (Event, EventPriceCalculation, PriceCalculationRevision, PriceItem,
                             PricingFormula, PricingPackage, PricingPackageOverride, PricingProfile,
                             PricingRule, PricingWorkflow)
from wishlist.pricing_v2.input import PricingValidationError
from wishlist.pricing_v2.services import calculate_event, calculate_payload, save_revision


@pytest.fixture
def stock(db):
    profile = PricingProfile.objects.create(name="Live", version=3, status="active",
        valid_from=timezone.now(), config_json=dict(
            included_hours="3", extra_hour_rate="0", setup_minutes=0, teardown_minutes=0,
            setup_teardown_hour_rate="0", included_km="0", km_rate="0", saturday_percent="0",
            sunday_percent="0", holiday_percent="0", holiday_region="BY", seasons=[],
            lead_time_tiers=[], guest_tiers=[{"lower_bound": 0, "upper_bound": None, "value": "0"}],
            minimum_price="0", rounding_mode="nearest",
            rounding_step="0.01"))
    package = PricingPackage.objects.create(name="Standard", version=2, base_price=Decimal("100"))
    included = PriceItem.objects.create(name="Included", category="tech", price=Decimal("15"))
    required = PriceItem.objects.create(name="Required", category="extra", price=Decimal("20"), is_required=True)
    extra = PriceItem.objects.create(name="Extra", category="tech", price=Decimal("30"))
    package.included_items.add(included)
    workflow = PricingWorkflow.objects.create(name="Standard", version=4, is_default=True,
        workflow_json=[{"type": "package", "config": {"package_id": package.pk}},
                       {"type": "items"}])
    return profile, package, included, required, extra, workflow


def payload(stock, **changes):
    data = dict(event_date="2026-12-02", start_time="18:00", end_time="21:00",
                package_id=str(stock[1].pk), selected_item_ids=[str(stock[4].pk)],
                amount="0", total="0")
    data.update(changes)
    return data


def test_public_required_item_and_server_prices_are_frozen(stock):
    profile, package, included, required, extra, workflow = stock
    result = calculate_payload(payload(stock), public=True)
    assert result.gross_total == Decimal("150.00")
    assert {line.code for line in result.lines} == {f"package:{package.pk}", f"item:{extra.pk}", f"item:{required.pk}"}
    assert result.inputs["pricing_region"] == "BY"
    assert result.inputs["catalog_snapshot"]["items"][str(included.pk)]["price"] == "15.00"
    assert result.inputs["catalog_snapshot"]["items"][str(required.pk)]["price"] == "20.00"
    assert result.inputs["workflow_id"] == workflow.pk
    assert result.inputs["profile_id"] == profile.pk
    json.dumps(result.to_dict())
    required.price = Decimal("90")
    required.save()
    assert result.inputs["catalog_snapshot"]["items"][str(required.pk)]["price"] == "20.00"


def test_public_rejects_internal_and_inactive_catalog_and_custom_prices(stock):
    internal = PriceItem.objects.create(name="Private", category="extra", price=Decimal("4"), is_public=False)
    for data in (payload(stock, selected_item_ids=[str(internal.pk)]),
                 payload(stock, custom_items=[{"code": "x", "label": "X", "amount": "1"}])):
        with pytest.raises(PricingValidationError):
            calculate_payload(data, public=True)
    stock[1].is_public = False
    stock[1].save()
    with pytest.raises(PricingValidationError) as error:
        calculate_payload(payload(stock), public=True)
    assert error.value.code == "reference_error"


def test_admin_event_and_payload_share_normalization_and_override(stock):
    stock[5].workflow_json.append({"type": "minimum_price"})
    stock[5].save(update_fields=["workflow_json"])
    PricingPackageOverride.objects.create(profile=stock[0], package=stock[1],
        overrides_json={"minimum_price": {"mode": "override", "value": "200"}})
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X",
                                 time_start=time(18), time_end=time(21), guest_count=0)
    calc = EventPriceCalculation.objects.create(event=event, package=stock[1])
    calc.selected_items.add(stock[4])
    from_event = calculate_event(event)
    from_payload = calculate_payload(payload(stock, guest_count=0), public=False)
    assert from_event.gross_total == from_payload.gross_total == Decimal("200.00")
    assert from_event.inputs["pricing_region"] == "BY"
    event.pricing_region = "HE"
    event.save()
    assert calculate_event(event).inputs["pricing_region"] == "HE"


def test_event_saved_v1_custom_items_are_explicit_admin_items(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X",
                                 time_start=time(18), time_end=time(21))
    EventPriceCalculation.objects.create(event=event, package=stock[1],
        custom_items_json=[{"name": "Onsite", "price": "8.50"}])
    result = calculate_event(event)
    assert result.gross_total == Decimal("128.50")
    assert any(line.code.startswith("custom:") for line in result.lines)


def test_missing_active_references_and_bad_legacy_workflow(stock):
    stock[4].is_active = False
    stock[4].save()
    with pytest.raises(PricingValidationError) as error:
        calculate_payload(payload(stock), public=True)
    assert error.value.code == "reference_error"


def test_advanced_catalog_is_frozen_and_unreferenced_formulas_are_not_executed(stock):
    PricingRule.objects.create(name="Surcharge", effect_type="flat_add", effect_value=Decimal("5"))
    PricingFormula.objects.create(name="Dormant", expression="1000")
    result = calculate_payload(payload(stock), public=False)
    assert result.gross_total == Decimal("150.00")
    snapshot = result.inputs["catalog_snapshot"]
    assert snapshot["rules"] == {}
    assert snapshot["formulas"] == {}
    assert not any(line.code.startswith("rule:") for line in result.lines)


def test_package_only_legacy_workflow_does_not_enable_other_price_blocks(stock):
    stock[5].workflow_json = [{"type": "package", "config": {"package_id": stock[1].pk}}]
    stock[5].save(update_fields=["workflow_json"])
    PricingRule.objects.create(name="Unselected", effect_type="flat_add", effect_value=Decimal("5"))
    stock[0].config_json["minimum_price"] = "500"
    stock[0].save(update_fields=["config_json"])
    result = calculate_payload(payload(stock), public=True)
    # V2 requires package+items; the adapter must not additionally enable
    # unlisted rules, minimum or other adjustment blocks.
    assert result.gross_total == Decimal("150.00")
    assert result.inputs["catalog_snapshot"]["rules"] == {}
    rules_block = next(block for block in result.inputs["workflow_blocks"] if block["type"] == "rules")
    assert rules_block["rule_ids"] == ()


def test_explicit_legacy_rules_still_apply_and_are_frozen(stock):
    rule = PricingRule.objects.create(name="Surcharge", effect_type="flat_add", effect_value=Decimal("5"))
    stock[5].workflow_json = [{"type": "package", "config": {"package_id": stock[1].pk}},
                              {"type": "rules", "config": {"rule_ids": [rule.pk]}}]
    stock[5].save()
    result = calculate_payload(payload(stock), public=True)
    assert result.gross_total == Decimal("155.00")
    assert result.inputs["catalog_snapshot"]["rules"][str(rule.pk)]["effect_value"] == "5.00"


def test_admin_custom_items_and_explicit_workflow_references(stock):
    result = calculate_payload(payload(stock, custom_items=[{"code": "special", "label": "Special",
                                                             "amount": "9.50"}]), public=False)
    assert result.gross_total == Decimal("159.50")
    stock[5].workflow_json = [{"type": "package", "config": {"package_id": stock[1].pk}},
                              {"type": "rules", "config": {"rule_ids": [999999]}}]
    stock[5].save()
    with pytest.raises(PricingValidationError) as error:
        calculate_payload(payload(stock), public=False)
    assert error.value.code == "reference_error"


def test_legacy_discount_config_percent_is_rejected_instead_of_silently_ignored(stock):
    stock[5].workflow_json = [{"type": "package", "config": {"package_id": stock[1].pk}},
                              {"type": "discount", "config": {"percent": "10"}}]
    stock[5].save()
    with pytest.raises(PricingValidationError) as error:
        calculate_payload(payload(stock), public=True)
    assert error.value.code == "invalid_input"
    stock[4].is_active = True
    stock[4].save()
    stock[5].workflow_json = [{"type": "offer", "config": {}}]
    stock[5].save()
    with pytest.raises(PricingValidationError) as error:
        calculate_payload(payload(stock), public=True)
    assert error.value.code == "invalid_input"
    stock[5].workflow_json = [{"type": "package", "config": {"package_id": 999999}}]
    stock[5].save()
    with pytest.raises(PricingValidationError) as error:
        calculate_payload(payload(stock), public=True)
    assert error.value.code == "reference_error"


def test_revision_snapshots_sequence_idempotency_and_actor(stock):
    actor = get_user_model().objects.create_user(username="pricing", password="x")
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X")
    result = calculate_payload(payload(stock), public=True)
    first = save_revision(event, result, kind="estimate", actor=actor, legacy_total=Decimal("140"))
    assert first.revision_number == 1
    assert first.created_by == actor
    assert first.gross_total == Decimal("150.00")
    assert event.total_price is None
    assert (first.profile_id_snapshot, first.profile_version) == (stock[0].pk, 3)
    assert (first.package_id_snapshot, first.package_version) == (stock[1].pk, 2)
    assert (first.workflow_id_snapshot, first.workflow_version) == (stock[5].pk, 4)
    assert first.input_json["catalog_snapshot"]["items"][str(stock[3].pk)]["price"] == "20.00"
    assert first.difference == Decimal("10.00")
    assert save_revision(event, result, kind="estimate", actor=actor, legacy_total=Decimal("140")).pk == first.pk
    second = save_revision(event, result, kind="draft", actor=actor)
    assert (second.revision_number, second.previous_revision_id) == (2, first.pk)
    assert PriceCalculationRevision.objects.filter(event=event).count() == 2


def test_migration_comparison_does_not_change_released_offer(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X",
                                 total_price=Decimal("999"))
    result = calculate_payload(payload(stock), public=True)
    released = PriceCalculationRevision.objects.create(event=event, kind="offer", status="released",
        revision_number=1, engine_version="2", gross_total=Decimal("999"))
    comparison = save_revision(event, result, kind="migration_compare", legacy_total=Decimal("999"))
    event.refresh_from_db()
    released.refresh_from_db()
    assert event.total_price == released.gross_total == Decimal("999")
    assert released.status == "released"
    assert comparison.previous_revision_id == released.pk


def test_draft_does_not_replace_released_offer_total(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X",
                                 total_price=Decimal("999"))
    PriceCalculationRevision.objects.create(event=event, kind="offer", status="released",
        revision_number=1, engine_version="2", gross_total=Decimal("999"))
    result = calculate_payload(payload(stock), public=True)
    save_revision(event, result, kind="migration_compare")
    save_revision(event, result, kind="draft")
    event.refresh_from_db()
    assert event.total_price == Decimal("999")


def test_retries_after_other_revisions_do_not_duplicate_estimate(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X")
    result = calculate_payload(payload(stock), public=True)
    estimate = save_revision(event, result, kind="estimate")
    save_revision(event, result, kind="migration_compare")
    assert save_revision(event, result, kind="estimate").pk == estimate.pk
    assert PriceCalculationRevision.objects.filter(event=event).count() == 2


@pytest.mark.parametrize("kind", ["estimate", "draft", "offer", "migration_compare"])
def test_exact_same_revision_retry_is_idempotent_for_each_kind(stock, kind):
    actor = get_user_model().objects.create_user(username=f"pricing-{kind}", password="x")
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X",
                                 total_price=Decimal("777"))
    result = calculate_payload(payload(stock), public=True)
    first = save_revision(event, result, kind=kind, actor=actor, legacy_total=Decimal("140"))
    repeated = save_revision(event, result, kind=kind, actor=actor, legacy_total=Decimal("140"))
    event.refresh_from_db()
    assert repeated.pk == first.pk
    assert PriceCalculationRevision.objects.filter(event=event).count() == 1
    assert event.total_price == Decimal("777")


def test_explicit_total_update_requires_opt_in_and_never_replaces_released_offer(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X",
                                 total_price=Decimal("777"))
    result = calculate_payload(payload(stock), public=True)
    save_revision(event, result, kind="draft", update_event_total=True)
    event.refresh_from_db()
    assert event.total_price == Decimal("150.00")
    event.total_price = Decimal("999")
    event.save(update_fields=["total_price"])
    PriceCalculationRevision.objects.create(event=event, kind="offer", status="released",
        revision_number=2, engine_version="2", gross_total=Decimal("999"))
    save_revision(event, result, kind="estimate", update_event_total=True)
    event.refresh_from_db()
    assert event.total_price == Decimal("999")


def test_opted_in_estimate_can_set_total_for_new_event(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X")
    save_revision(event, calculate_payload(payload(stock), public=True),
                  kind="estimate", update_event_total=True)
    event.refresh_from_db()
    assert event.total_price == Decimal("150.00")


def test_new_snapshot_and_actor_produce_new_draft_or_offer_revision(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X")
    actor = get_user_model().objects.create_user(username="first", password="x")
    other = get_user_model().objects.create_user(username="second", password="x")
    initial = calculate_payload(payload(stock), public=False)
    changed = calculate_payload(payload(stock, selected_item_ids=[]), public=False)
    for kind in ("draft", "offer"):
        first = save_revision(event, initial, kind=kind, actor=actor)
        second = save_revision(event, changed, kind=kind, actor=actor)
        third = save_revision(event, initial, kind=kind, actor=other)
        assert len({first.pk, second.pk, third.pk}) == 3


def test_same_result_but_different_comparison_is_a_new_revision(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X")
    result = calculate_payload(payload(stock), public=True)
    first = save_revision(event, result, kind="migration_compare", legacy_total=Decimal("100"))
    second = save_revision(event, result, kind="migration_compare", legacy_total=Decimal("105"))
    assert second.pk != first.pk
    assert (first.difference, second.difference) == (Decimal("50.00"), Decimal("45.00"))


def test_released_offer_retry_does_not_return_historical_offer(stock):
    event = Event.objects.create(name="Party", date=date(2026, 12, 2), location="X")
    result = calculate_payload(payload(stock), public=False)
    first = save_revision(event, result, kind="offer")
    first.status = PriceCalculationRevision.Status.RELEASED
    first.save(update_fields=["status"])
    second = save_revision(event, result, kind="offer")
    assert (second.revision_number, second.previous_revision_id) == (2, first.pk)
    assert second.status == PriceCalculationRevision.Status.DRAFT
