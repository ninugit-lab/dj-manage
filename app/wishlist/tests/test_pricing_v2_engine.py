import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from wishlist.pricing_v2.config import resolve_pricing_config
from wishlist.pricing_v2.input import PricingInput, PricingValidationError
from wishlist.pricing_v2.workflow import DEFAULT_BLOCKS, ValidatedWorkflow, validate_workflow
from wishlist.pricing_v2.engine import PricingEngineV2


def request(**changes):
    data = dict(event_date=date(2026, 1, 3), start_time=time(23), end_time=time(2),
                package_id="party", selected_item_ids=("included", "lights"), guest_count=50,
                distance_km=Decimal("25"), requested_at=datetime(2025, 12, 1, tzinfo=timezone.utc))
    data.update(changes)
    return PricingInput(**data)


def config(**changes):
    data = dict(included_hours="3", extra_hour_rate="100", setup_minutes=30,
                teardown_minutes=30, setup_teardown_hour_rate="60", included_km="25",
                km_rate="1", saturday_percent="0", sunday_percent="0", holiday_percent="0",
                holiday_region="NW", seasons=[], lead_time_tiers=[], guest_tiers=[
                    dict(lower_bound=0, upper_bound=49, value="0"),
                    dict(lower_bound=50, upper_bound=None, value="10")], minimum_price="0",
                rounding_mode="nearest", rounding_step="0.01")
    data.update(changes)
    return resolve_pricing_config(SimpleNamespace(id="profile", version=2, config_json=data),
                                  SimpleNamespace(id="party", version=3, overrides_json={}))


def catalog(**changes):
    data = dict(profile_id="profile", packages={"party": dict(label="Party", base_price="100", version=3,
                                        included_item_ids=("included",), required_item_ids=("required",))},
                items={"included": dict(label="Included", price="20", active=True),
                       "lights": dict(label="Lights", price="30", active=True, category="tech"),
                       "required": dict(label="Required", price="10", active=True)},
                rules={}, formulas={})
    data.update(changes)
    return data


def run(req=None, cfg=None, blocks=None, stock=None):
    stock = catalog() if stock is None else stock
    workflow = validate_workflow(DEFAULT_BLOCKS if blocks is None else blocks, stock)
    return PricingEngineV2.calculate(req or request(), cfg or config(), workflow, stock)


def test_default_pipeline_charges_required_once_and_respects_inclusive_boundaries():
    result = run()
    assert len(DEFAULT_BLOCKS) == 12
    assert [line.code for line in result.lines] == ["package:party", "item:lights", "item:required",
                                                     "setup_teardown", "guest_tier:50"]
    assert result.gross_total == Decimal("220.00")
    assert result.discount_total == Decimal("0")
    assert [block["type"] for block in result.block_subtotals] == [b["type"] for b in DEFAULT_BLOCKS]
    assert result.inputs["package_id"] == "party"
    assert result.inputs["profile_id"] == "profile"
    assert result.inputs["workflow_id"] is None
    assert (result.profile_version, result.package_version) == (2, 3)
    assert "vat" not in result.to_dict()


def test_boundary_overnight_one_minute_extra_and_explicit_zero_setup():
    result = run(req=request(end_time=time(2, 1), setup_minutes=0, teardown_minutes=0,
                             distance_km=Decimal("25.5"), guest_count=49),
                 cfg=config(guest_tiers=[dict(lower_bound=0, upper_bound=None, value="0")]))
    assert [(line.code, line.amount) for line in result.lines] == [
        ("package:party", Decimal("100")), ("item:lights", Decimal("30")),
        ("item:required", Decimal("10")), ("duration:extra", Decimal("100")),
        ("travel", Decimal("0.5"))]
    assert result.gross_total == Decimal("240.50")


@pytest.mark.parametrize("blocks,code", [
    ([{"type": "alien"}, {"type": "package"}], "invalid_input"),
    ([{"type": "package"}, {"type": "package"}], "invalid_input"),
    ([{"type": "rounding"}, {"type": "package"}], "invalid_input"),
    ([{"type": "discount"}, {"type": "rounding"}], "invalid_input"),
    ([{"type": "items"}, {"type": "package"}], "invalid_input"),
    ([{"type": "package", "config": []}], "invalid_input"),
    ([{"type": "package", "rule_ids": ["missing"]}], "invalid_input"),
])
def test_workflow_rejects_invalid_shape_and_order(blocks, code):
    with pytest.raises(PricingValidationError) as exc:
        validate_workflow(blocks, catalog())
    assert exc.value.code == code


@pytest.mark.parametrize("blocks,stock", [
    ([{"type": "package"}, {"type": "rules", "rule_ids": ["missing"]}], catalog()),
    ([{"type": "package"}, {"type": "formula", "formula_id": "missing"}], catalog()),
    ([{"type": "package", "package_id": "missing"}], catalog()),
    ([{"type": "package"}, {"type": "items", "item_ids": ["missing"]}], catalog()),
])
def test_workflow_rejects_unresolved_references(blocks, stock):
    with pytest.raises(PricingValidationError) as exc:
        validate_workflow(blocks, stock)
    assert exc.value.code == "reference_error"


def test_discount_and_minimum_placement_changes_gross():
    before = [dict(type=name) for name in ("package", "items", "minimum_price", "discount", "rounding")]
    after = [dict(type=name) for name in ("package", "items", "discount", "minimum_price", "rounding")]
    req = request(discount_percent=Decimal("50"))
    assert run(req=req, cfg=config(minimum_price="160"), blocks=before).gross_total == Decimal("80.00")
    assert run(req=req, cfg=config(minimum_price="160"), blocks=after).gross_total == Decimal("160.00")


def test_nrw_holiday_factor_can_be_disabled_and_json_is_reproducible():
    req = request(event_date=date(2026, 5, 1))
    cfg = config(holiday_percent="20")
    first = run(req=req, cfg=cfg)
    second = run(req=req, cfg=cfg)
    assert first.gross_total == Decimal("264.00")
    assert "calendar:holiday" in [line.code for line in first.lines]
    assert json.dumps(first.to_dict(), sort_keys=True) == json.dumps(second.to_dict(), sort_keys=True)
    assert isinstance(first.to_dict()["gross_total"], str)
    disabled = resolve_pricing_config(SimpleNamespace(id="profile", version=2, config_json={
        **{key: getattr(cfg.normalized, key) for key in cfg.normalized.__dataclass_fields__},
        "seasons": [], "lead_time_tiers": [], "guest_tiers": [dict(lower_bound=0, upper_bound=49, value="0"), dict(lower_bound=50, upper_bound=None, value="10")]}),
        SimpleNamespace(id="party", version=3, overrides_json={"holiday_percent": {"mode": "disabled"}}))
    assert run(req=req, cfg=disabled).gross_total == Decimal("220.00")


def test_rules_and_formula_each_apply_once_with_explicit_mode():
    stock = catalog(rules={"bonus": dict(id="bonus", name="Bonus", applies_to="subtotal",
                                              effect_type="flat_add", effect_value="5", condition_json=[])},
                    formulas={"calc": dict(id="calc", name="Calc", mode="replace", expression="base + 2")})
    blocks = [dict(type="package"), dict(type="items"), dict(type="rules", rule_ids=["bonus"]),
              dict(type="formula", formula_id="calc"), dict(type="rounding")]
    result = run(req=request(selected_item_ids=()), blocks=blocks, stock=stock)
    assert [line.code for line in result.lines] == ["package:party", "item:required", "rule:bonus", "formula:calc"]
    assert result.gross_total == Decimal("117.00")


@pytest.mark.parametrize("stock,blocks", [
    (catalog(formulas={"calc": dict(id="calc", name="Bad", mode="add", expression="unknown + 1")}),
     [dict(type="package"), dict(type="items"), dict(type="formula", formula_id="calc")]),
    (catalog(rules={"bad": dict(id="bad", name="Bad", effect_type="nonsense", effect_value="5")}),
     [dict(type="package"), dict(type="items"), dict(type="rules", rule_ids=["bad"])]),
])
def test_invalid_advanced_expressions_and_effects_are_typed(stock, blocks):
    with pytest.raises(PricingValidationError) as exc:
        run(stock=stock, blocks=blocks)
    assert exc.value.code in {"invalid_input", "calculation_error"}


def test_missing_request_and_malformed_catalog_fail_closed():
    with pytest.raises(PricingValidationError) as exc:
        PricingEngineV2.calculate(None, config(), DEFAULT_BLOCKS, catalog())
    assert exc.value.code == "invalid_input"
    with pytest.raises(PricingValidationError) as exc:
        run(stock=catalog(items={"included": {}, "required": {}}))
    assert exc.value.code == "reference_error"


def test_workflow_selected_package_must_match_request_not_silently_price_another():
    stock = catalog(packages={**catalog()["packages"], "other": dict(label="Other", base_price="900")})
    with pytest.raises(PricingValidationError) as exc:
        run(blocks=[dict(type="package", package_id="other"), dict(type="items")], stock=stock)
    assert exc.value.code == "reference_error"


@pytest.mark.parametrize("stock", [
    catalog(profile_id=[]),
    catalog(packages={"party": dict(label="Party", base_price="100", version=[],
                                     included_item_ids=(), required_item_ids=())}),
    catalog(rules=[]),
])
def test_malformed_snapshot_metadata_and_catalog_have_typed_errors(stock):
    with pytest.raises(PricingValidationError) as exc:
        run(stock=stock)
    assert exc.value.code in {"invalid_input", "reference_error"}


def test_rounding_is_last_even_for_equal_but_distinct_block_mappings():
    with pytest.raises(PricingValidationError) as exc:
        validate_workflow([dict(type="package"), dict(type="rounding"), dict(type="discount")], catalog())
    assert exc.value.code == "invalid_input"


def test_malformed_advanced_rule_sort_order_is_typed_not_raw_type_error():
    stock = catalog(rules={"one": dict(id="one", name="One", effect_type="flat_add", effect_value="1",
                                          sort_order=[]),
                           "two": dict(id="two", name="Two", effect_type="flat_add", effect_value="1",
                                          sort_order=0)})
    with pytest.raises(PricingValidationError) as exc:
        run(stock=stock, blocks=[dict(type="package"), dict(type="items"), dict(type="rules", rule_ids=["one", "two"])])
    assert exc.value.code == "invalid_input"


def test_invalid_frozen_workflow_version_is_rejected_before_result_serialization():
    workflow = ValidatedWorkflow(validate_workflow(DEFAULT_BLOCKS, catalog()).blocks, "flow", [])
    with pytest.raises(PricingValidationError) as exc:
        PricingEngineV2.calculate(request(), config(), workflow, catalog())
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize("blocks", [
    [dict(type="package"), dict(type="rounding")],
    [dict(type="package"), dict(type="discount"), dict(type="rounding")],
])
def test_package_without_items_block_must_not_drop_required_extras(blocks):
    with pytest.raises(PricingValidationError) as exc:
        validate_workflow(blocks, catalog())
    assert exc.value.code == "invalid_input"
    with pytest.raises(PricingValidationError) as exc:
        PricingEngineV2.calculate(request(), config(), blocks, catalog())
    assert exc.value.code == "invalid_input"


@pytest.mark.parametrize("field,bad", [
    ("required_item_ids", None), ("included_item_ids", None),
    ("required_item_ids", "required"), ("included_item_ids", {"included": True}),
])
def test_invalid_catalog_item_id_sequences_are_typed_before_unpacked(field, bad):
    stock = catalog()
    stock["packages"]["party"][field] = bad
    with pytest.raises(PricingValidationError) as exc:
        run(stock=stock)
    assert exc.value.code == "invalid_input"


def test_explicit_workflow_selection_matches_request_and_still_charges_required_item():
    blocks = [dict(type="package"), dict(type="items", item_ids=["lights"]), dict(type="rounding")]
    result = run(req=request(selected_item_ids=("lights",)), blocks=blocks)
    assert [(line.code, line.amount) for line in result.lines] == [
        ("package:party", Decimal("100")), ("item:lights", Decimal("30")),
        ("item:required", Decimal("10"))]
    assert result.gross_total == Decimal("140.00")


@pytest.mark.parametrize("chosen,listed", [
    (("lights",), ()), (("lights", "included"), ("lights",)),
    ((), ("lights",)), (("lights",), ("required", "lights")),
])
def test_explicit_item_ids_never_ignore_or_override_request_selection(chosen, listed):
    blocks = [dict(type="package"), dict(type="items", item_ids=list(listed))]
    with pytest.raises(PricingValidationError) as exc:
        run(req=request(selected_item_ids=chosen), blocks=blocks)
    assert exc.value.code == "reference_error"


def test_cent_quantization_reconciles_line_and_last_subtotal():
    stock = catalog(packages={"party": dict(label="Party", base_price="1.005", version=3,
                                             required_item_ids=(), included_item_ids=())})
    blocks = [dict(type="package"), dict(type="items"), dict(type="rounding")]
    result = run(req=request(selected_item_ids=()), cfg=config(rounding_step="0.001"),
                 blocks=blocks, stock=stock)
    assert result.gross_total == Decimal("1.01")
    assert result.block_subtotals[-1]["subtotal"] == result.gross_total
    assert sum((line.amount for line in result.lines), Decimal("0")) == result.gross_total
    assert result.rounding_difference == Decimal("0.005")
    assert [(line.code, line.amount) for line in result.lines] == [
        ("package:party", Decimal("1.005")), ("rounding:cent", Decimal("0.005"))]
    assert json.dumps(result.to_dict(), sort_keys=True) == json.dumps(
        run(req=request(selected_item_ids=()), cfg=config(rounding_step="0.001"),
            blocks=blocks, stock=stock).to_dict(), sort_keys=True)
