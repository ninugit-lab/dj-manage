import json

import pytest
from django.urls import reverse

from wishlist.models import PricingWorkflow


@pytest.fixture
def staff_client(db, client, django_user_model):
    user = django_user_model.objects.create_user(
        username="dj", password="x", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


def test_workflow_blocks_embedded_as_valid_json(staff_client):
    wf = PricingWorkflow.objects.create(
        name="WF", is_active=True,
        workflow_json=[{"type": "package", "label": "Paket", "config": {"package_id": 1}}])
    resp = staff_client.get(reverse("dj_admin:workflow_builder"))
    content = resp.content.decode()
    assert 'id="wf-blocks-data"' in content
    # extract json_script payload and ensure it parses + contains our workflow
    start = content.index('id="wf-blocks-data"')
    snippet = content[start:start + 600]
    payload = snippet.split('>', 1)[1].split('</script>', 1)[0]
    data = json.loads(payload)
    assert str(wf.pk) in data
    assert data[str(wf.pk)][0]["config"]["package_id"] == 1


def test_empty_workflow_list_renders(staff_client):
    resp = staff_client.get(reverse("dj_admin:workflow_builder"))
    assert resp.status_code == 200
    assert 'id="wf-blocks-data"' in resp.content.decode()


def test_dj_admin_declares_dark_color_scheme_for_native_form_controls(staff_client):
    # Without an explicit dark color-scheme, browsers render native <select>
    # dropdown popups (and other form controls) with their light UA theme,
    # so the page's light text color becomes nearly invisible against it.
    resp = staff_client.get(reverse("dj_admin:workflow_builder"))
    assert "color-scheme:dark" in resp.content.decode().replace(" ", "")


def test_month_select_uses_german_month_names_not_placeholders(staff_client):
    resp = staff_client.get(reverse("dj_admin:workflow_builder"))
    content = resp.content.decode()
    start = content.index('id="tp-month"')
    end = content.index('</select>', start)
    snippet = content[start:end]
    assert "Monat 1" not in snippet
    assert "Monat 9" not in snippet
    for name in [
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ]:
        assert name in snippet
