from datetime import date, datetime, time, timezone
from decimal import Decimal
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

from wishlist.pricing_v2.blocks import (
    BlockResult, calendar_factors_block, discount_block, duration_block, guest_tiers_block,
    items_block, minimum_price_block, package_block, rounding_block, setup_teardown_block,
    travel_block,
)
from wishlist.pricing_v2.calendar import CalendarFactor
from wishlist.pricing_v2.config import resolve_pricing_config
from wishlist.pricing_v2.input import PricingInput, PricingValidationError


def request(**changes):
    values = dict(event_date=date(2026, 1, 3), start_time=time(23), end_time=time(2),
                  package_id="party", selected_item_ids=("lights",), guest_count=50,
                  distance_km=Decimal("26.5"), requested_at=datetime(2025, 12, 1, tzinfo=timezone.utc))
    values.update(changes)
    return PricingInput(**values)


def config(**changes):
    values = {"included_hours": "2", "extra_hour_rate": "100", "setup_minutes": 30,
              "teardown_minutes": 20, "setup_teardown_hour_rate": "60", "included_km": "25",
              "km_rate": "0.65", "saturday_percent": "10", "sunday_percent": "0",
              "holiday_percent": "0", "holiday_region": "NW", "seasons": [], "lead_time_tiers": [],
              "guest_tiers": [{"lower_bound": 0, "upper_bound": 49, "value": "0"},
                              {"lower_bound": 50, "upper_bound": None, "value": "10"}],
              "minimum_price": "500", "rounding_mode": "nearest", "rounding_step": "5"}
    values.update(changes)
    return resolve_pricing_config(SimpleNamespace(id=1, version=2, config_json=values))


def catalog(**changes):
    value = {"packages": {"party": {"label": "Party", "base_price": "300", "included_item_ids": ("included",), "required_item_ids": ("required",)}},
             "items": {"included": {"label": "Included", "price": "30", "active": True, "category": "extra", "source": "catalog"},
                       "lights": {"label": "Lights", "price": "50", "active": True, "category": "extra", "source": "catalog"},
                       "required": {"label": "Required", "price": "20", "active": True, "category": "extra", "source": "catalog"}}}
    value.update(changes)
    return value


def test_package_and_items_charge_selected_required_and_custom_without_double_charging():
    value = request(selected_item_ids=("included", "lights", "lights"), custom_items=({"code": "admin", "label": "Admin item", "amount": Decimal("7")},))
    package = package_block(value, config(), Decimal("0"), catalog())
    result = items_block(value, config(), package.subtotal, catalog())
    assert package.lines[0].amount == Decimal("300")
    assert [(line.code, line.amount) for line in result.lines] == [("item:lights", Decimal("50")), ("item:required", Decimal("20")), ("custom:admin", Decimal("7"))]
    assert result.subtotal == Decimal("377")


@pytest.mark.parametrize("broken", [
    {"packages": {}},
    {"items": {"lights": {"label": "Lights", "price": "-1", "active": True, "category": "extra", "source": "catalog"}}},
])
def test_catalog_reference_and_negative_amounts_are_typed_errors(broken):
    with pytest.raises(PricingValidationError) as exc:
        if "packages" in broken:
            package_block(request(), config(), Decimal("0"), catalog(**broken))
        else:
            items_block(request(), config(), Decimal("0"), catalog(**broken))
    assert exc.value.code in {"reference_error", "invalid_input"}


def test_duration_overnight_boundary_and_one_minute_over_round_up():
    exact = duration_block(request(), config(), Decimal("0"), catalog())
    over = duration_block(request(end_time=time(2, 1)), config(), Decimal("0"), catalog())
    assert exact.lines[0].quantity == Decimal("1") and exact.subtotal == Decimal("100")
    assert over.lines[0].quantity == Decimal("2") and over.subtotal == Decimal("200")


def test_setup_defaults_event_override_and_disabled_factor():
    default = setup_teardown_block(request(setup_minutes=None, teardown_minutes=None), config(), Decimal("0"), catalog())
    override = setup_teardown_block(request(setup_minutes=10, teardown_minutes=20), config(), Decimal("0"), catalog())
    disabled = setup_teardown_block(request(setup_minutes=None, teardown_minutes=None), config(), Decimal("0"), catalog(), setup_minutes=0, teardown_minutes=0)
    assert default.lines[0].amount == Decimal("50")
    assert override.lines[0].amount == Decimal("30")
    assert disabled.lines == ()


def test_setup_fractional_hour_billing_preserves_decimal_quantity():
    result = setup_teardown_block(request(setup_minutes=1, teardown_minutes=0), config(), Decimal("0"), catalog())
    assert result.lines[0].quantity == Decimal(1) / Decimal(60)
    assert result.subtotal == Decimal(1)


def test_explicit_zero_event_setup_overrides_configured_default():
    result = setup_teardown_block(request(setup_minutes=0, teardown_minutes=0), config(), Decimal("0"), catalog())
    assert result.lines == ()


def test_travel_free_boundary_fractional_distance_missing_and_disabled():
    assert travel_block(request(distance_km=Decimal("25")), config(), Decimal("0"), catalog()).lines == ()
    assert travel_block(request(), config(), Decimal("0"), catalog()).lines[0].amount == Decimal("0.975")
    with pytest.raises(PricingValidationError, match="distance") as exc:
        travel_block(request(distance_km=None), config(), Decimal("0"), catalog())
    assert exc.value.code == "missing_input"
    disabled = resolve_pricing_config(SimpleNamespace(id=1, version=2, config_json={
        "included_hours": "2", "extra_hour_rate": "100", "setup_minutes": 30, "teardown_minutes": 20,
           "setup_teardown_hour_rate": "60", "included_km": "25", "km_rate": "0.65", "saturday_percent": "0",
        "sunday_percent": "0", "holiday_percent": "0", "holiday_region": "NW", "seasons": [], "lead_time_tiers": [],
        "guest_tiers": [], "minimum_price": "0", "rounding_mode": "nearest", "rounding_step": "5"}),
        SimpleNamespace(id=2, version=3, overrides_json={"km_rate": {"mode": "disabled"}}))
    assert travel_block(request(), disabled, Decimal("0"), catalog()).lines == ()


def test_guest_calendar_discount_minimum_and_rounding_respect_incoming_subtotal():
    guest = guest_tiers_block(request(), config(), Decimal("100"), catalog())
    factors = (CalendarFactor("saturday", Decimal("10"), "global", 2, None, "Saturday"),)
    calendar = calendar_factors_block(request(), config(), guest.subtotal, factors)
    discount = discount_block(request(discount_percent=Decimal("20"), discount_amount=Decimal("5")), config(), calendar.subtotal, catalog())
    minimum = minimum_price_block(request(), config(minimum_price="100"), discount.subtotal, catalog())
    rounded = rounding_block(request(), config(rounding_step="10", rounding_mode="nearest"), Decimal("105"), catalog())
    assert guest.subtotal == Decimal("110") and calendar.subtotal == Decimal("121")
    assert discount.subtotal == Decimal("91.8") and minimum.subtotal == Decimal("100")
    assert rounded.subtotal == Decimal("110") and rounded.lines[0].amount == Decimal("5")


@pytest.mark.parametrize("mode,expected", [("nearest", "110"), ("up", "110"), ("down", "100")])
def test_rounding_modes_have_deterministic_half_step_behavior(mode, expected):
    assert rounding_block(request(), config(rounding_mode=mode, rounding_step="10"), Decimal("105"), catalog()).subtotal == Decimal(expected)


def test_invalid_discount_and_guest_no_match_are_never_silent():
    with pytest.raises(PricingValidationError) as discount:
        discount_block(request(discount_percent=Decimal("101")), config(), Decimal("100"), catalog())
    assert discount.value.code == "invalid_input"
    with pytest.raises(PricingValidationError) as tier:
        guest_tiers_block(request(guest_count=2), config(guest_tiers=[]), Decimal("100"), catalog())
    assert tier.value.code == "calculation_error"


def test_blocks_are_immutable_and_deterministic_without_mutating_context():
    context = catalog()
    value, resolved = request(), config()
    first = package_block(value, resolved, Decimal("0"), context)
    assert isinstance(first, BlockResult) and first == package_block(value, resolved, Decimal("0"), context)
    assert context["packages"]["party"]["base_price"] == "300"


def test_explicit_event_setup_and_teardown_override_defaults_and_fractional_hours():
    result = setup_teardown_block(request(setup_minutes=10, teardown_minutes=20), config(), Decimal("0"), catalog())
    assert result.lines[0].quantity == Decimal("0.5")
    assert result.subtotal == Decimal("30")


def test_keyword_setup_override_takes_precedence_even_when_zero():
    result = setup_teardown_block(request(setup_minutes=10, teardown_minutes=20), config(), Decimal("0"), catalog(), setup_minutes=0)
    assert result.lines[0].quantity == Decimal("0.3333333333333333333333333333")


def test_package_and_selected_ids_are_validated_and_duplicate_custom_codes_rejected():
    with pytest.raises(PricingValidationError) as invalid:
        items_block(request(selected_item_ids=("lights", "missing")), config(), Decimal("0"), catalog())
    assert invalid.value.code == "reference_error"
    with pytest.raises(PricingValidationError) as duplicate:
        items_block(request(custom_items=({"code": "x", "label": "one", "amount": "1"},
                                          {"code": "x", "label": "two", "amount": "2"})), config(), Decimal("0"), catalog())
    assert duplicate.value.code == "invalid_input"


def test_items_reject_inactive_optional_item_and_restore_inactive_required_item():
    context = catalog(items={"lights": {"label": "Lights", "price": "50", "active": False},
                             "required": {"label": "Required", "price": "20", "active": False},
                             "included": {"label": "Included", "price": "30", "active": True}})
    with pytest.raises(PricingValidationError) as error:
        items_block(request(selected_item_ids=("lights",)), config(), Decimal("0"), context)
    assert error.value.code == "reference_error"
    result = items_block(request(selected_item_ids=()), config(), Decimal("0"), context)
    assert [line.code for line in result.lines] == ["item:required"]


def test_zero_package_and_zero_items_do_not_emit_lines():
    context = catalog(packages={"party": {"label": "Party", "base_price": "0", "included_item_ids": (), "required_item_ids": ()}},
                      items={"lights": {"label": "Lights", "price": "0", "active": True}})
    assert package_block(request(), config(), Decimal("0"), context).lines == ()
    assert items_block(request(selected_item_ids=("lights",)), config(), Decimal("0"), context).lines == ()


def test_calendar_factor_stack_is_sequential_and_skips_zero_without_mutation():
    factors = (CalendarFactor("first", Decimal("10"), "profile", 1, None, "First"),
               CalendarFactor("zero", Decimal("0"), "profile", 1, None, "Zero"),
               CalendarFactor("second", Decimal("10"), "override", 1, 2, "Second"))
    result = calendar_factors_block(request(), config(), Decimal("100"), factors)
    assert [(line.code, line.amount, line.source) for line in result.lines] == [
        ("calendar:first", Decimal("10"), "profile"), ("calendar:second", Decimal("11"), "override")]
    assert result.subtotal == Decimal("121")


def test_catalog_context_and_request_are_not_mutated():
    context = catalog()
    before = repr(context)
    value = request(selected_item_ids=("lights", "lights"))
    items_block(value, config(), Decimal("0"), context)
    assert repr(context) == before
    assert value.selected_item_ids == ("lights", "lights")


def test_negative_or_malformed_calculation_inputs_are_rejected():
    with pytest.raises(PricingValidationError) as subtotal:
        package_block(request(), config(), Decimal("-1"), catalog())
    assert subtotal.value.code == "invalid_input"
    with pytest.raises(PricingValidationError) as discount:
        discount_block(request(discount_percent=Decimal("-1")), config(), Decimal("100"), catalog())
    assert discount.value.code == "invalid_input"


def test_disabled_setup_defaults_are_non_applicable_even_with_rate_enabled():
    resolved = config()
    disabled = replace(resolved, setup_minutes=replace(resolved.setup_minutes, value=None),
                       teardown_minutes=replace(resolved.teardown_minutes, value=None))
    assert setup_teardown_block(request(setup_minutes=None, teardown_minutes=None), disabled, Decimal("12"), catalog()) == BlockResult((), Decimal("12"))


def test_disabled_setup_component_does_not_suppress_enabled_teardown():
    resolved = config()
    disabled = replace(resolved, setup_minutes=replace(resolved.setup_minutes, value=None))
    result = setup_teardown_block(request(setup_minutes=None, teardown_minutes=None), disabled, Decimal("0"), catalog())
    assert result.subtotal == Decimal("20")


def test_explicit_event_minutes_can_enable_disabled_default_component():
    resolved = config()
    disabled = replace(resolved, setup_minutes=replace(resolved.setup_minutes, value=None),
                       teardown_minutes=replace(resolved.teardown_minutes, value=None))
    result = setup_teardown_block(request(setup_minutes=10, teardown_minutes=None), disabled, Decimal("0"), catalog())
    assert result.subtotal == Decimal("10")


def test_missing_included_km_with_charged_travel_is_configuration_error():
    resolved = config()
    missing = replace(resolved, included_km=replace(resolved.included_km, value=None))
    with pytest.raises(PricingValidationError) as error:
        travel_block(request(), missing, Decimal("0"), catalog())
    assert error.value.code == "calculation_error"


def test_equal_start_and_end_is_rejected_not_charged_as_24_hours():
    with pytest.raises(PricingValidationError) as error:
        duration_block(request(start_time=time(23), end_time=time(23)), config(), Decimal("0"), catalog())
    assert error.value.code == "invalid_input"


def test_exact_included_duration_has_no_line_and_overnight_is_three_hours():
    result = duration_block(request(), config(included_hours="3"), Decimal("12"), catalog())
    assert result == BlockResult((), Decimal("12"))


@pytest.mark.parametrize("step,base,nearest,up,down", [
    ("5", "102.5", "105", "105", "100"),
    ("10", "105", "110", "110", "100"),
    ("50", "125", "150", "150", "100"),
])
def test_rounding_halfway_ties_go_up_for_all_supported_steps(step, base, nearest, up, down):
    for mode, expected in (("nearest", nearest), ("up", up), ("down", down)):
        result = rounding_block(request(), config(rounding_step=step, rounding_mode=mode), Decimal(base), catalog())
        assert result.subtotal == Decimal(expected)
        assert result.lines[0].amount == Decimal(expected) - Decimal(base)


def test_minimum_applied_before_discount_differs_from_minimum_after_discount():
    value, resolved = request(discount_amount=Decimal("30")), config(minimum_price="100")
    before = discount_block(value, resolved, minimum_price_block(value, resolved, Decimal("80"), catalog()).subtotal, catalog())
    after = minimum_price_block(value, resolved, discount_block(value, resolved, Decimal("80"), catalog()).subtotal, catalog())
    assert before.subtotal == Decimal("70")
    assert after.subtotal == Decimal("100")


def test_required_items_and_included_references_are_validated_even_without_charge():
    broken = catalog(packages={"party": {"label": "Party", "base_price": "300", "included_item_ids": ("missing",), "required_item_ids": ("required",)}})
    with pytest.raises(PricingValidationError) as error:
        items_block(request(selected_item_ids=()), config(), Decimal("0"), broken)
    assert error.value.code == "reference_error"


def test_negative_required_item_is_not_silently_skipped_when_included():
    broken = catalog(packages={"party": {"label": "Party", "base_price": "300", "included_item_ids": ("required",), "required_item_ids": ("required",)}},
                     items={"required": {"label": "Required", "price": "-1", "active": True}})
    with pytest.raises(PricingValidationError) as error:
        items_block(request(selected_item_ids=()), config(), Decimal("0"), broken)
    assert error.value.code == "invalid_input"


def test_block_result_and_prices_are_immutable_decimals():
    result = items_block(request(), config(), Decimal("1.125"), catalog())
    assert result.subtotal == Decimal("71.125")
    assert isinstance(result.subtotal, Decimal)
    assert all(isinstance(line.amount, Decimal) and isinstance(line.unit_price, Decimal) for line in result.lines)
    with pytest.raises(FrozenInstanceError):
        result.subtotal = Decimal("0")


def test_discount_exceeding_subtotal_is_capped_without_zero_amount_lines():
    capped = discount_block(request(discount_percent=Decimal("100"), discount_amount=Decimal("25")),
                            config(), Decimal("10"), catalog())
    assert capped.subtotal == Decimal("0")
    assert [(line.code, line.amount) for line in capped.lines] == [("discount:percent", Decimal("-10"))]
    empty = discount_block(request(discount_amount=Decimal("25")), config(), Decimal("0"), catalog())
    assert empty.lines == ()


def test_invalid_custom_item_amount_and_float_are_rejected():
    for amount in ("-1", 1.5):
        with pytest.raises(PricingValidationError) as error:
            items_block(request(selected_item_ids=(), custom_items=({"code": "extra", "label": "Extra", "amount": amount},)),
                        config(), Decimal("0"), catalog())
        assert error.value.code == "invalid_input"
