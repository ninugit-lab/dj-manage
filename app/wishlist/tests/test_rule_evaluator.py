import datetime
from types import SimpleNamespace

import pytest

from wishlist.price_engine import RuleEvaluator


def make_event(**kwargs):
    defaults = dict(
        date=datetime.date(2026, 6, 20),  # Saturday
        time_start=datetime.time(20, 0),
        time_end=datetime.time(23, 0),
        guest_count=80,
        event_type="Hochzeit",
        distance_km=15,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def make_rule(conditions):
    return SimpleNamespace(condition_json=conditions)


def test_build_context_extracts_duration_and_weekday():
    ctx = RuleEvaluator.build_context(make_event())
    assert ctx["duration_hours"] == 3.0
    assert ctx["date_weekday"] == 5  # Saturday
    assert ctx["guest_count"] == 80
    assert ctx["event_type"] == "Hochzeit"
    assert ctx["date_month"] == 6


def test_build_context_handles_overnight_event():
    ev = make_event(time_start=datetime.time(22, 0), time_end=datetime.time(2, 0))
    ctx = RuleEvaluator.build_context(ev)
    assert ctx["duration_hours"] == 4.0


def test_offer_guest_count_overrides_event():
    offer = SimpleNamespace(guest_count=120)
    ctx = RuleEvaluator.build_context(make_event(guest_count=80), offer)
    assert ctx["guest_count"] == 120


def test_empty_conditions_always_match():
    assert RuleEvaluator.evaluate(make_rule([]), {}) is True


def test_gt_condition_matches():
    rule = make_rule([{"field": "guest_count", "op": "gt", "value": 50}])
    assert RuleEvaluator.evaluate(rule, {"guest_count": 80}) is True
    assert RuleEvaluator.evaluate(rule, {"guest_count": 30}) is False


def test_is_weekend_condition():
    rule = make_rule([{"field": "date_weekday", "op": "is_weekend", "value": None}])
    assert RuleEvaluator.evaluate(rule, {"date_weekday": 5}) is True
    assert RuleEvaluator.evaluate(rule, {"date_weekday": 2}) is False


def test_between_condition():
    rule = make_rule([{"field": "guest_count", "op": "between", "value": [50, 100]}])
    assert RuleEvaluator.evaluate(rule, {"guest_count": 80}) is True
    assert RuleEvaluator.evaluate(rule, {"guest_count": 120}) is False


def test_missing_field_in_context_fails():
    rule = make_rule([{"field": "absent", "op": "gt", "value": 1}])
    assert RuleEvaluator.evaluate(rule, {"guest_count": 80}) is False


def test_unknown_operator_fails():
    rule = make_rule([{"field": "guest_count", "op": "bogus", "value": 1}])
    assert RuleEvaluator.evaluate(rule, {"guest_count": 80}) is False


def test_all_conditions_must_match():
    rule = make_rule([
        {"field": "guest_count", "op": "gt", "value": 50},
        {"field": "event_type", "op": "eq", "value": "Hochzeit"},
    ])
    assert RuleEvaluator.evaluate(rule, {"guest_count": 80, "event_type": "Hochzeit"}) is True
    assert RuleEvaluator.evaluate(rule, {"guest_count": 80, "event_type": "Club"}) is False
