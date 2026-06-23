from decimal import Decimal

import pytest

from wishlist.price_engine import SafeFormulaEvaluator, FORMULA_VARS


@pytest.fixture
def ev():
    return SafeFormulaEvaluator(FORMULA_VARS)


def test_basic_arithmetic_returns_decimal(ev):
    result = ev.evaluate("base + 10", {"base": 5})
    assert result == Decimal("15")
    assert isinstance(result, Decimal)


def test_multiplication_and_precedence(ev):
    assert ev.evaluate("base + guests * 2", {"base": 10, "guests": 3}) == Decimal("16")


def test_unknown_variable_raises(ev):
    with pytest.raises(ValueError, match="Unbekannte Variable"):
        ev.evaluate("nonsense + 1", {})


def test_division_by_zero_raises(ev):
    with pytest.raises(ValueError, match="Division durch Null"):
        ev.evaluate("base / 0", {"base": 10})


def test_ternary_if_expression(ev):
    expr = "base if guests > 50 else 0"
    assert ev.evaluate(expr, {"base": 100, "guests": 80}) == Decimal("100")
    assert ev.evaluate(expr, {"base": 100, "guests": 20}) == Decimal("0")


def test_comparison_returns_boolean_decimal(ev):
    assert ev.evaluate("guests > 50", {"guests": 80}) == Decimal("1")
    assert ev.evaluate("guests > 50", {"guests": 20}) == Decimal("0")


def test_too_long_formula_raises(ev):
    with pytest.raises(ValueError, match="zu lang"):
        ev.evaluate("1+" * 300 + "1", {})


def test_disallowed_operator_raises(ev):
    with pytest.raises(ValueError):
        ev.evaluate("base ** 2", {"base": 3})


def test_missing_variable_value_defaults_to_zero(ev):
    assert ev.evaluate("base + 1", {}) == Decimal("1")
