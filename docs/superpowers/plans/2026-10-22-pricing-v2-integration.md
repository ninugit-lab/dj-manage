# Pricing V2 Integration and Shadow Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Pricing V2 into public and admin calculation paths behind one `legacy|shadow|v2` switch while preserving V1 behavior until cutover.

**Architecture:** A canonical service freezes ORM data, invokes the selected engine, stores revisions and returns one API contract. Public preview, submit, admin preview and event save all call this service; shadow mode stores V1/V2 comparisons while showing V1.

**Tech Stack:** Django 4.2, Pricing V2 core, vanilla JS, pytest

**Spec:** `docs/superpowers/specs/2026-10-22-preiskalkulation-v2-design.md`

## Global Constraints

- Requires all commits from `2026-10-22-pricing-v2-core.md`.
- One setting controls all callers: `legacy`, `shadow`, or `v2`.
- Public requests may select only active public packages/items; required public items are restored server-side.
- Public errors show `Preis wird individuell geprüft`; internal details remain private.
- Public, admin and persisted V2 calculations use identical normalized inputs and service code.
- Money JSON values are decimal strings.

## Review Focus

- Out-of-order browser responses must not display an older estimate — Task 3 test.
- A manipulated request omitting required items must still price/persist them — Task 4 test.
- Shadow-mode V2 failure must not break visible V1 behavior — Task 2 test.
- A mode switch must affect preview, submit, admin preview and event save together — Task 5 test.
- Submit retries must not create duplicate estimate revisions for one event — Task 4 test.

---

### Task 1: Canonical Pricing Service and Revision Writer

**Files:**
- Create: `app/wishlist/pricing_v2/services.py`
- Create: `app/wishlist/tests/test_pricing_v2_services.py`

**Interfaces:**
- Consumes: `PricingInput`, `resolve_pricing_config`, `validate_workflow`, `PricingEngineV2`.
- Produces: `calculate_payload(payload, *, public, actor=None) -> PricingResult`; `calculate_event(event, *, actor=None) -> PricingResult`; `save_revision(event, result, *, kind, actor=None, legacy_total=None) -> PriceCalculationRevision`.

- [ ] Write failing tests for ORM catalog freezing, active profile/default workflow loading, monotonic revision numbering under `select_for_update`, complete snapshots and Event total writes only via service.
- [ ] Run `pytest -q wishlist/tests/test_pricing_v2_services.py`; expect missing service failures.
- [ ] Implement service with one transaction for revision numbering/writes; keep engine ORM-free.
- [ ] Run tests and commit: `feat(pricing): add canonical v2 pricing service`.

### Task 2: Engine Mode and Shadow Comparison

**Files:**
- Modify: `app/wishlist/models.py`
- Create: `app/wishlist/migrations/0014_pricing_engine_mode.py`
- Modify: `app/wishlist/pricing_v2/services.py`
- Create: `app/wishlist/tests/test_pricing_v2_shadow.py`

**Interfaces:**
- Produces: `AppConfig.pricing_engine_mode` choices `legacy|shadow|v2`; `calculate_with_mode(..., persist_comparison=False) -> PricingDecision`.

- [ ] Write failing tests: legacy calls V1 only; shadow returns V1; `persist_comparison=True` stores one `migration_compare`; live preview with the default `False` stores none; V2 returns V2; shadow V2 failure returns V1 plus diagnostics.
- [ ] Run RED.
- [ ] Add field/migration and dispatcher. Serialize V1 into a compatibility result without changing its arithmetic.
- [ ] Run migration check/tests and commit: `feat(pricing): add v2 shadow mode`.

### Task 3: Public Estimate API and Safe UI

**Files:**
- Modify: `app/wishlist/views.py:640-704`
- Modify: `app/templates/wishlist/event_form.html:417-472`
- Create: `app/wishlist/tests/test_pricing_v2_public_api.py`
- Create: `app/wishlist/tests/test_pricing_v2_public_ui.py`

**Interfaces:**
- Consumes: `calculate_with_mode(payload, public=True)`.
- Produces: `/api/price-estimate/` JSON `{ok, engine_version, status, lines, gross_total, warnings}`.

- [ ] Write failing API tests for public filtering, required items, decimal strings, missing-distance warning, hidden internal errors and equality with direct service.
- [ ] Write failing JS/helper tests for stale response sequence IDs and DOM/textContent rendering instead of unescaped admin text in `innerHTML`.
- [ ] Run RED.
- [ ] Replace view-local assembly with service call. Show `Unverbindliche Schätzung: ca. … € brutto`; use `AbortController` and monotonically increasing IDs.
- [ ] Run tests and commit: `feat(pricing): integrate v2 public estimate`.

### Task 4: Server-Authoritative Submission and Estimate Revision

**Files:**
- Modify: `app/wishlist/views.py:297-490`
- Modify: `app/wishlist/tests/test_booking_validation.py`
- Create: `app/wishlist/tests/test_pricing_v2_submission.py`

**Interfaces:**
- Consumes: saved Event and public selections.
- Produces: exactly one `estimate` revision per successful submission; `Event.total_price` from service or null on individual review.

- [ ] Write failing tests for forged browser total ignored, required-item restoration, stored versions/inputs, pricing failure allowing inquiry with null price and revision idempotency.
- [ ] Run RED.
- [ ] Remove direct PriceEngine/snapshot assembly; call service after Event creation and before asynchronous Google work.
- [ ] Run tests and commit: `feat(pricing): persist authoritative customer estimates`.

### Task 5: Admin Preview and Event Save Parity

**Files:**
- Modify: `app/wishlist/admin_views.py:146-307,825-873`
- Modify: `app/templates/dj_admin/event_form.html:157-390,590-700`
- Modify: `app/wishlist/tests/test_event_edit_price_save.py`
- Create: `app/wishlist/tests/test_pricing_v2_parity.py`

**Interfaces:**
- Consumes: canonical service.
- Produces: admin preview response compatible with public result; event save creates a draft revision and rejects a free total override.

- [ ] Write failing tests proving identical public/admin/service totals, disabled required checkboxes persist, posted total is ignored, save creates draft, and all mode-sensitive paths change together.
- [ ] Run RED.
- [ ] Route admin API and save through service; make total read-only. Preserve V1 controls only in legacy/shadow.
- [ ] Run all integration tests and commit: `feat(pricing): unify admin and persisted v2 pricing`.
