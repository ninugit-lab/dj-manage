from datetime import date, datetime, time, timezone
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from wishlist.pricing_v2 import PricingInput, PricingLine, PricingResult, PricingValidationError


def valid_data(**overrides):
    data = {
        "event_date": "2026-12-24",
        "start_time": "23:00",
        "end_time": "02:00",
        "package_id": "party",
        "selected_item_ids": ["lights"],
        "guest_count": 50,
        "distance_km": "12.50",
        "setup_minutes": 30,
        "teardown_minutes": 20,
    }
    data.update(overrides)
    return data


def test_normalizes_iso_and_native_date_time_and_preserves_overnight():
    result = PricingInput.from_mapping(
        valid_data(event_date=date(2026, 12, 24), start_time=time(23), end_time=time(2)),
        requested_at=datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc),
    )
    assert result.event_date == date(2026, 12, 24)
    assert result.start_time == time(23)
    assert result.end_time == time(2)


def test_requested_at_is_stored_deterministically_and_naive_is_utc():
    result = PricingInput.from_mapping(valid_data(), requested_at="2026-10-22T09:30:00Z")
    assert result.requested_at == datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc)


def test_decimal_distance_has_no_float_leakage_and_region_defaults():
    result = PricingInput.from_mapping(valid_data(distance_km=Decimal("12.50")), requested_at=datetime.now(timezone.utc))
    assert result.distance_km == Decimal("12.50")
    assert result.pricing_region == "NW"

    explicit = PricingInput.from_mapping(valid_data(pricing_region="  BY "), requested_at=datetime.now(timezone.utc))
    assert explicit.pricing_region == "BY"


@pytest.mark.parametrize("data,field", [({key: value for key, value in valid_data().items() if key != "event_date"}, "event_date")])
def test_missing_required_input_uses_typed_error(data, field):
    with pytest.raises(PricingValidationError) as exc:
        PricingInput.from_mapping(data, requested_at=datetime.now(timezone.utc))
    assert exc.value.code == "missing_input"
    assert exc.value.field == field


@pytest.mark.parametrize("field,value", [("event_date", "not-a-date"), ("distance_km", "nope")])
def test_malformed_input_uses_invalid_error(field, value):
    with pytest.raises(PricingValidationError) as exc:
        PricingInput.from_mapping(valid_data(**{field: value}), requested_at=datetime.now(timezone.utc))
    assert exc.value.code == "invalid_input"
    assert exc.value.field == field


@pytest.mark.parametrize("field", ["guest_count", "setup_minutes", "teardown_minutes", "distance_km"])
def test_negative_values_are_rejected(field):
    with pytest.raises(PricingValidationError) as exc:
        PricingInput.from_mapping(valid_data(**{field: -1}), requested_at=datetime.now(timezone.utc))
    assert exc.value.code == "invalid_input"
    assert exc.value.field == field


def test_bool_is_not_an_integer_input():
    with pytest.raises(PricingValidationError) as exc:
        PricingInput.from_mapping(valid_data(guest_count=True), requested_at=datetime.now(timezone.utc))
    assert exc.value.field == "guest_count"


def test_contract_dataclasses_are_frozen():
    value = PricingInput.from_mapping(valid_data(), requested_at=datetime.now(timezone.utc))
    with pytest.raises(FrozenInstanceError):
        value.guest_count = 2


def test_result_serializes_decimals_dates_times_and_ordered_lines():
    line = PricingLine("travel", "Anfahrt", "42 km", Decimal("22.00"), "km", Decimal("0.50"), Decimal("11.00"), "global")
    result = PricingResult(
        engine_version="2.0", profile_version=1, package_version=2, workflow_version=3,
        inputs={"event_date": date(2026, 1, 2), "at": time(9), "when": datetime(2026, 1, 2, 9, tzinfo=timezone.utc)},
        lines=(line,), gross_total=Decimal("11.00"),
    )
    expected = result.to_dict()
    assert expected["lines"][0]["amount"] == "11.00"
    assert expected["gross_total"] == "11.00"
    assert expected["inputs"]["event_date"] == "2026-01-02"
    assert expected["inputs"]["at"] == "09:00:00"
    assert result.to_dict() == expected
