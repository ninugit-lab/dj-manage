from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import wishlist.pricing_v2.calendar as pricing_calendar
from wishlist.pricing_v2.calendar import calendar_factors, select_tier
from wishlist.pricing_v2.config import resolve_pricing_config
from wishlist.pricing_v2.input import PricingValidationError


def profile_config(**changes):
    config = {
        "included_hours": "4", "extra_hour_rate": "125.50", "setup_minutes": 30,
        "teardown_minutes": 20, "setup_teardown_hour_rate": "80", "included_km": "25",
        "km_rate": "0.65", "saturday_percent": "10", "sunday_percent": "20",
        "holiday_percent": "30", "holiday_region": "NW",
        "seasons": [{"identifier": "winter", "label": "Winter", "start": "12-01", "end": "02-28", "percent": "15"}],
        "lead_time_tiers": [{"lower_bound": 0, "upper_bound": 6, "value": "25"}, {"lower_bound": 7, "upper_bound": None, "value": "0"}],
        "guest_tiers": [{"lower_bound": 0, "upper_bound": 49, "value": "0"}, {"lower_bound": 50, "upper_bound": None, "value": "10"}],
        "minimum_price": "500", "rounding_mode": "nearest", "rounding_step": "0.05",
    }
    config.update(changes)
    return config


def resolved(changes=None, overrides=None):
    profile = SimpleNamespace(id=1, version=4, config_json=profile_config(**(changes or {})))
    package = SimpleNamespace(id=2, version=9, overrides_json=overrides or {})
    return resolve_pricing_config(profile, package)


def factors(event_date, requested_at, config=None, region=None):
    return calendar_factors(event_date, requested_at, region, config or resolved())


def codes(result):
    return [factor.code for factor in result]


def test_german_state_holidays_include_nrw_new_year_good_friday_christmas_and_corpus_christi():
    config = resolved(changes={"seasons": [], "lead_time_tiers": []})
    for day in (date(2026, 1, 1), date(2026, 4, 3), date(2026, 12, 25)):
        assert "holiday" in codes(factors(day, datetime(2025, 1, 1, tzinfo=timezone.utc), config))
    corpus_christi = date(2026, 6, 4)
    assert "holiday" in codes(factors(corpus_christi, datetime(2025, 1, 1, tzinfo=timezone.utc), config, "NW"))
    assert "holiday" not in codes(factors(corpus_christi, datetime(2025, 1, 1, tzinfo=timezone.utc), config, "BE"))


def test_weekend_holiday_season_and_lead_time_stack_in_stable_order():
    config = resolved(changes={"seasons": [{"identifier": "winter", "label": "Winter", "start": "12-01", "end": "02-28", "percent": "15"}]})
    result = factors(date(2022, 1, 1), datetime(2021, 12, 31, 23, tzinfo=timezone.utc), config)
    assert codes(result) == ["saturday", "holiday", "season:winter", "lead_time:0"]
    assert [factor.percent for factor in result] == [Decimal("10"), Decimal("30"), Decimal("15"), Decimal("25")]
    assert result[0].source == "global" and result[0].profile_version == 4


def test_sunday_factor_is_applied_on_sunday():
    result = factors(date(2022, 1, 2), datetime(2021, 1, 1, tzinfo=timezone.utc), resolved(changes={"seasons": [], "lead_time_tiers": []}))
    assert codes(result) == ["sunday"]


def test_season_bounds_are_inclusive_and_year_wraps():
    config = resolved(changes={"seasons": [
        {"identifier": "spring", "label": "Spring", "start": "03-01", "end": "03-02", "percent": "5"},
        {"identifier": "winter", "label": "Winter", "start": "12-01", "end": "02-28", "percent": "15"},
    ], "lead_time_tiers": []})
    for day in (date(2026, 3, 1), date(2026, 3, 2), date(2026, 12, 1), date(2027, 2, 28)):
        assert any(code.startswith("season:") for code in codes(factors(day, datetime(2025, 1, 1, tzinfo=timezone.utc), config)))
    assert not any(code.startswith("season:") for code in codes(factors(date(2026, 3, 3), datetime(2025, 1, 1, tzinfo=timezone.utc), config)))


def test_lead_time_uses_berlin_local_date_across_midnight_and_dst():
    config = resolved(changes={"seasons": [], "lead_time_tiers": [
        {"lower_bound": 0, "upper_bound": 0, "value": "1"},
        {"lower_bound": 1, "upper_bound": None, "value": "2"},
    ]})
    at_berlin_midnight = datetime(2026, 3, 28, 23, 30, tzinfo=timezone.utc)
    assert "lead_time:1" in codes(factors(date(2026, 3, 30), at_berlin_midnight, config))
    assert "lead_time:0" in codes(factors(date(2026, 3, 29), at_berlin_midnight, config))
    after_dst_jump = datetime(2026, 3, 29, 22, 30, tzinfo=timezone.utc)
    assert "lead_time:1" in codes(factors(date(2026, 3, 31), after_dst_jump, config))


def test_past_event_clamps_lead_time_to_zero_days():
    config = resolved(changes={"seasons": [], "lead_time_tiers": [
        {"lower_bound": 0, "upper_bound": 0, "value": "7"},
        {"lower_bound": 1, "upper_bound": None, "value": "2"},
    ]})
    result = factors(date(2026, 1, 1), datetime(2026, 1, 2, tzinfo=timezone.utc), config)
    lead = next(factor for factor in result if factor.code.startswith("lead_time:"))
    assert (lead.code, lead.percent, lead.description) == ("lead_time:0", Decimal("7"), "0 days before event")


@pytest.mark.parametrize("value,low,high", [(0, 0, 49), (49, 0, 49), (50, 50, None), (99, 50, None)])
def test_select_tier_inclusive_boundaries(value, low, high):
    tiers = resolved().guest_tiers.value
    selected = select_tier(value, tiers)
    assert (selected.lower_bound, selected.upper_bound) == (low, high)


def test_select_tier_returns_none_for_gap_and_rejects_negative_or_bool():
    tiers = (SimpleNamespace(lower_bound=0, upper_bound=2), SimpleNamespace(lower_bound=4, upper_bound=None))
    assert select_tier(3, tiers) is None
    for value in (-1, True, False):
        with pytest.raises(PricingValidationError):
            select_tier(value, tiers)


def test_disabled_factors_never_apply():
    config = resolved(changes={"seasons": [{"identifier": "x", "label": "X", "start": "01-01", "end": "01-02", "percent": "99"}]}, overrides={
        "saturday_percent": {"mode": "disabled"}, "holiday_percent": {"mode": "disabled"},
        "seasons": {"mode": "disabled"}, "lead_time_tiers": {"mode": "disabled"},
    })
    result = factors(date(2022, 1, 1), datetime(2021, 1, 1, tzinfo=timezone.utc), config)
    assert codes(result) == []


def test_rejects_unknown_region_without_fallback():
    with pytest.raises(PricingValidationError):
        factors(date(2026, 1, 1), datetime(2025, 1, 1, tzinfo=timezone.utc), region="XX")


def test_valid_region_does_not_require_optional_holidays_subdivisions_attribute(monkeypatch):
    germany = pricing_calendar.holidays.Germany
    monkeypatch.setattr(pricing_calendar.holidays, "Germany", lambda **kwargs: germany(**kwargs))
    result = factors(date(2026, 1, 1), datetime(2025, 1, 1, tzinfo=timezone.utc), region="NW")
    assert "holiday" in codes(result)


def test_region_error_remains_typed_when_optional_subdivision_attribute_is_absent(monkeypatch):
    germany = pricing_calendar.holidays.Germany
    monkeypatch.setattr(pricing_calendar.holidays, "Germany", lambda **kwargs: germany(**kwargs))
    with pytest.raises(PricingValidationError) as exc:
        factors(date(2026, 1, 1), datetime(2025, 1, 1, tzinfo=timezone.utc), region="XX")
    assert exc.value.code == "invalid_input"


def test_repeated_calls_are_identical_and_do_not_mutate_inputs():
    config = resolved()
    event = date(2026, 2, 1)
    requested = datetime(2026, 1, 1, tzinfo=timezone.utc)
    before = (config, event, requested)
    first = factors(event, requested, config)
    second = factors(event, requested, config)
    assert first == second
    assert (config, event, requested) == before
