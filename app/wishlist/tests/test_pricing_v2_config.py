from dataclasses import FrozenInstanceError
from decimal import Decimal
from types import SimpleNamespace

import pytest

from wishlist.pricing_v2.config import resolve_pricing_config, validate_profile_config
from wishlist.pricing_v2.input import PricingValidationError


def profile_config(**changes):
    config = {
        "included_hours": "4", "extra_hour_rate": "125.50",
        "setup_minutes": 30, "teardown_minutes": 20,
        "setup_teardown_hour_rate": "80", "included_km": "25",
        "km_rate": "0.65", "saturday_percent": "10",
        "sunday_percent": "20", "holiday_percent": "30",
        "holiday_region": "NW",
        "seasons": [{"identifier": "winter", "label": "Winter", "start": "12-01", "end": "02-28", "percent": "15"}],
        "lead_time_tiers": [{"lower_bound": 0, "upper_bound": 6, "value": "25"}, {"lower_bound": 7, "upper_bound": None, "value": "0"}],
        "guest_tiers": [{"lower_bound": 0, "upper_bound": 49, "value": "0"}, {"lower_bound": 50, "upper_bound": None, "value": "10"}],
        "minimum_price": "500", "rounding_mode": "nearest", "rounding_step": "0.05",
    }
    config.update(changes)
    return config


def profile(config=None, *, id=10, version=3):
    return SimpleNamespace(id=id, version=version, config_json=profile_config() if config is None else config)


def package(overrides=None, *, id=20, version=7):
    return SimpleNamespace(id=id, version=version, overrides_json={} if overrides is None else overrides)


def assert_invalid(config, field):
    with pytest.raises(PricingValidationError) as exc:
        resolve_pricing_config(profile(config))
    assert (exc.value.code, exc.value.field) == ("invalid_input", field)


def test_resolves_every_profile_key_to_typed_immutable_global_factor():
    # Break caught: removing a normalizer or source metadata from any profile factor.
    result = resolve_pricing_config(profile())

    assert result.included_hours.value == Decimal("4")
    assert result.extra_hour_rate.value == Decimal("125.50")
    assert result.setup_minutes.value == 30
    assert result.teardown_minutes.value == 20
    assert result.setup_teardown_hour_rate.value == Decimal("80")
    assert result.included_km.value == Decimal("25")
    assert result.km_rate.value == Decimal("0.65")
    assert result.saturday_percent.value == Decimal("10")
    assert result.sunday_percent.value == Decimal("20")
    assert result.holiday_percent.value == Decimal("30")
    assert result.holiday_region.value == "NW"
    assert result.minimum_price.value == Decimal("500")
    assert result.rounding_mode.value == "nearest"
    assert result.rounding_step.value == Decimal("0.05")
    assert result.seasons.value[0].identifier == "winter"
    assert result.lead_time_tiers.value[0].upper_bound == 6
    assert result.guest_tiers.value[1].lower_bound == 50
    assert result.included_hours.source == "global"
    assert result.included_hours.profile_version == 3
    assert result.included_hours.package_version is None
    assert "profile" in result.included_hours.description
    with pytest.raises(FrozenInstanceError):
        result.included_hours.value = Decimal("1")


@pytest.mark.parametrize("field", ["included_hours", "extra_hour_rate", "setup_teardown_hour_rate", "included_km", "km_rate", "saturday_percent", "sunday_percent", "holiday_percent", "minimum_price", "rounding_step"])
@pytest.mark.parametrize("value", [1.5, True, "-1", "NaN"])
def test_rejects_float_bool_and_negative_or_nonfinite_decimal_values(field, value):
    assert_invalid(profile_config(**{field: value}), field)


@pytest.mark.parametrize("field,value", [("setup_minutes", True), ("teardown_minutes", -1), ("setup_minutes", "30")])
def test_setup_and_teardown_require_nonnegative_integers(field, value):
    assert_invalid(profile_config(**{field: value}), field)


def test_defaults_and_validates_german_holiday_region():
    assert resolve_pricing_config(profile(profile_config(holiday_region="BY"))).holiday_region.value == "BY"
    assert resolve_pricing_config(profile(profile_config(holiday_region=None))).holiday_region.value == "NW"
    assert_invalid(profile_config(holiday_region="XX"), "holiday_region")


def test_season_percent_rejects_boolean():
    assert_invalid(profile_config(seasons=[
        {"identifier": "bool", "label": "Bool", "start": "03-01", "end": "03-02", "percent": True},
    ]), "seasons")


@pytest.mark.parametrize("field,value", [("rounding_mode", "ceil"), ("rounding_step", "0"), ("rounding_step", "-0.01")])
def test_rejects_bad_rounding_configuration(field, value):
    assert_invalid(profile_config(**{field: value}), field)


def test_rejects_unknown_profile_keys():
    assert_invalid(profile_config(silent_typo="bad"), "config_json")


def test_seasons_are_inclusive_and_detect_collision_across_year_end():
    result = resolve_pricing_config(profile())
    season = result.seasons.value[0]
    assert season.includes("12-01") is True
    assert season.includes("02-28") is True
    assert season.includes("03-01") is False

    assert_invalid(profile_config(seasons=[
        {"identifier": "winter", "label": "Winter", "start": "12-01", "end": "02-28", "percent": "15"},
        {"identifier": "collision", "label": "Collision", "start": "02-28", "end": "03-01", "percent": "5"},
    ]), "seasons")


def test_seasons_reject_invalid_month_days_and_duplicate_identifiers():
    assert_invalid(profile_config(seasons=[
        {"identifier": "bad", "label": "Bad", "start": "02-30", "end": "03-01", "percent": "1"},
    ]), "seasons")
    assert_invalid(profile_config(seasons=[
        {"identifier": "same", "label": "One", "start": "03-01", "end": "03-02", "percent": "1"},
        {"identifier": "same", "label": "Two", "start": "04-01", "end": "04-02", "percent": "1"},
    ]), "seasons")


@pytest.mark.parametrize("field", ["seasons", "lead_time_tiers", "guest_tiers"])
def test_rejects_overlapping_or_malformed_ranges(field):
    bad = {
        "seasons": [{"identifier": "a", "label": "A", "start": "01-01", "end": "01-02", "percent": "1"}, {"identifier": "b", "label": "B", "start": "01-02", "end": "01-03", "percent": "1"}],
        "lead_time_tiers": [{"lower_bound": 0, "upper_bound": 3, "value": "1"}, {"lower_bound": 3, "upper_bound": None, "value": "2"}],
        "guest_tiers": [{"lower_bound": 1, "upper_bound": None, "value": "1"}, {"lower_bound": 2, "upper_bound": 4, "value": "2"}],
    }[field]
    assert_invalid(profile_config(**{field: bad}), field)


@pytest.mark.parametrize("field,tiers", [
    ("lead_time_tiers", [{"lower_bound": -1, "upper_bound": 2, "value": "1"}]),
    ("guest_tiers", [{"lower_bound": 2, "upper_bound": 1, "value": "1"}]),
    ("lead_time_tiers", [{"lower_bound": True, "upper_bound": 2, "value": "1"}]),
    ("guest_tiers", [{"lower_bound": 0, "upper_bound": None, "value": 1.5}]),
])
def test_tiers_require_valid_inclusive_nonnegative_bounds_and_decimal_values(field, tiers):
    assert_invalid(profile_config(**{field: tiers}), field)


def test_package_override_inherit_override_and_disabled_preserve_metadata():
    result = resolve_pricing_config(profile(), package({
        "included_hours": {"mode": "inherit"},
        "extra_hour_rate": {"mode": "override", "value": "99.00"},
        "sunday_percent": {"mode": "disabled"},
    }))

    assert result.included_hours.value == Decimal("4")
    assert result.included_hours.source == "global"
    assert result.included_hours.package_version == 7
    assert result.extra_hour_rate.value == Decimal("99.00")
    assert result.extra_hour_rate.source == "package_override"
    assert result.extra_hour_rate.package_version == 7
    assert "package override" in result.extra_hour_rate.description
    assert result.sunday_percent.value is None
    assert result.sunday_percent.source == "package_disabled"


@pytest.mark.parametrize("overrides", [
    {"unknown": {"mode": "inherit"}},
    {"included_hours": {"mode": "wrong"}},
    {"included_hours": {"mode": "override"}},
    {"included_hours": {"mode": "inherit", "value": "1"}},
    {"included_hours": {"mode": "disabled", "value": "1"}},
    {"included_hours": "override"},
    {"included_hours": {"mode": "override", "value": 1.5}},
])
def test_rejects_malformed_package_overrides(overrides):
    with pytest.raises(PricingValidationError) as exc:
        resolve_pricing_config(profile(), package(overrides))
    assert exc.value.code == "invalid_input"


def test_explicit_null_override_is_invalid_not_inherit():
    with pytest.raises(PricingValidationError) as exc:
        resolve_pricing_config(profile(), package({"included_hours": None}))
    assert (exc.value.code, exc.value.field) == ("invalid_input", "overrides_json")


@pytest.mark.parametrize("value", [[], {}, None, 1])
def test_unhashable_or_non_string_rounding_mode_is_typed_invalid_input(value):
    assert_invalid(profile_config(rounding_mode=value), "rounding_mode")


@pytest.mark.parametrize("config", [[], None, {"included_hours": "4"}])
def test_validate_profile_config_rejects_malformed_mapping_and_exact_keys(config):
    with pytest.raises(PricingValidationError) as exc:
        validate_profile_config(config)
    assert exc.value.code == "invalid_input"


def test_validate_profile_config_normalizes_exact_complete_mapping():
    normalized = validate_profile_config(profile_config())
    assert normalized.included_hours == Decimal("4")


@pytest.mark.parametrize("bad_profile", [
    None, profile(id=None), profile(id=0), profile(version=None), profile(version=0),
])
def test_bad_profile_identity_is_reference_error(bad_profile):
    with pytest.raises(PricingValidationError) as exc:
        resolve_pricing_config(bad_profile)
    assert exc.value.code == "reference_error"


@pytest.mark.parametrize("bad_profile,bad_package", [
    (profile(id=None), None), (profile(version=None), None), (profile(config=[]), None),
    (profile(), package(id=None)), (profile(), package(version=None)), (profile(), package([])),
])
def test_rejects_missing_or_malformed_references(bad_profile, bad_package):
    with pytest.raises(PricingValidationError) as exc:
        resolve_pricing_config(bad_profile, bad_package)
    assert exc.value.code == "reference_error"


def test_rejects_absent_profile_reference():
    with pytest.raises(PricingValidationError) as exc:
        resolve_pricing_config(None)
    assert exc.value.code == "reference_error"


def test_normalized_values_are_deeply_immutable_and_not_affected_by_caller_mutation():
    config = profile_config()
    result = resolve_pricing_config(profile(config))
    config["seasons"][0]["label"] = "Changed"
    config["lead_time_tiers"].append({"lower_bound": 100, "upper_bound": None, "value": "1"})
    assert result.seasons.value[0].label == "Winter"
    assert len(result.lead_time_tiers.value) == 2
    assert result.normalized.seasons == result.seasons.value


def test_range_normalization_is_deterministic_for_equivalent_input_order():
    unordered = profile_config(lead_time_tiers=[
        {"lower_bound": 7, "upper_bound": None, "value": "0"},
        {"lower_bound": 0, "upper_bound": 6, "value": "25"},
    ])
    resolved = resolve_pricing_config(profile(unordered))

    assert tuple(tier.lower_bound for tier in resolved.lead_time_tiers.value) == (0, 7)
    assert resolved.normalized.lead_time_tiers == resolved.lead_time_tiers.value
