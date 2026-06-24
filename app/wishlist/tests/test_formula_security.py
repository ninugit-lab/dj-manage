"""Security-Tests fuer SafeFormulaEvaluator (Sandbox-Escape + DoS).

User koennen ueber den Workflow-Builder beliebige Formel-Ausdruecke
anlegen, die serverseitig ausgewertet werden. Diese Tests stellen sicher,
dass die AST-Whitelist gefaehrliche Konstrukte abweist.
"""
import time

import pytest

from wishlist.price_engine import SafeFormulaEvaluator, FORMULA_VARS

VARS = {v: 1 for v in FORMULA_VARS}


@pytest.fixture
def ev():
    return SafeFormulaEvaluator(FORMULA_VARS)


# --- Sandbox-Escape: muss alles mit ValueError abgewiesen werden ---
@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')",          # Funktionsaufruf
    "().__class__.__bases__[0].__subclasses__()",  # Attribut/Subscript-Kette
    "base.__class__",                          # Attribut-Zugriff
    "open('/etc/passwd').read()",              # I/O via Call
    "eval('1+1')",                             # eval
    "exec('x=1')",                             # exec
    "[x for x in range(10)]",                  # Comprehension
    "{1: 2}",                                  # Dict-Literal
    "lambda: 1",                               # Lambda
    "base[0]",                                 # Subscript
    "globals()",                               # builtins
    "guests if base else __import__('os')",    # Escape im else-Zweig
    "base ** 2",                               # nicht-gewhitelisteter Operator (Pow)
    "base & 1",                                # BitAnd
    "base << 2",                               # LShift
    "'string'",                                # String-Konstante
    "True and base",                           # BoolOp
])
def test_escape_attempts_rejected(ev, expr):
    with pytest.raises(ValueError):
        ev.evaluate(expr, VARS)


def test_unknown_variable_rejected(ev):
    with pytest.raises(ValueError):
        ev.evaluate("secret + 1", VARS)


def test_syntax_error_not_swallowed(ev):
    with pytest.raises((ValueError, SyntaxError)):
        ev.evaluate("base +", VARS)


# --- DoS-Schutz ---
def test_overlong_expression_rejected(ev):
    with pytest.raises(ValueError):
        ev.evaluate("1+" * 300 + "1", VARS)  # > MAX_LEN


def test_deep_nesting_no_hang(ev):
    # Tiefe arithmetische Verschachtelung innerhalb der Laengengrenze
    # darf nicht haengen / muss schnell terminieren.
    expr = "(" * 60 + "base" + "+1)" * 60
    if len(expr) <= ev.MAX_LEN:
        start = time.monotonic()
        try:
            ev.evaluate(expr, VARS)
        except (ValueError, RecursionError):
            pass
        assert time.monotonic() - start < 2.0


# --- Positiv: legitime Formeln funktionieren weiterhin ---
def test_valid_arithmetic(ev):
    assert ev.evaluate("base + guests * 5", {"base": 100, "guests": 10}) == 150


def test_valid_conditional(ev):
    assert ev.evaluate("base if guests > 5 else 0", {"base": 200, "guests": 10}) == 200
