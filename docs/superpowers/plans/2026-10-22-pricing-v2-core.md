# Pricing V2 Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the versioned, deterministic Pricing V2 domain model and calculation engine without changing any live pricing caller.

**Architecture:** Add immutable pricing contracts and focused V2 modules beside the legacy engine. A versioned global profile plus package overrides resolve into one frozen configuration, which a validated workflow executes using `Decimal` only.

**Tech Stack:** Python 3.12, Django 4.2, SQLite, pytest/pytest-django, `holidays>=0.59,<1`

**Spec:** `docs/superpowers/specs/2026-10-22-preiskalkulation-v2-design.md`

## Global Constraints

- Public prices are unverbindliche Brutto-Schätzungen; V2 never adds VAT.
- V1 remains unchanged and authoritative throughout this plan.
- Money arithmetic uses `Decimal`; JSON exposes decimal strings, never float money values.
- NRW (`NW`) is the default holiday region; an event may override it.
- Global defaults support package modes `inherit`, `override`, and `disabled` per factor.
- Unknown blocks, duplicate singleton blocks, invalid references, negative prices, discounts outside `0..100`, and invalid formulas are validation errors, never silent zero steps.

## Review Focus

- Overnight events (`23:00–02:00`) must calculate three hours, not a negative duration — Task 5 test.
- Exactly-on-boundary guest, distance, duration, season, and lead-time tiers must select one deterministic tier — Tasks 4–5 tests.
- Missing package/profile references must return `reference_error`, not fall back silently — Tasks 3 and 7 tests.
- A package-disabled factor must remain disabled even when the global profile has a nonzero value — Task 3 test.
- Repeated calculation with identical frozen inputs/config must produce byte-equivalent serialized results — Task 7 test.

---

### Task 1: Versioned Pricing Schema

**Files:**
- Modify: `requirements.txt`
- Modify: `app/wishlist/models.py`
- Create: `app/wishlist/migrations/0012_pricing_v2_core.py`
- Create: `app/wishlist/tests/test_pricing_v2_models.py`

**Interfaces:**
- Produces: `PricingProfile`, `PricingPackageOverride`, `PriceCalculationRevision`, `ManualPriceAdjustment`; version fields on `PricingPackage` and `PricingWorkflow`; pricing inputs on `Event`.

- [ ] **Step 1: Write failing model/constraint tests**

Test names and assertions:

```python
def test_only_one_active_pricing_profile(db): ...  # second active profile raises IntegrityError
def test_profile_name_and_version_are_unique(db): ...
def test_offer_numbers_are_unique_per_event(db): ...
def test_manual_adjustment_requires_nonempty_reason(db): ...
def test_pricing_package_override_unique_per_profile_and_package(db): ...
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest -q wishlist/tests/test_pricing_v2_models.py` from `app/` in the project container.  
Expected: collection/import failures because V2 models do not exist.

- [ ] **Step 3: Add models and migration**

Exact model surface:

```python
class PricingProfile(models.Model):
    class Status(models.TextChoices): DRAFT='draft'; ACTIVE='active'; RETIRED='retired'
    name: CharField(200)
    version: PositiveIntegerField
    status: CharField
    valid_from: DateTimeField
    config_json: JSONField
    created_by: ForeignKey(User, SET_NULL, null=True)
    created_at: DateTimeField(auto_now_add=True)

class PricingPackageOverride(models.Model):
    profile: ForeignKey(PricingProfile, CASCADE)
    package: ForeignKey(PricingPackage, CASCADE)
    overrides_json: JSONField(default=dict)

class PriceCalculationRevision(models.Model):
    # kind choices: estimate, draft, offer, legacy, migration_compare
    # status choices: draft, released, sent, send_failed, superseded
    event: ForeignKey(Event, CASCADE, related_name='price_revisions')
    revision_number: PositiveIntegerField
    offer_number: PositiveIntegerField(null=True, blank=True)
    engine_version/profile/package/workflow IDs and versions
    input_json/result_json: JSONField
    gross_total: DecimalField(12, 2)
    previous_revision: ForeignKey('self', SET_NULL, null=True, blank=True)
    legacy_total/difference: DecimalField(12, 2, null=True, blank=True)
    created_by/created_at/sent_at

class ManualPriceAdjustment(models.Model):
    event: ForeignKey(Event, CASCADE, related_name='price_adjustments')
    label: CharField(200)
    amount: DecimalField(12, 2)
    reason: TextField
    created_by/created_at
```

Add `version=1` to `PricingPackage` and `PricingWorkflow`; add `pricing_region='NW'`, `setup_minutes`, `teardown_minutes` to `Event`. Add conditional unique constraints for one active profile and non-null `(event, offer_number)`. Add `holidays>=0.59,<1`.

- [ ] **Step 4: Generate/check migration and run GREEN**

Run: `python manage.py makemigrations --check --dry-run && pytest -q wishlist/tests/test_pricing_v2_models.py`  
Expected: no model drift; all tests pass.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt app/wishlist/models.py app/wishlist/migrations/0012_pricing_v2_core.py app/wishlist/tests/test_pricing_v2_models.py
git commit -m "feat(pricing): add versioned v2 pricing schema"
```

### Task 2: Immutable Inputs and Decimal Result Contracts

**Files:**
- Create: `app/wishlist/pricing_v2/__init__.py`
- Create: `app/wishlist/pricing_v2/input.py`
- Create: `app/wishlist/pricing_v2/result.py`
- Create: `app/wishlist/tests/test_pricing_v2_contracts.py`

**Interfaces:**
- Produces: `PricingInput.from_mapping(data, *, requested_at) -> PricingInput`; `PricingLine`; `PricingResult.to_dict() -> dict`.

- [ ] Write failing tests for ISO date/time normalization, decimal distance, nonnegative guests/setup/teardown, default `NW`, `invalid_input`/`missing_input`, and serialization of `Decimal('11.00')` as `'11.00'`.
- [ ] Run `pytest -q wishlist/tests/test_pricing_v2_contracts.py`; expect import failures.
- [ ] Implement frozen `PricingInput`, `PricingLine`, `PricingResult`, and `PricingValidationError(code, message, field=None)` dataclasses. Keep presentation formatting outside contracts.
- [ ] Run tests; expect pass.
- [ ] Commit: `feat(pricing): add immutable v2 contracts`.

### Task 3: Profile and Package Configuration Resolution

**Files:**
- Create: `app/wishlist/pricing_v2/config.py`
- Create: `app/wishlist/tests/test_pricing_v2_config.py`

**Interfaces:**
- Consumes: `PricingProfile.config_json`, `PricingPackageOverride.overrides_json`.
- Produces: `resolve_pricing_config(profile, package=None) -> ResolvedPricingConfig`; `validate_profile_config(mapping) -> NormalizedProfileConfig`.

- [ ] Write failing tests for keys `included_hours`, `extra_hour_rate`, `setup_minutes`, `teardown_minutes`, `setup_teardown_hour_rate`, `included_km`, `km_rate`, weekend/holiday values, `holiday_region`, `seasons`, `lead_time_tiers`, `guest_tiers`, `minimum_price`, `rounding_mode`, `rounding_step`; assert inherit/override/disabled and source metadata.
- [ ] Run `pytest -q wishlist/tests/test_pricing_v2_config.py`; expect missing functions.
- [ ] Implement validation and resolution. Reject negative values, invalid/overlapping ranges, invalid region codes, modes outside `nearest|up|down`, and nonpositive rounding steps. Missing references raise `reference_error`.
- [ ] Run tests and commit: `feat(pricing): resolve profile and package overrides`.

### Task 4: Calendar and Tier Factors

**Files:**
- Create: `app/wishlist/pricing_v2/calendar.py`
- Create: `app/wishlist/tests/test_pricing_v2_calendar.py`

**Interfaces:**
- Produces: `calendar_factors(event_date, requested_at, region, config) -> tuple[Factor, ...]`; `select_tier(value, tiers) -> Mapping | None`.

- [ ] Write failing tests for NRW New Year/Good Friday/Christmas, a holiday differing by state, Saturday/Sunday, inclusive season boundaries, lead-time boundaries, guest-tier boundaries, and overlapping-season rejection.
- [ ] Run `pytest -q wishlist/tests/test_pricing_v2_calendar.py`; expect RED.
- [ ] Implement with `holidays.Germany(subdiv=region)`. Return stable codes and `Decimal` percentages; validated bounds are inclusive and nonoverlapping.
- [ ] Run tests and commit: `feat(pricing): add calendar and tier factors`.

### Task 5: Standard Pricing Blocks

**Files:**
- Create: `app/wishlist/pricing_v2/blocks.py`
- Create: `app/wishlist/tests/test_pricing_v2_blocks.py`

**Interfaces:**
- Consumes: `PricingInput`, `ResolvedPricingConfig`, current `Decimal` subtotal.
- Produces: `BlockResult(lines, subtotal)` via `package_block`, `items_block`, `duration_block`, `setup_teardown_block`, `travel_block`, `guest_tiers_block`, `calendar_factors_block`, `discount_block`, `minimum_price_block`, `rounding_block`.

- [ ] Write failing tests for included items, server-required items, overnight duration, exact included-hour/free-km boundaries, extra-hour rounding, setup defaults/event overrides, tier boundaries, percentage/absolute discount, minimum before/after discount, and nearest/up/down rounding to 5/10/50 €.
- [ ] Run `pytest -q wishlist/tests/test_pricing_v2_blocks.py`; expect RED.
- [ ] Implement pure block functions with no ORM access. Every line contains code, label, basis, quantity, unit, unit price, amount and source.
- [ ] Run tests and commit: `feat(pricing): implement standard v2 pricing blocks`.

### Task 6: Advanced Rules and Formula Modes

**Files:**
- Create: `app/wishlist/pricing_v2/advanced.py`
- Modify: `app/wishlist/models.py`
- Create: `app/wishlist/migrations/0013_pricing_formula_modes.py`
- Create: `app/wishlist/tests/test_pricing_v2_advanced.py`

**Interfaces:**
- Produces: `apply_rules(...) -> BlockResult`; `apply_formula(...) -> BlockResult`; `PricingFormula.mode` choices `add|subtract|replace`.

- [ ] Write failing tests for `applies_to` bases `package|tech|extras|travel|subtotal`, all formula modes, invalid variable/division-by-zero errors, and Decimal-only formula variables.
- [ ] Run RED.
- [ ] Reuse the AST whitelist with Decimal inputs. Replace V2 `flat_set` with formula `replace` or minimum; keep legacy behavior in V1.
- [ ] Run migration check/tests and commit: `feat(pricing): add scoped rules and formula modes`.

### Task 7: Workflow Validator and Deterministic Engine

**Files:**
- Create: `app/wishlist/pricing_v2/workflow.py`
- Create: `app/wishlist/pricing_v2/engine.py`
- Create: `app/wishlist/tests/test_pricing_v2_engine.py`

**Interfaces:**
- Produces: `validate_workflow(blocks, references) -> ValidatedWorkflow`; `PricingEngineV2.calculate(request, config, workflow, catalog) -> PricingResult`.
- Consumed by: integration plan service layer.

- [ ] Write failing tests for the default 12-block pipeline, unknown/duplicate blocks, rounding-not-last, missing references, missing price block, order-sensitive minimum/discount examples, stable line ordering, and byte-equivalent sorted JSON on repeat.
- [ ] Run `pytest -q wishlist/tests/test_pricing_v2_engine.py`; expect RED.
- [ ] Implement ORM-free validator/dispatcher; quantize final money to `0.01`, return structured errors and frozen versions/inputs.
- [ ] Run `pytest -q wishlist/tests/test_pricing_v2_*.py`; expect all core tests pass.
- [ ] Commit: `feat(pricing): add deterministic v2 workflow engine`.
