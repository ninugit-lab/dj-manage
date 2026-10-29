"""Small explicit whitelist; no Python builtins or attribute access are exposed."""
from decimal import ROUND_CEILING


def ceil(value):
    return value.to_integral_value(rounding=ROUND_CEILING)


FUNCTIONS = {'ceil': (ceil, 1, 1), 'max': (max, 2, 8)}
