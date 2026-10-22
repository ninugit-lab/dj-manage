"""Pure advanced pricing blocks for the Task 7 dispatcher.

``apply_rules(request, subtotal, prior_lines, rules, *, event_type="")`` and
``apply_formula(request, subtotal, prior_lines, formula)`` accept caller-supplied
snapshots (mappings, not ORM objects) and return immutable BlockResult values.
The caller supplies all preceding lines and the current subtotal; category bases
are derived exclusively from those lines. Neither function performs I/O.
"""
from __future__ import annotations

import ast
import operator
from collections.abc import Mapping
from decimal import Decimal, DecimalException

from .blocks import BlockResult, _decimal
from .input import PricingInput, PricingValidationError
from .result import PricingLine


ZERO = Decimal("0")
ONE = Decimal("1")
OPERATORS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
COMPARISONS = {"eq": operator.eq, "neq": operator.ne, "gt": operator.gt, "gte": operator.ge,
               "lt": operator.lt, "lte": operator.le}


def _error(code, message, field=None):
    raise PricingValidationError(code, message, field)


def _lines(prior_lines):
    if not isinstance(prior_lines, (tuple, list)) or not all(isinstance(line, PricingLine) for line in prior_lines):
        _error("invalid_input", "prior_lines must be PricingLine values", "prior_lines")
    for line in prior_lines:
        _decimal(line.amount, "prior_lines", allow_negative=True)
    return tuple(prior_lines)


def _category_amounts(prior_lines):
    amounts = {name: ZERO for name in ("package", "tech", "extras", "travel")}
    seen = set()
    for line in prior_lines:
        if line.code.startswith("package:") or line.unit == "package":
            category = "package"
        elif line.code == "travel" or line.code.startswith("travel:"):
            category = "travel"
        elif line.basis == "tech":
            category = "tech"
        elif line.basis in ("extra", "extras", "custom item"):
            category = "extras"
        else:
            continue
        seen.add(category)
        amounts[category] += line.amount
    return amounts, seen


def _context(request, event_type):
    if not isinstance(request, PricingInput) or not isinstance(event_type, str):
        _error("invalid_input", "expected normalized request and event type", "request")
    start = request.start_time.hour * 3600 + request.start_time.minute * 60 + request.start_time.second
    end = request.end_time.hour * 3600 + request.end_time.minute * 60 + request.end_time.second
    if end <= start:
        end += 86400
    return {"guest_count": request.guest_count, "duration_hours": Decimal(end - start) / Decimal(3600),
            "date_weekday": request.event_date.weekday(), "event_type": event_type,
            "distance_km": request.distance_km if request.distance_km is not None else ZERO,
            "date_month": request.event_date.month}


def _matches(conditions, context):
    if not isinstance(conditions, (list, tuple)):
        _error("invalid_input", "conditions must be a sequence", "condition_json")
    for condition in conditions:
        if not isinstance(condition, Mapping):
            _error("invalid_input", "condition must be a mapping", "condition_json")
        field, op, expected = condition.get("field"), condition.get("op"), condition.get("value")
        if field not in context or op not in (*COMPARISONS, "in", "between", "is_weekend"):
            _error("reference_error", "unknown condition field or operator", "condition_json")
        actual = context[field]
        try:
            if op == "is_weekend":
                matched = actual in (5, 6)
            elif op in ("in", "between"):
                if not isinstance(expected, (list, tuple)) or (op == "between" and len(expected) != 2):
                    _error("invalid_input", "condition needs a list of values", "condition_json")
                values = [Decimal(str(v)) if isinstance(actual, Decimal) and not isinstance(v, (bool, float)) else v for v in expected]
                matched = (actual in values) if op == "in" else values[0] <= actual <= values[1]
            else:
                if isinstance(actual, Decimal):
                    if isinstance(expected, (bool, float)):
                        _error("invalid_input", "condition value must be exact", "condition_json")
                    expected = Decimal(str(expected))
                matched = COMPARISONS[op](actual, expected)
        except (TypeError, ValueError, DecimalException):
            _error("invalid_input", "invalid condition value", "condition_json")
        if not matched:
            return False
    return True


def apply_rules(request: PricingInput, subtotal: Decimal, prior_lines: tuple[PricingLine, ...],
                rules: tuple[Mapping, ...], *, event_type: str = "") -> BlockResult:
    """Evaluate active rules in sort order; percent bases use trusted preceding lines."""
    current = _decimal(subtotal, "subtotal")
    prior = _lines(prior_lines)
    if not isinstance(rules, (list, tuple)):
        _error("invalid_input", "rules must be snapshot mappings", "rules")
    amounts, seen = _category_amounts(prior)
    context = _context(request, event_type)
    additions = []
    for rule in sorted(rules, key=lambda item: item.get("sort_order", 0) if isinstance(item, Mapping) else 0):
        if not isinstance(rule, Mapping):
            _error("invalid_input", "rule must be a mapping", "rules")
        if rule.get("is_active", True) is False:
            continue
        effect = rule.get("effect_type")
        if effect not in ("percent_add", "flat_add"):
            _error("invalid_input", "unsupported rule effect", "effect_type")
        category = rule.get("applies_to", "subtotal")
        category = "package" if category == "base" else category
        if category not in (*amounts, "subtotal"):
            _error("reference_error", "unknown rule basis", "applies_to")
        if not _matches(rule.get("condition_json") or (), context):
            continue
        if category != "subtotal" and category not in seen:
            _error("reference_error", "rule basis has no preceding lines", "applies_to")
        value = _decimal(rule.get("effect_value"), "effect_value", allow_negative=True)
        basis = current if category == "subtotal" else amounts[category]
        try:
            amount = basis * value / Decimal(100) if effect == "percent_add" else value
            updated = current + amount
        except DecimalException:
            _error("calculation_error", "rule would produce invalid price", "effect_value")
        if not amount.is_finite() or not updated.is_finite() or updated < 0:
            _error("calculation_error", "rule would produce invalid price", "effect_value")
        name = rule.get("name")
        if not isinstance(name, str) or not name:
            _error("invalid_input", "rule requires a name", "rules")
        current = updated
        if amount:
            additions.append(PricingLine(f"rule:{rule.get('id', name)}", name, category, ONE,
                                         "%" if effect == "percent_add" else "EUR", value, amount, "rule"))
    return BlockResult(tuple(additions), current)


def _evaluate(expression, variables):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 500:
        _error("calculation_error", "invalid formula expression", "expression")
    try:
        tree = ast.parse(expression, mode="eval")

        def walk(node):
            if isinstance(node, ast.Name):
                if node.id not in variables:
                    _error("calculation_error", "undefined formula variable", "expression")
                return variables[node.id]
            if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                # AST floats are rounded by Python; use the exact source spelling.
                return Decimal(ast.get_source_segment(expression, node))
            if isinstance(node, ast.BinOp) and type(node.op) in OPERATORS:
                return OPERATORS[type(node.op)](walk(node.left), walk(node.right))
            if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY:
                return UNARY[type(node.op)](walk(node.operand))
            if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in (
                    ast.Eq, ast.NotEq, ast.Gt, ast.GtE, ast.Lt, ast.LtE):
                comparison = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Gt: operator.gt,
                              ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le}
                return comparison[type(node.ops[0])](walk(node.left), walk(node.comparators[0]))
            if isinstance(node, ast.IfExp):
                return walk(node.body if walk(node.test) else node.orelse)
            _error("calculation_error", "unsupported formula syntax", "expression")

        result = walk(tree.body)
        if not result.is_finite() or result < 0:
            _error("calculation_error", "formula must produce a finite nonnegative price", "expression")
        return result
    except (SyntaxError, DecimalException, OverflowError, ValueError) as exc:
        if isinstance(exc, PricingValidationError):
            raise
        _error("calculation_error", "formula could not be calculated", "expression")


def apply_formula(request: PricingInput, subtotal: Decimal, prior_lines: tuple[PricingLine, ...],
                  formula: Mapping) -> BlockResult:
    """Apply add/subtract/replace to current subtotal; replace emits its difference."""
    base = _decimal(subtotal, "subtotal")
    amounts, _ = _category_amounts(_lines(prior_lines))
    if not isinstance(formula, Mapping):
        _error("invalid_input", "formula must be a snapshot mapping", "formula")
    if formula.get("is_active", True) is False:
        return BlockResult((), base)
    mode = formula.get("mode", "add")
    if mode not in ("add", "subtract", "replace"):
        _error("invalid_input", "unsupported formula mode", "mode")
    context = _context(request, "")
    variables = {"base": base, "guests": Decimal(context["guest_count"]),
                 "hours": context["duration_hours"], "distance": context["distance_km"],
                 "items_total": amounts["tech"] + amounts["extras"], "package_price": amounts["package"],
                 "subtotal": base, "package": amounts["package"], "tech": amounts["tech"],
                 "extras": amounts["extras"], "travel": amounts["travel"]}
    value = _evaluate(formula.get("expression"), variables)
    delta = value if mode == "add" else -value if mode == "subtract" else value - base
    try:
        total = base + delta
    except DecimalException:
        _error("calculation_error", "formula would produce invalid price", "expression")
    if not total.is_finite() or total < 0:
        _error("calculation_error", "formula would produce invalid price", "expression")
    name = formula.get("name")
    if not isinstance(name, str) or not name:
        _error("invalid_input", "formula requires a name", "formula")
    if delta == 0:
        return BlockResult((), total)
    line = PricingLine(f"formula:{formula.get('id', name)}", name, mode, ONE, "EUR", value, delta, "formula")
    return BlockResult((line,), total)
