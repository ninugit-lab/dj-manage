from decimal import Decimal
from types import SimpleNamespace
from django.utils import timezone
import json
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

import pytest

from wishlist.models import AppConfig, PriceCalculationRevision, PricingProfile, PricingReferenceCase, PricingWorkflow
from wishlist.pricing_v2.activation import ActivationReport, activate_v2, rollback_to_legacy, validate_activation


@pytest.mark.django_db
def test_diagnostic_comparison_blocks_activation_even_without_difference():
    from wishlist.models import Event
    from datetime import date
    event = Event.objects.create(name='Bad calculation', date=date(2027, 1, 1), location='X')
    PriceCalculationRevision.objects.create(
        event=event, kind='migration_compare', status='draft', revision_number=1,
        engine_version='2', gross_total=Decimal('0'), difference=None,
        input_json={'source': 'migration'}, result_json={'diagnostic': {'code': 'invalid_input'}},
    )
    assert any(e['check'] == 'migration_differences_reviewed' for e in validate_activation().errors)


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
            "expected_v1_total": "100", "expected_v2_total": "101", "expected_difference": "1",
        }],
    )

    assert report.ok is False  # catalog/workflow checks still protect the real gate
    reference = next(check for check in report.checks if check["name"] == "reference_case_0")
    assert reference["ok"] is True
    assert reference["difference"] == "1"


@pytest.mark.django_db
def test_reference_case_without_all_exact_expected_totals_blocks_gate():
    profile = SimpleNamespace(status="active", config_json={"activation_difference_threshold": "2"})
    workflow = SimpleNamespace(is_active=True, is_default=True, workflow_json=[])
    report = validate_activation(
        profile=profile, workflow=workflow,
        reference_cases=[{"result": {"v1_gross": "100", "v2_gross": "101", "difference": "1"},
                          "expected_v1_total": "100", "expected_v2_total": "101", "expected_difference": None}],
    )

    assert any(error["check"] == "reference_case_0" for error in report.errors)


@pytest.mark.django_db
def test_staff_can_save_reference_case_and_activation_uses_stored_case():
    from datetime import date, time
    from wishlist.models import Event, EventPriceCalculation, PricingPackage, PriceItem
    from wishlist.pricing_v2.migration import compare_event
    profile = PricingProfile.objects.create(name='Approved', status='active', valid_from=timezone.now(), config_json={
        'included_hours': '3', 'extra_hour_rate': '0', 'setup_minutes': 0, 'teardown_minutes': 0,
        'setup_teardown_hour_rate': '0', 'included_km': '0', 'km_rate': '0',
        'saturday_percent': '0', 'sunday_percent': '0', 'holiday_percent': '0',
        'holiday_region': 'NW', 'seasons': [], 'lead_time_tiers': [],
        'guest_tiers': [{'lower_bound': 0, 'upper_bound': None, 'value': '0'}],
        'minimum_price': '0', 'rounding_mode': 'nearest', 'rounding_step': '0.01',
    })
    package = PricingPackage.objects.create(name='Reference', base_price=Decimal('100'))
    item = PriceItem.objects.create(name='Reference item', price=Decimal('20'))
    workflow = PricingWorkflow.objects.create(name='Reference flow', is_default=True, workflow_json=[
        {'type': 'package', 'config': {'package_id': package.pk}}, {'type': 'items'}])
    event = Event.objects.create(name='Reference event', date=date(2027, 1, 1), location='X',
                                 time_start=time(18), time_end=time(21))
    calc = EventPriceCalculation.objects.create(event=event, package=package)
    calc.selected_items.add(item)
    comparison = compare_event(event, profile, workflow)
    assert comparison.status == 'ok', comparison.diagnostics
    user = get_user_model().objects.create_user("activation-staff", password="secret", is_staff=True)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    client.get(reverse("dj_admin:pricing_v2_activation"))
    token = client.cookies["csrftoken"].value
    response = client.post(
        reverse("dj_admin:pricing_v2_reference_cases"),
        data=json.dumps({"name": "baseline", "result": {"event_id": event.pk,
                         "v1_gross": str(comparison.v1_gross), "v2_gross": str(comparison.v2_gross),
                         "difference": str(comparison.difference)},
                         "expected_v1_total": str(comparison.v1_gross),
                         "expected_v2_total": str(comparison.v2_gross),
                         "expected_difference": str(comparison.difference)}),
        content_type="application/json", HTTP_X_CSRFTOKEN=token,
    )

    assert response.status_code == 201
    assert PricingReferenceCase.objects.get(name='baseline').expected_v1_total == comparison.v1_gross
    page = client.get(reverse("dj_admin:pricing_v2_activation"))
    assert page.status_code == 200
    assert b"baseline" in page.content


@pytest.mark.django_db
def test_reference_case_edit_serializes_on_appconfig_lock_and_stale_stamp_cannot_activate(monkeypatch):
    user = get_user_model().objects.create_user("case-lock-staff", password="secret", is_staff=True)
    AppConfig.objects.create(pk=1, pricing_engine_mode="legacy")
    from wishlist.models import Event
    from datetime import date
    event = Event.objects.create(name='Reference', date=date(2027, 1, 1), location='X')
    case = PricingReferenceCase.objects.create(
        name="baseline", result_json={"event_id": event.pk, "v1_gross": "100", "v2_gross": "100", "difference": "0"},
        expected_v1_total=100, expected_v2_total=100, expected_difference=0,
    )
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    client.get(reverse("dj_admin:pricing_v2_activation"))
    token = client.cookies["csrftoken"].value
    old_stamp = validate_activation().reference_case_stamp
    lock_calls = []
    original = AppConfig.objects.select_for_update
    monkeypatch.setattr(AppConfig.objects, "select_for_update", lambda *args, **kwargs: (lock_calls.append(True) or original(*args, **kwargs)))

    response = client.post(
        reverse("dj_admin:pricing_v2_reference_cases"),
        data=json.dumps({"name": "baseline-2", "result": {"event_id": event.pk, "v1_gross": "100", "v2_gross": "101", "difference": "1"},
                         "expected_v1_total": "100", "expected_v2_total": "101", "expected_difference": "1"}),
        content_type="application/json", HTTP_X_CSRFTOKEN=token,
    )
    assert response.status_code == 201
    assert lock_calls
    stale = client.post(reverse("dj_admin:pricing_v2_activation_api"),
        data=json.dumps({"action": "activate", "reference_case_stamp": old_stamp}),
        content_type="application/json", HTTP_X_CSRFTOKEN=token)
    assert stale.status_code == 409
    assert AppConfig.objects.get(pk=1).pricing_engine_mode == "legacy"


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
