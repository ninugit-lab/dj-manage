import datetime
from decimal import Decimal

import pytest

from wishlist.price_engine import PriceEngine
from wishlist.models import (
    Event,
    EventOffer,
    PriceItem,
    PricingPackage,
    PricingRule,
    PricingFormula,
)


@pytest.fixture
def event(db):
    return Event.objects.create(
        name="Gig", date=datetime.date(2026, 6, 20), location="X",
        time_start=datetime.time(20, 0), time_end=datetime.time(23, 0),
        guest_count=80, event_type="Hochzeit", distance_km=Decimal("15"),
    )


def test_empty_calculation_is_zero(event):
    r = PriceEngine.calculate(event)
    assert r["subtotal"] == Decimal("0")
    assert r["grand_total"] == Decimal("0")


def test_package_price_added(event):
    pkg = PricingPackage.objects.create(name="Basis", base_price=Decimal("500"))
    r = PriceEngine.calculate(event, package_id=pkg.pk)
    assert r["package"] == {"name": "Basis", "price": Decimal("500")}
    assert r["grand_total"] == Decimal("500")


def test_selected_items_added(event):
    i1 = PriceItem.objects.create(name="Licht", category="tech", price=Decimal("100"))
    i2 = PriceItem.objects.create(name="Nebel", category="tech", price=Decimal("50"))
    r = PriceEngine.calculate(event, selected_item_ids=[i1.pk, i2.pk])
    assert len(r["items"]) == 2
    assert r["grand_total"] == Decimal("150")


def test_package_included_items_not_double_counted(event):
    included = PriceItem.objects.create(name="Boxen", category="tech", price=Decimal("100"))
    extra = PriceItem.objects.create(name="Funk", category="tech", price=Decimal("40"))
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    pkg.included_items.add(included)
    r = PriceEngine.calculate(event, package_id=pkg.pk,
                              selected_item_ids=[included.pk, extra.pk])
    # included is in package -> not added again; only extra counts
    assert len(r["items"]) == 1
    assert r["grand_total"] == Decimal("540")


def test_custom_items_added(event):
    r = PriceEngine.calculate(event, custom_items=[
        {"name": "Sonderwunsch", "price": "75.50"},
        {"name": "Bad", "price": "nope"},  # invalid, skipped
    ])
    assert len(r["custom_items"]) == 1
    assert r["grand_total"] == Decimal("75.50")


def test_inactive_items_ignored(event):
    active = PriceItem.objects.create(name="A", category="tech", price=Decimal("100"))
    inactive = PriceItem.objects.create(name="B", category="tech", price=Decimal("999"), is_active=False)
    r = PriceEngine.calculate(event, selected_item_ids=[active.pk, inactive.pk])
    assert len(r["items"]) == 1
    assert r["grand_total"] == Decimal("100")


def test_event_offer_breakdown_included(event):
    EventOffer.objects.create(event=event, guest_count=40, threshold=50,
                              rate_1=Decimal("10"), base_rent=Decimal("200"))
    r = PriceEngine.calculate(event)
    assert r["offer_breakdown"]["grand_total"] == 600.0  # 40*10 + 200
    assert r["grand_total"] == Decimal("600")


def test_percent_add_rule(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("1000"))
    PricingRule.objects.create(name="Wochenend-Zuschlag",
                               condition_json=[{"field": "date_weekday", "op": "is_weekend", "value": None}],
                               effect_type="percent_add", effect_value=Decimal("10"))
    r = PriceEngine.calculate(event, package_id=pkg.pk)
    assert len(r["rules_applied"]) == 1
    assert r["rules_applied"][0]["amount"] == Decimal("100")
    assert r["grand_total"] == Decimal("1100")


def test_flat_add_rule(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    PricingRule.objects.create(name="Anfahrt", condition_json=[],
                               effect_type="flat_add", effect_value=Decimal("80"))
    r = PriceEngine.calculate(event, package_id=pkg.pk)
    assert r["grand_total"] == Decimal("580")


def test_flat_set_rule(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    PricingRule.objects.create(name="Festpreis", condition_json=[],
                               effect_type="flat_set", effect_value=Decimal("999"))
    r = PriceEngine.calculate(event, package_id=pkg.pk)
    assert r["grand_total"] == Decimal("999")


def test_non_matching_rule_skipped(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    PricingRule.objects.create(name="GrosseEvents",
                               condition_json=[{"field": "guest_count", "op": "gt", "value": 200}],
                               effect_type="flat_add", effect_value=Decimal("100"))
    r = PriceEngine.calculate(event, package_id=pkg.pk)
    assert r["rules_applied"] == []
    assert r["grand_total"] == Decimal("500")


def test_discount_applied(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("1000"))
    r = PriceEngine.calculate(event, package_id=pkg.pk, discount_percent=20)
    assert r["discount_amount"] == Decimal("200")
    assert r["grand_total"] == Decimal("800")


def test_formula_overrides_subtotal(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    formula = PricingFormula.objects.create(name="F", expression="base + guests * 5")
    r = PriceEngine.calculate(event, package_id=pkg.pk, formula_id=formula.pk)
    # base = pre_rules_subtotal (500), guests = 80 -> 500 + 400 = 900
    assert r["formula_result"] == Decimal("900")
    assert r["grand_total"] == Decimal("900")


def test_full_combination(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("1000"))
    item = PriceItem.objects.create(name="Extra", category="extra", price=Decimal("200"))
    PricingRule.objects.create(name="WE", condition_json=[], effect_type="flat_add",
                               effect_value=Decimal("100"))
    r = PriceEngine.calculate(event, package_id=pkg.pk, selected_item_ids=[item.pk],
                              custom_items=[{"name": "C", "price": "50"}], discount_percent=10)
    # 1000 + 200 + 50 = 1250 pre-rules; + 100 flat = 1350; -10% = 1215
    assert r["subtotal"] == Decimal("1350")
    assert r["grand_total"] == Decimal("1215")
