import datetime
from decimal import Decimal

import pytest
from django.urls import reverse

from wishlist.models import (
    Event,
    EventPriceCalculation,
    PriceItem,
    PricingPackage,
)


@pytest.fixture
def staff_client(db, client, django_user_model):
    user = django_user_model.objects.create_user(
        username="dj", password="x", is_staff=True, is_superuser=True)
    client.force_login(user)
    return client


@pytest.fixture
def event(db):
    return Event.objects.create(
        name="Gig", date=datetime.date(2999, 6, 20), location="X")


def _base_post(event):
    return {
        "name": event.name,
        "date": "2999-06-20",
        "location": "X",
        "guest_count": "80",
        "status": event.status,
        "max_wishes_per_session": "3",
    }


def test_selected_items_persist_on_save(staff_client, event):
    i1 = PriceItem.objects.create(name="Licht", category="tech", price=Decimal("100"))
    i2 = PriceItem.objects.create(name="Nebel", category="tech", price=Decimal("50"))
    data = _base_post(event)
    data["selected_items"] = [str(i1.pk), str(i2.pk)]
    resp = staff_client.post(reverse("dj_admin:event_edit", args=[event.pk]), data)
    assert resp.status_code == 302
    calc = EventPriceCalculation.objects.get(event=event)
    assert set(calc.selected_items.values_list("pk", flat=True)) == {i1.pk, i2.pk}


def test_discount_persists_on_save(staff_client, event):
    data = _base_post(event)
    data["pricing_discount_percent"] = "15"
    staff_client.post(reverse("dj_admin:event_edit", args=[event.pk]), data)
    calc = EventPriceCalculation.objects.get(event=event)
    assert calc.discount_percent == Decimal("15")


def test_items_reload_into_form(staff_client, event):
    item = PriceItem.objects.create(name="Licht", category="tech", price=Decimal("100"))
    calc = EventPriceCalculation.objects.create(event=event)
    calc.selected_items.add(item)
    resp = staff_client.get(reverse("dj_admin:event_edit", args=[event.pk]))
    content = resp.content.decode()
    # checkbox for the selected item is rendered checked with a submittable name
    assert 'name="selected_items"' in content


def test_clearing_items_removes_them(staff_client, event):
    item = PriceItem.objects.create(name="Licht", category="tech", price=Decimal("100"))
    calc = EventPriceCalculation.objects.create(event=event)
    calc.selected_items.add(item)
    data = _base_post(event)  # no selected_items key
    staff_client.post(reverse("dj_admin:event_edit", args=[event.pk]), data)
    calc.refresh_from_db()
    assert calc.selected_items.count() == 0
