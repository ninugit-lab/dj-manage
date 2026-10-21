# Pricing V2 Admin and Offer Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide understandable profile/package/workflow administration and a versioned admin offer workflow with configurable email summaries.

**Architecture:** Split configuration into focused server-rendered panels backed by validated service endpoints. Event pricing becomes a review workspace where adjustments feed a draft; release freezes an offer revision and retries send that same revision.

**Tech Stack:** Django 4.2 templates/views, vanilla JS, Pricing V2 services, Google Gmail service, pytest

**Spec:** `docs/superpowers/specs/2026-10-22-preiskalkulation-v2-design.md`

## Global Constraints

- Requires completed core and integration plans.
- Common configuration uses simple forms; advanced rules/formulas remain available.
- A released offer is immutable; retries never recalculate it.
- Manual adjustments require label, nonzero amount and reason.
- Releasing an offer does not confirm the event.
- Email prices are Brutto-Endpreise from the frozen offer result.

## Review Focus

- Concurrent offer release must create one offer number — Task 4 test.
- Concurrent profile/package edits must detect stale versions — Tasks 1–2 tests.
- Gmail timeout must leave a retryable immutable `send_failed` offer — Task 5 test.
- Unknown email placeholders must show validation, not a 500 — Task 5 test.
- Referenced components must be retired or deletion-blocked, never invalidate offers — Tasks 2–3 tests.

---

### Task 1: Global Pricing Profile Admin

**Files:**
- Create: `app/wishlist/pricing_admin_views.py`
- Modify: `app/wishlist/admin_urls.py`
- Create: `app/templates/dj_admin/pricing_v2/profile.html`
- Create: `app/wishlist/tests/test_pricing_v2_profile_admin.py`

**Interfaces:**
- Produces endpoints `pricing-v2/profile/`, `api/pricing-v2/profile/validate/`, `api/pricing-v2/profile/activate/`; optimistic `version` token.

- [ ] Write failing staff/auth, validation, stale-write, clone-new-version and activation tests.
- [ ] Run RED.
- [ ] Implement simple fields for global factors, tier/season editors, validation preview and version cloning. Never mutate an active profile.
- [ ] Run tests and commit: `feat(pricing): add versioned profile administration`.

### Task 2: Package Override and Component Admin

**Files:**
- Modify: `app/wishlist/pricing_admin_views.py`
- Create: `app/templates/dj_admin/pricing_v2/packages.html`
- Create: `app/templates/dj_admin/pricing_v2/components.html`
- Create: `app/wishlist/tests/test_pricing_v2_package_admin.py`

**Interfaces:**
- Produces factor modes `inherit|override|disabled`; validated CRUD for items/rules/formulas/seasons/lead-time/guest tiers.

- [ ] Write failing inheritance display, override save, disabled factor, stale package, referenced-component retirement, formula-mode validation and multi-condition rule tests.
- [ ] Run RED.
- [ ] Implement focused forms and increment package version on pricing changes. Replace destructive deletes of referenced components with retirement.
- [ ] Run tests and commit: `feat(pricing): add package override administration`.

### Task 3: Workflow Builder V2 and Test Lab

**Files:**
- Modify: `app/wishlist/pricing_admin_views.py`
- Create: `app/templates/dj_admin/pricing_v2/workflow.html`
- Create: `app/wishlist/tests/test_pricing_v2_workflow_admin.py`

**Interfaces:**
- Produces workflow CRUD/validation/test endpoints; consumes core validator and canonical service.

- [ ] Write failing tests for all 12 block types, duplicate/order/reference validation, activation gate, V1/V2 comparison and workflow version increment.
- [ ] Run RED.
- [ ] Build click-based ordered canvas with source values, warnings, test inputs, breakdown and comparison. Block activation while errors exist.
- [ ] Run tests and commit: `feat(pricing): add validated v2 workflow builder`.

### Task 4: Event Price Review and Offer Release

**Files:**
- Create: `app/wishlist/pricing_v2/offers.py`
- Modify: `app/wishlist/admin_views.py`
- Modify: `app/wishlist/admin_urls.py`
- Modify: `app/templates/dj_admin/event_form.html`
- Create: `app/wishlist/tests/test_pricing_v2_offers.py`

**Interfaces:**
- Produces: `build_draft(event, actor)`, `add_adjustment(event, label, amount, reason, actor)`, `release_offer(event, actor) -> PriceCalculationRevision`; endpoints `events/<pk>/price/recalculate|adjust|release/`.

- [ ] Write failing tests for estimate/draft/difference display, required adjustment reason, immutable release, sequential offer numbers under row lock and unchanged event status.
- [ ] Run RED.
- [ ] Implement transactional draft/release services and `Preis & Angebot` panel with email preview.
- [ ] Run tests and commit: `feat(pricing): add offer review and release workflow`.

### Task 5: Offer Email, History and Retry

**Files:**
- Modify: `app/wishlist/models.py`
- Create: `app/wishlist/migrations/0015_offer_email_template.py`
- Modify: `app/wishlist/pricing_v2/offers.py`
- Modify: `app/templates/dj_admin/config.html`
- Modify: `app/templates/dj_admin/event_form.html`
- Create: `app/wishlist/tests/test_pricing_v2_offer_email.py`

**Interfaces:**
- Produces AppConfig subject/body; `send_offer(revision, *, retry=False) -> SendResult`; endpoint `price-revisions/<pk>/send/`.

- [ ] Write failing tests for exact placeholders `{client_name,event_name,event_date,price_breakdown,gross_total,dj_name}`, unknown placeholders, sent/send_failed, EmailLog and retry preserving result/total/offer number.
- [ ] Run RED.
- [ ] Implement escaped plain-text breakdown, configurable template, history and retry action.
- [ ] Run migration check/full offer suite and commit: `feat(pricing): send versioned offer summaries`.
