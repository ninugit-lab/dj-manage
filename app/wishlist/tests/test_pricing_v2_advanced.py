from datetime import date, time
from decimal import Decimal

import pytest

from wishlist.pricing_v2.advanced import apply_formula, apply_rules
from wishlist.pricing_v2.blocks import BlockResult
from wishlist.pricing_v2.input import PricingInput, PricingValidationError
from wishlist.pricing_v2.result import PricingLine


def request():
    return PricingInput(event_date=date(2026, 10, 24), start_time=time(18), end_time=time(22),
                        package_id="party", guest_count=80, distance_km=Decimal("15"))


def lines():
    return (
        PricingLine("package:party", "Party", "package base", 1, "package", Decimal("100"), Decimal("100"), "catalog"),
        PricingLine("item:light", "Light", "tech", 1, "item", Decimal("40"), Decimal("40"), "catalog"),
        PricingLine("item:smoke", "Smoke", "extra", 1, "item", Decimal("20"), Decimal("20"), "catalog"),
        PricingLine("travel", "Travel", "distance", 1, "km", Decimal("10"), Decimal("10"), "config"),
    )


@pytest.mark.parametrize("basis,amount", [("package", "10"), ("base", "10"), ("tech", "4"),
                                         ("extras", "2"), ("travel", "1"), ("subtotal", "17")])
def test_scoped_rule_uses_prior_lines_not_rule_supplied_basis(basis, amount):
    result = apply_rules(request(), Decimal("170"), lines(), ({"id": 1, "name": "Surcharge",
        "applies_to": basis, "effect_type": "percent_add", "effect_value": "10",
        "condition_json": [], "is_active": True, "sort_order": 0},))
    assert isinstance(result, BlockResult)
    assert result.lines[0].amount == Decimal(amount)
    assert result.subtotal == Decimal("170") + Decimal(amount)


def test_rule_conditions_combine_guest_time_and_event_type():
    rule = {"id": 2, "name": "Weekend wedding", "effect_type": "flat_add", "effect_value": "7",
            "applies_to": "subtotal", "condition_json": [
                {"field": "guest_count", "op": "gte", "value": 70},
                {"field": "date_weekday", "op": "is_weekend", "value": True},
                {"field": "event_type", "op": "eq", "value": "wedding"}], "is_active": True}
    assert apply_rules(request(), Decimal("170"), lines(), (rule,), event_type="wedding").subtotal == Decimal("177")
    assert apply_rules(request(), Decimal("170"), lines(), (rule,), event_type="club").lines == ()


def test_rule_sequence_orders_deterministically_and_rebases_only_subtotal_percentage():
    rules = (
        {"id": "scoped", "name": "Tech surcharge", "applies_to": "tech", "effect_type": "percent_add",
         "effect_value": "10", "condition_json": [], "is_active": True, "sort_order": 10},
        {"id": "subtotal", "name": "Subtotal surcharge", "applies_to": "subtotal", "effect_type": "percent_add",
         "effect_value": "10", "condition_json": [], "is_active": True, "sort_order": 20},
    )
    result = apply_rules(request(), Decimal("170"), lines(), rules)
    assert [(line.code, line.amount) for line in result.lines] == [
        ("rule:scoped", Decimal("4")), ("rule:subtotal", Decimal("17.4"))]
    assert result.subtotal == Decimal("191.4")


def test_division_by_zero_formula_has_calculation_error_code():
    with pytest.raises(PricingValidationError) as exc:
        apply_formula(request(), Decimal("170"), lines(),
                      {"id": 3, "name": "Divide", "expression": "1 / 0", "mode": "add"})
    assert exc.value.code == "calculation_error"


@pytest.mark.parametrize("mode,amount,total", [("add", "0.3", "170.3"),
                                                ("subtract", "-0.3", "169.7"),
                                                ("replace", "-169.7", "0.3")])
def test_formula_modes_preserve_literal_decimal_precision(mode, amount, total):
    result = apply_formula(request(), Decimal("170"), lines(),
                           {"id": 3, "name": "Formula", "expression": "0.1 + 0.2", "mode": mode, "is_active": True})
    assert isinstance(result, BlockResult)
    assert result.lines[0].amount == Decimal(amount)
    assert result.subtotal == Decimal(total)


@pytest.mark.parametrize("expression", ["missing + 1", "1 / 0", "__import__('os')", "base.real",
                                        "base[0]", "[x for x in (1,)]", "1e9999999999", "-1"])
def test_invalid_formula_raises_calculation_error(expression):
    with pytest.raises(PricingValidationError) as exc:
        apply_formula(request(), Decimal("170"), lines(), {"id": 3, "name": "Bad", "expression": expression, "mode": "add"})
    assert exc.value.code == "calculation_error"


@pytest.mark.parametrize("mode,expression", [("multiply", "1"), ("subtract", "171")])
def test_invalid_mode_or_subtraction_below_zero(mode, expression):
    with pytest.raises(PricingValidationError):
        apply_formula(request(), Decimal("170"), lines(), {"id": 3, "name": "Bad", "expression": expression, "mode": mode})


@pytest.mark.parametrize("rule", [
    {"applies_to": "subtotal", "effect_type": "flat_set", "effect_value": "5"},
    {"applies_to": "missing", "effect_type": "flat_add", "effect_value": "5"},
    {"applies_to": "subtotal", "effect_type": "flat_add", "effect_value": "-171"},
    {"applies_to": "subtotal", "effect_type": "percent_add", "effect_value": "NaN"},
])
def test_invalid_rules_fail_closed(rule):
    with pytest.raises(PricingValidationError):
        apply_rules(request(), Decimal("170"), lines(), ({"id": 5, "name": "Bad", **rule},))


def test_formula_uses_trusted_category_totals_and_normalized_input():
    result = apply_formula(request(), Decimal("170"), lines(), {"id": 4, "name": "Based",
        "expression": "package_price + items_total + guests + hours + distance + base", "mode": "add"})
    assert result.lines[0].amount == Decimal("429")
    assert result.subtotal == Decimal("599")


def test_rule_rejects_missing_category_and_does_not_modify_inputs():
    originals = lines()
    with pytest.raises(PricingValidationError) as exc:
        apply_rules(request(), Decimal("100"), originals[:1], ({"id": 1, "name": "Bad", "effect_type": "percent_add",
            "effect_value": "10", "applies_to": "tech"},))
    assert exc.value.code == "reference_error"
    assert originals == lines()


def test_conditional_formula_uses_decimal_comparison_and_only_selected_branch():
    formula = {"id": "conditional", "name": "Conditional", "mode": "add",
               "expression": "0.1 + 0.2 if guests >= 70 else 1 / 0"}
    result = apply_formula(request(), Decimal("170"), lines(), formula)
    assert result.subtotal == Decimal("170.3")


@pytest.mark.parametrize("expression", ["1 if base.real else 2", "1 if guests[0] else 2",
                                        "1 if __import__('os') else 2"])
def test_conditional_formula_does_not_expand_ast_permissions(expression):
    with pytest.raises(PricingValidationError) as exc:
        apply_formula(request(), Decimal("170"), lines(), {"name": "Bad", "expression": expression})
    assert exc.value.code == "calculation_error"
