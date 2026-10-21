# Pricing V2 Migration and Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare V1/V2 safely, activate V2 only after validation, and idempotently reprice inquiries plus present/future confirmed events without changing historical or cancelled events.

**Architecture:** Management commands produce auditable comparison/repricing revisions; an activation service validates configuration and reference cases before changing the central mode. Legacy code and snapshots remain available for rollback and historical reads.

**Tech Stack:** Django management commands, Pricing V1/V2 services, pytest

**Spec:** `docs/superpowers/specs/2026-10-22-preiskalkulation-v2-design.md`

## Global Constraints

- Requires completed core, integration and offer/admin plans.
- Reprice all inquiries and confirmed events whose date is today or later.
- Never alter past or cancelled event prices/revisions.
- Existing released offers are never overwritten; repricing creates a new draft.
- Every command is dry-run by default, idempotent with `--apply`, and emits a machine-readable report.
- Rollback changes engine mode only; it never deletes V2 data.

## Review Focus

- Running `--apply` twice must not duplicate revisions — Tasks 1–2 tests.
- Date-boundary execution must use `timezone.localdate()` consistently — Task 2 test.
- One malformed event must be reported without aborting the batch — Tasks 1–2 tests.
- Existing offer and Event total must not be overwritten by migration draft — Task 2 test.
- Mode rollback during traffic must leave a coherent engine mode — Task 3 test.

---

### Task 1: V1/V2 Comparison Command and Report

**Files:**
- Create: `app/wishlist/management/commands/pricing_v2_compare.py`
- Create: `app/wishlist/pricing_v2/migration.py`
- Create: `app/wishlist/tests/test_pricing_v2_compare_command.py`

**Interfaces:**
- Produces: `compare_event(event, profile, workflow) -> Comparison`; command options `--event-id`, `--status`, `--output`, `--apply`.

- [ ] Write failing dry-run/no-write, applied comparison, exact difference, idempotency-key, malformed-event continuation and JSON-report tests.
- [ ] Run RED.
- [ ] Implement command. Idempotency key uses event ID + frozen V1 input hash + V2 profile/workflow versions.
- [ ] Run tests and commit: `feat(pricing): add v1 v2 comparison command`.

### Task 2: Controlled Open-Event Repricing

**Files:**
- Create: `app/wishlist/management/commands/pricing_v2_reprice.py`
- Modify: `app/wishlist/pricing_v2/migration.py`
- Create: `app/wishlist/tests/test_pricing_v2_reprice_command.py`

**Interfaces:**
- Produces: `eligible_for_reprice(event, today) -> bool`; command `pricing_v2_reprice [--apply] [--output path]`.

- [ ] Write failing matrix tests: every inquiry; confirmed date >= today; exclude past/cancelled; preserve released offer; new draft with old/new/difference; repeated apply idempotent.
- [ ] Run RED.
- [ ] Implement per-event transactions so one failure is isolated/reported. Never replace released revisions or legacy snapshots.
- [ ] Run tests and commit: `feat(pricing): add controlled v2 repricing`.

### Task 3: Activation Gate and Atomic Mode Switch

**Files:**
- Create: `app/wishlist/pricing_v2/activation.py`
- Modify: `app/wishlist/pricing_admin_views.py`
- Modify: `app/templates/dj_admin/pricing_v2/workflow.html`
- Create: `app/wishlist/tests/test_pricing_v2_activation.py`

**Interfaces:**
- Produces: `validate_activation(profile, workflow, reference_cases) -> ActivationReport`; `activate_v2(actor)`; `rollback_to_legacy(actor)`.

- [ ] Write failing tests for missing profile, invalid workflow, failed reference case, unresolved migration differences, successful atomic switch and rollback preserving revisions.
- [ ] Run RED.
- [ ] Implement activation checklist and transactional AppConfig mode update under `select_for_update`.
- [ ] Run tests and commit: `feat(pricing): gate and activate v2 pricing`.

### Task 4: End-to-End Cutover Verification and Operations Docs

**Files:**
- Create: `app/wishlist/tests/test_pricing_v2_end_to_end.py`
- Create: `docs/handbook/pricing-v2.md`
- Modify: `DEPLOY.md`

**Interfaces:**
- Consumes all V2 interfaces; produces operational runbook only.

- [ ] Add end-to-end test: public preview → submit revision → admin recalc → adjustment → release → failed send → retry; assert same pre-adjustment total and immutable offer.
- [ ] Add rollback end-to-end test proving legacy resumes without V2 deletion.
- [ ] Run RED if integration gaps remain; fix through owning service interfaces.
- [ ] Run full `pytest -q`, migration checks, `manage.py check`, and Docker build.
- [ ] Document configuration, shadow report review, activation, repricing dry-run/apply, rollback and evidence commands.
- [ ] Commit: `docs(pricing): add v2 rollout runbook`.
