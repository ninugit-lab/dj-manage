from decimal import Decimal
from types import SimpleNamespace
from django.utils import timezone

import pytest

from wishlist.models import AppConfig, PriceCalculationRevision, PricingProfile, PricingWorkflow
from wishlist.pricing_v2.activation import ActivationReport, activate_v2, rollback_to_legacy, validate_activation


@pytest.mark.django_db
def test_gate_reports_missing_profile_and_workflow_as_structured_errors():
    report = validate_activation(reference_cases=[])

    assert report.ok is False
    assert {error["check"] for error in report.errors} >= {
        "active_profile", "active_default_workflow", "reference_cases",
    }
    assert all({"name", "ok", "message"} <= set(check) for check in report.checks)


@pytest.mark.django_db
def test_reference_case_checks_expected_totals_and_configured_difference_threshold():
    profile = SimpleNamespace(status="active", config_json={"activation_difference_threshold": "2"})
    workflow = SimpleNamespace(is_active=True, is_default=True, workflow_json=[])
    report = validate_activation(
        profile=profile,
        workflow=workflow,
        reference_cases=[{
            "result": {"v1_gross": "100", "v2_gross": "101", "difference": "1"},
            "expected_v1_total": "100", "expected_v2_total": "101",
        }],
    )

    assert report.ok is False  # catalog/workflow checks still protect the real gate
    reference = next(check for check in report.checks if check["name"] == "reference_case_0")
    assert reference["ok"] is True
    assert reference["difference"] == "1"


@pytest.mark.django_db
def test_atomic_mode_switch_and_rollback_preserve_pricing_data(monkeypatch):
    config = AppConfig.objects.create(pk=1, pricing_engine_mode="legacy")
    profile = PricingProfile.objects.create(name="Global", version=1, status="active", valid_from=timezone.now(), config_json={})
    workflow = PricingWorkflow.objects.create(name="Standard", is_active=True, is_default=True, workflow_json=[])
    passing = ActivationReport(checks=({"name": "all", "ok": True, "message": "ok"},))
    monkeypatch.setattr("wishlist.pricing_v2.activation.validate_activation", lambda: passing)

    activate_v2("staff-1")
    config.refresh_from_db()
    assert config.pricing_engine_mode == "v2"
    assert PricingProfile.objects.filter(pk=profile.pk).exists()
    assert PricingWorkflow.objects.filter(pk=workflow.pk).exists()

    rollback_to_legacy("staff-1")
    config.refresh_from_db()
    assert config.pricing_engine_mode == "legacy"
    assert PricingProfile.objects.filter(pk=profile.pk).exists()
    assert PricingWorkflow.objects.filter(pk=workflow.pk).exists()
