"""Activation gate for the explicit V2 pricing engine mode switch."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import logging
import hashlib
import json
from typing import Any, Iterable

from django.db import transaction
from django.utils import timezone

from wishlist.models import (
    AppConfig, Event, PriceCalculationRevision, PriceItem, PricingFormula, PricingPackage,
    PricingProfile, PricingReferenceCase, PricingRule, PricingWorkflow,
)
from .config import validate_profile_config
from .input import PricingValidationError
from .migration import compare_event
from .workflow import validate_workflow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivationReport:
    """Machine-readable activation checklist and its human-facing messages."""

    checks: tuple[dict[str, Any], ...] = ()
    errors: tuple[dict[str, Any], ...] = ()
    warnings: tuple[dict[str, Any], ...] = ()
    reference_case_stamp: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def valid(self) -> bool:
        return self.ok

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "valid": self.valid, "checks": list(self.checks),
                "errors": list(self.errors), "warnings": list(self.warnings),
                "reference_case_stamp": self.reference_case_stamp}


def _reference_case_stamp():
    rows = list(PricingReferenceCase.objects.filter(approved=True).order_by("pk").values(
        "pk", "updated_at", "expected_v1_total", "expected_v2_total", "expected_difference",
        "tolerance_threshold", "result_json"))
    return hashlib.sha256(json.dumps(rows, default=str, sort_keys=True).encode()).hexdigest()


def _check(checks, errors, name, passed, message, **details):
    item = {"name": name, "ok": bool(passed), "message": message, **details}
    checks.append(item)
    if not passed:
        errors.append({"check": name, "message": message, **details})


def _threshold(profile):
    config = profile.config_json if profile else {}
    for key in ("activation_difference_threshold", "reference_difference_threshold",
                "v1_v2_difference_threshold"):
        value = config.get(key) if isinstance(config, dict) else None
        if value is not None:
            try:
                value = Decimal(str(value))
                if value.is_finite() and value >= 0:
                    return value
            except (InvalidOperation, TypeError, ValueError):
                return None
    return None


def _references(workflow):
    refs = {"packages": set(), "items": set(), "rules": set(), "formulas": set()}
    for block in workflow.workflow_json if isinstance(workflow.workflow_json, list) else []:
        if not isinstance(block, dict):
            continue
        config = block.get("config") if isinstance(block.get("config"), dict) else block
        mapping = {"package": ("package_id", "packages"), "items": ("item_ids", "items"),
                   "rules": ("rule_ids", "rules"), "formula": ("formula_id", "formulas")}
        if block.get("type") not in mapping:
            continue
        key, collection = mapping[block["type"]]
        value = config.get(key)
        values = (value or []) if block["type"] in ("items", "rules") else [value]
        refs[collection].update(str(v) for v in values if v is not None)
    return refs


def _normalized_workflow(workflow):
    result = []
    for block in workflow.workflow_json if isinstance(workflow.workflow_json, list) else []:
        if not isinstance(block, dict):
            return workflow.workflow_json
        config = block.get("config", {})
        if not isinstance(config, dict):
            return workflow.workflow_json
        result.append({**{key: value for key, value in block.items() if key != "config"}, **config})
    return result


def _reference_check(model, identities, *, public=False):
    if not identities:
        return True, []
    query = model.objects.filter(pk__in=identities, is_active=True)
    if public and hasattr(model, "is_public"):
        query = query.filter(is_public=True)
    found = {str(pk) for pk in query.values_list("pk", flat=True)}
    return found == set(identities), sorted(set(identities) - found)


def _validate_reference_cases(profile, workflow, reference_cases, checks, errors, warnings):
    if reference_cases is None:
        reference_cases = list(PricingReferenceCase.objects.filter(approved=True).values(
            "name", "result_json", "expected_v1_total", "expected_v2_total", "expected_difference",
            "tolerance_threshold"))
        for case in reference_cases:
            case["result"] = case.pop("result_json")
            case["difference_threshold"] = case.pop("tolerance_threshold")
            case["stored"] = True
    cases = list(reference_cases or [])
    _check(checks, errors, "reference_cases", bool(cases),
           "Mindestens ein Referenzfall ist erforderlich.", count=len(cases))
    threshold = _threshold(profile)
    for index, case in enumerate(cases):
        try:
            result = case() if callable(case) else None
            if (result is None and isinstance(case, dict) and isinstance(case.get("result"), dict)
                    and case["result"].get("event_id") is not None):
                event = Event.objects.get(pk=case["result"]["event_id"])
                result = compare_event(event, profile, workflow).to_dict()
            if result is None and isinstance(case, dict) and case.get("event") is not None:
                result = compare_event(case["event"], profile, workflow, actor=case.get("actor"))
            if result is None and isinstance(case, dict):
                result = case.get("result")
            if (isinstance(case, dict) and case.get("stored")
                    and (not isinstance(case.get("result"), dict) or not case["result"].get("event_id"))):
                raise ValueError("Gespeicherter Referenzfall benötigt einen prüfbaren Event-Bezug.")
            if hasattr(result, "to_dict"):
                result = result.to_dict()
            if not isinstance(result, dict):
                raise ValueError("Referenzfall lieferte kein Ergebnis.")
            v1 = result.get("v1_gross", result.get("v1_total"))
            v2 = result.get("v2_gross", result.get("v2_total"))
            difference = result.get("difference")
            if v1 is None or v2 is None:
                raise ValueError("V1/V2-Ergebnis fehlt.")
            v1, v2 = Decimal(str(v1)), Decimal(str(v2))
            difference = v2 - v1 if difference is None else Decimal(str(difference))
            expected_v1 = case.get("expected_v1_total") if isinstance(case, dict) else None
            expected_v2 = case.get("expected_v2_total") if isinstance(case, dict) else None
            expected_difference = case.get("expected_difference") if isinstance(case, dict) else None
            if expected_v1 is None or expected_v2 is None or expected_difference is None:
                raise ValueError("Alle erwarteten V1-, V2- und Differenzsummen sind erforderlich.")
            if v1 != Decimal(str(expected_v1)):
                raise ValueError("V1-Summe entspricht nicht dem erwarteten Wert.")
            if v2 != Decimal(str(expected_v2)):
                raise ValueError("V2-Summe entspricht nicht dem erwarteten Wert.")
            if difference != Decimal(str(expected_difference)):
                raise ValueError("Differenz entspricht nicht dem erwarteten Wert.")
            allowed = case.get("difference_threshold") if isinstance(case, dict) else None
            allowed = threshold if allowed is None else Decimal(str(allowed))
            if difference.copy_abs() and allowed is None:
                warnings.append({"check": "reference_cases", "message":
                                 "V1/V2-Differenz ohne konfigurierten Schwellenwert.", "case": index})
                raise ValueError("Differenz-Schwellenwert fehlt.")
            if allowed is not None and difference.copy_abs() > allowed:
                raise ValueError("V1/V2-Differenz überschreitet den Schwellenwert.")
            _check(checks, errors, f"reference_case_{index}", True, "Referenzfall bestanden.",
                   v1_total=str(v1), v2_total=str(v2), difference=str(difference))
        except (Exception,) as exc:
            _check(checks, errors, f"reference_case_{index}", False, str(exc))


def validate_activation(profile=None, workflow=None, reference_cases=None) -> ActivationReport:
    """Evaluate every activation prerequisite without changing database state."""
    checks, errors, warnings = [], [], []
    profiles = list(PricingProfile.objects.filter(status=PricingProfile.Status.ACTIVE)
                    if profile is None else [profile])
    active_profiles = [p for p in profiles if p and p.status == PricingProfile.Status.ACTIVE]
    _check(checks, errors, "active_profile", len(active_profiles) == 1,
           "Genau ein aktives Preisprofil ist erforderlich.", count=len(active_profiles))
    profile = active_profiles[0] if len(active_profiles) == 1 else profile
    if profile:
        try:
            validate_profile_config(profile.config_json)
            _check(checks, errors, "profile_config", True, "Aktives Profil ist gültig.")
        except Exception as exc:
            _check(checks, errors, "profile_config", False, str(exc))

    workflows = list(PricingWorkflow.objects.filter(is_active=True, is_default=True)
                     if workflow is None else [workflow])
    defaults = [w for w in workflows if w and w.is_active and w.is_default]
    _check(checks, errors, "active_default_workflow", len(defaults) == 1,
           "Genau ein aktiver Standard-Workflow ist erforderlich.", count=len(defaults))
    workflow = defaults[0] if len(defaults) == 1 else workflow
    refs = _references(workflow) if workflow else {key: set() for key in ("packages", "items", "rules", "formulas")}
    if workflow:
        try:
            validate_workflow(_normalized_workflow(workflow), {
                "packages": {key: {"is_active": p.is_active} for p in PricingPackage.objects.all() for key in (p.pk, str(p.pk))},
                "items": {key: {"is_active": i.is_active} for i in PriceItem.objects.all() for key in (i.pk, str(i.pk))},
                "rules": {key: {"is_active": r.is_active} for r in PricingRule.objects.all() for key in (r.pk, str(r.pk))},
                "formulas": {key: {"is_active": f.is_active} for f in PricingFormula.objects.all() for key in (f.pk, str(f.pk))},
            })
            _check(checks, errors, "workflow_valid", True, "Workflow ist gültig.")
        except Exception as exc:
            _check(checks, errors, "workflow_valid", False, str(exc))
    models = {"packages": (PricingPackage, True), "items": (PriceItem, True),
              "rules": (PricingRule, False), "formulas": (PricingFormula, False)}
    for name, (model, public) in models.items():
        passed, missing = _reference_check(model, refs[name], public=public)
        _check(checks, errors, f"references_{name}", passed,
               f"Alle referenzierten {name} sind aktiv und verfügbar.", missing=missing)

    unresolved = []
    for revision in PriceCalculationRevision.objects.filter(kind=PriceCalculationRevision.Kind.MIGRATION_COMPARE):
        if not isinstance(revision.input_json, dict) or not isinstance(revision.result_json, dict):
            unresolved.append(revision.pk)
        elif (revision.result_json.get("malformed_snapshot") or revision.input_json.get("malformed_snapshot")
              or revision.result_json.get("diagnostic")):
            unresolved.append(revision.pk)
        elif revision.result_json.get("reviewed") is not True and revision.input_json.get("reviewed") is not True:
            if revision.difference not in (None, 0, Decimal("0")):
                unresolved.append(revision.pk)
    _check(checks, errors, "migration_differences_reviewed", not unresolved,
           "Alle Migration-Differenzen müssen geprüft oder gelöst sein.", unresolved=unresolved)
    _validate_reference_cases(profile, workflow, reference_cases, checks, errors, warnings)
    return ActivationReport(tuple(checks), tuple(errors), tuple(warnings), _reference_case_stamp())


def activate_v2(actor, *, expected_reference_case_stamp=None):
    """Atomically switch the singleton mode after re-running the gate under lock."""
    with transaction.atomic():
        config = AppConfig.objects.select_for_update().get_or_create(pk=1)[0]
        if (expected_reference_case_stamp is not None and
                expected_reference_case_stamp != _reference_case_stamp()):
            raise PricingValidationError("stale_activation", "Referenzfälle wurden seit der Checkliste geändert.", "reference_cases")
        report = validate_activation()
        if not report.ok:
            raise PricingValidationError("activation_blocked", "Aktivierung nicht möglich.", "activation")
        config.pricing_engine_mode = AppConfig.PricingEngineMode.V2
        config.save(update_fields=["pricing_engine_mode", "updated_at"])
        logger.info("pricing_v2_activation actor=%s at=%s", getattr(actor, "pk", actor), timezone.now().isoformat())
    return config


def rollback_to_legacy(actor):
    """Atomically return to legacy without touching V2 data or event totals."""
    with transaction.atomic():
        config = AppConfig.objects.select_for_update().get_or_create(pk=1)[0]
        config.pricing_engine_mode = AppConfig.PricingEngineMode.LEGACY
        config.save(update_fields=["pricing_engine_mode", "updated_at"])
        logger.info("pricing_v2_rollback actor=%s at=%s", getattr(actor, "pk", actor), timezone.now().isoformat())
    return config
