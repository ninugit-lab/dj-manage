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
    PricingWorkflow,
)


@pytest.fixture
def event(db):
    return Event.objects.create(
        name="Gig", date=datetime.date(2026, 6, 20), location="X",
        time_start=datetime.time(20, 0), time_end=datetime.time(23, 0),
        guest_count=80, event_type="Hochzeit", distance_km=Decimal("15"),
    )


def make_workflow(blocks, **kwargs):
    return PricingWorkflow.objects.create(name="WF", workflow_json=blocks, **kwargs)


def test_missing_workflow_falls_back_to_calculate(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    r = PriceEngine.calculate_workflow(event, workflow_id=99999, package_id=pkg.pk)
    # fallback returns plain calculate() result (no 'steps' key)
    assert "steps" not in r
    assert r["grand_total"] == Decimal("500")


def test_empty_blocks_falls_back_to_calculate(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    wf = make_workflow([])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk, package_id=pkg.pk)
    assert "steps" not in r
    assert r["grand_total"] == Decimal("500")


def test_package_block(event):
    pkg = PricingPackage.objects.create(name="Basis", base_price=Decimal("500"))
    wf = make_workflow([{"type": "package", "label": "Paket"}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk, package_id=pkg.pk)
    assert r["workflow"] == "WF"
    assert len(r["steps"]) == 1
    assert r["steps"][0]["amount"] == Decimal("500")
    assert r["package"] == {"name": "Basis", "price": Decimal("500")}
    assert r["grand_total"] == Decimal("500")


def test_package_id_from_block_config(event):
    pkg = PricingPackage.objects.create(name="Basis", base_price=Decimal("300"))
    wf = make_workflow([{"type": "package", "config": {"package_id": pkg.pk}}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk)
    assert r["grand_total"] == Decimal("300")


def test_items_block_with_custom(event):
    item = PriceItem.objects.create(name="Licht", category="tech", price=Decimal("100"))
    wf = make_workflow([{"type": "items"}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk,
                                       selected_item_ids=[item.pk],
                                       custom_items=[{"name": "C", "price": "50"}])
    assert r["grand_total"] == Decimal("150")
    assert len(r["custom_items"]) == 1


def test_offer_block(event):
    EventOffer.objects.create(event=event, guest_count=40, threshold=50,
                              rate_1=Decimal("10"), base_rent=Decimal("200"))
    wf = make_workflow([{"type": "offer"}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk)
    assert r["offer_breakdown"]["grand_total"] == 600.0
    assert r["grand_total"] == Decimal("600")


def test_rules_block_percent(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("1000"))
    PricingRule.objects.create(name="WE", condition_json=[], effect_type="percent_add",
                               effect_value=Decimal("10"))
    wf = make_workflow([{"type": "package"}, {"type": "rules"}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk, package_id=pkg.pk)
    assert len(r["rules_applied"]) == 1
    assert r["grand_total"] == Decimal("1100")


def test_rules_block_only_selected(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("1000"))
    r1 = PricingRule.objects.create(name="A", condition_json=[], effect_type="flat_add",
                                    effect_value=Decimal("100"))
    PricingRule.objects.create(name="B", condition_json=[], effect_type="flat_add",
                               effect_value=Decimal("200"))
    wf = make_workflow([{"type": "package"},
                        {"type": "rules", "config": {"rule_ids": [r1.pk]}}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk, package_id=pkg.pk)
    assert len(r["rules_applied"]) == 1
    assert r["grand_total"] == Decimal("1100")


def test_formula_block_uses_running_subtotal(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    formula = PricingFormula.objects.create(name="F", expression="base + guests * 5")
    wf = make_workflow([{"type": "package"}, {"type": "formula"}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk,
                                       package_id=pkg.pk, formula_id=formula.pk)
    # base = 500 (running subtotal), guests 80 -> 500 + 400 = 900
    assert r["formula_result"] == Decimal("900")
    assert r["grand_total"] == Decimal("900")


def test_discount_block_config_percent(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("1000"))
    wf = make_workflow([{"type": "package"},
                        {"type": "discount", "config": {"percent": 20}}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk, package_id=pkg.pk)
    assert r["discount_amount"] == Decimal("200")
    assert r["grand_total"] == Decimal("800")
    assert r["steps"][-1]["amount"] == Decimal("-200")


def test_discount_block_falls_back_to_arg(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("1000"))
    wf = make_workflow([{"type": "package"}, {"type": "discount"}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk,
                                       package_id=pkg.pk, discount_percent=10)
    assert r["grand_total"] == Decimal("900")


def test_unknown_block_type_is_noop(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("500"))
    wf = make_workflow([{"type": "package"}, {"type": "bogus"}])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk, package_id=pkg.pk)
    assert r["steps"][-1]["amount"] == Decimal("0")
    assert r["grand_total"] == Decimal("500")


def test_full_workflow_pipeline(event):
    pkg = PricingPackage.objects.create(name="P", base_price=Decimal("1000"))
    item = PriceItem.objects.create(name="Extra", category="extra", price=Decimal("200"))
    PricingRule.objects.create(name="Flat", condition_json=[], effect_type="flat_add",
                               effect_value=Decimal("100"))
    wf = make_workflow([
        {"type": "package"},
        {"type": "items"},
        {"type": "rules"},
        {"type": "discount", "config": {"percent": 10}},
    ])
    r = PriceEngine.calculate_workflow(event, workflow_id=wf.pk, package_id=pkg.pk,
                                       selected_item_ids=[item.pk])
    # 1000 + 200 + 100 = 1300; -10% = 1170
    assert r["grand_total"] == Decimal("1170")
    assert r["subtotal"] == Decimal("1300")
    assert len(r["steps"]) == 4
