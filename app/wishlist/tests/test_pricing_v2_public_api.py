"""Public estimate contracts through the HTTP boundary."""
import json
from decimal import Decimal

import pytest
from django.utils import timezone
from unittest.mock import patch

from wishlist.models import AppConfig, PriceItem, PricingPackage, PricingProfile, PricingWorkflow


@pytest.fixture
def catalog(db):
    package = PricingPackage.objects.create(name="Party", base_price=Decimal("100"))
    required = PriceItem.objects.create(name="Required", price=Decimal("20"), is_required=True)
    extra = PriceItem.objects.create(name="Extra", price=Decimal("30"))
    private = PriceItem.objects.create(name="Private", price=Decimal("999"), is_public=False)
    PricingWorkflow.objects.create(name="Default", is_default=True, workflow_json=[
        {"type": "package", "config": {"package_id": package.pk}}, {"type": "items"}])
    PricingProfile.objects.create(name="Live", status="active", valid_from=timezone.now(), config_json=dict(
        included_hours="3", extra_hour_rate="0", setup_minutes=0, teardown_minutes=0,
        setup_teardown_hour_rate="0", included_km="0", km_rate="0", saturday_percent="0",
        sunday_percent="0", holiday_percent="0", holiday_region="BY", seasons=[],
        lead_time_tiers=[], guest_tiers=[{"lower_bound": 0, "upper_bound": None, "value": "0"}],
        minimum_price="0", rounding_mode="nearest", rounding_step="0.01"))
    return package, required, extra, private


def estimate(client, catalog, **changes):
    body = dict(date="2026-12-02", time_start="18:00", time_end="21:00",
                package_id=catalog[0].pk, selected_item_ids=[catalog[2].pk], distance_km="12")
    body.update(changes)
    return client.post("/api/price-estimate/", data=json.dumps(body), content_type="application/json")


@pytest.mark.parametrize("mode,version", [("legacy", "1"), ("shadow", "1"), ("v2", "2")])
def test_mode_preserves_gross_arithmetic_and_decimal_contract(client, catalog, mode, version):
    AppConfig.objects.create(pricing_engine_mode=mode)
    response = estimate(client, catalog, grand_total="0", price="0", selected_item_ids=[catalog[2].pk])
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["engine_version"] == version
    assert data["status"] == "ok"
    assert data["gross_total"] == "150.00"
    assert data["grand_total"] == 150.0  # pre-cutover response compatibility
    assert data["warnings"] == []
    assert data["lines"] and all(isinstance(line["amount"], str) and isinstance(line["unit_price"], str)
                                 for line in data["lines"])
    assert {line["label"] for line in data["lines"]} >= {"Party", "Required", "Extra"}


def test_public_rejects_private_items_without_leaking_diagnostics(client, catalog):
    AppConfig.objects.create(pricing_engine_mode="v2")
    response = estimate(client, catalog, selected_item_ids=[catalog[3].pk])
    assert response.status_code == 400
    data = response.json()
    assert data["ok"] is False
    assert data["status"] == "individual_review"
    assert data["message"] == "Preis wird individuell geprüft"
    assert data["gross_total"] is None
    assert "private" not in response.content.decode().lower()
    assert "traceback" not in response.content.decode().lower()


@pytest.mark.parametrize("mode", ["legacy", "v2"])
def test_missing_distance_never_displays_a_fabricated_final_quote(client, catalog, mode):
    AppConfig.objects.create(pricing_engine_mode=mode)
    data = estimate(client, catalog, distance_km=None).json()
    assert data["ok"] is True
    assert data["status"] == "individual_review"
    assert data["warnings"] and "Entfernung" in " ".join(data["warnings"])
    assert data["gross_total"] is None
    assert data["grand_total"] is None


def test_malformed_request_returns_public_safe_error(client, catalog):
    response = client.post("/api/price-estimate/", data="{broken", content_type="application/json")
    assert response.status_code == 400
    assert response.json()["message"] == "Preis wird individuell geprüft"


def test_calculation_failure_does_not_leak_internal_error(client, catalog):
    with patch('wishlist.pricing_v2.services.calculate_with_mode', side_effect=RuntimeError('secret traceback')):
        response = estimate(client, catalog)
    assert response.status_code == 503
    assert response.json()['message'] == 'Preis wird individuell geprüft'
    assert 'secret' not in response.content.decode()


def test_private_package_does_not_fall_back_to_another_public_price(client, catalog):
    catalog[0].is_public = False
    catalog[0].save(update_fields=['is_public'])
    response = estimate(client, catalog)
    assert response.status_code == 400
    assert response.json()['gross_total'] is None


@pytest.mark.parametrize('mode', ['legacy', 'shadow', 'v2'])
def test_browser_cannot_submit_admin_only_adjustments(client, catalog, mode):
    AppConfig.objects.create(pricing_engine_mode=mode)
    response = estimate(client, catalog, custom_items=[{'name': 'secret', 'price': '900'}],
                        discount_percent='99', formula_id=1, workflow_id=999)
    assert response.status_code == 200
    assert response.json()['gross_total'] == '150.00'
