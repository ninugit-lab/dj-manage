"""Historical validator retained only for migration 0025; not a pricing engine."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError


BASIS_CONFIG = {
    'guest_threshold': 51, 'guest_surcharge': '90', 'without_sound_discount': '80',
    'included_minutes': 360, 'extra_hour_rate': '60',
    'included_round_trip_km': '100', 'km_rate': '0.30',
}


def validate_package_pricing(value):
    if not isinstance(value, dict):
        raise ValidationError('Paketregeln müssen ein JSON-Objekt sein.')
    if not value:
        return
    if set(value) != set(BASIS_CONFIG):
        raise ValidationError('Paketregeln müssen alle vorgesehenen Faktoren enthalten.')
    for key, raw in value.items():
        if key in ('guest_threshold', 'included_minutes'):
            if type(raw) is not int or raw < 0:
                raise ValidationError(f'{key}: nichtnegative ganze Zahl erforderlich.')
        else:
            try:
                amount = Decimal(str(raw))
            except (InvalidOperation, ValueError):
                raise ValidationError(f'{key}: ungültiger Betrag.')
            if isinstance(raw, bool) or not amount.is_finite() or amount < 0:
                raise ValidationError(f'{key}: nichtnegativer, endlicher Betrag erforderlich.')
