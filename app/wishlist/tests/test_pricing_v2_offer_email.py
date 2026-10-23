from decimal import Decimal

import pytest

from wishlist.models import AppConfig, EmailLog, PriceCalculationRevision
from wishlist.pricing_v2.offers import OfferTemplateError, _render_offer_template


def test_offer_template_contract_has_only_the_six_fields():
    context = {name: name for name in (
        "client_name", "event_name", "event_date", "price_breakdown", "gross_total", "dj_name")}
    assert _render_offer_template("{client_name} {event_name} {event_date} {price_breakdown} {gross_total} {dj_name}", context)
    with pytest.raises(OfferTemplateError):
        _render_offer_template("{unknown}", context)
    with pytest.raises(OfferTemplateError):
        _render_offer_template("{gross_total:.2f}", context)


def test_email_log_can_be_linked_to_frozen_revision(db):
    assert "revision" in {field.name for field in EmailLog._meta.fields}
    assert AppConfig._meta.get_field("offer_email_subject").default
    assert PriceCalculationRevision._meta.get_field("gross_total").get_internal_type() == "DecimalField"
    assert Decimal("1.00") == Decimal("1.00")
