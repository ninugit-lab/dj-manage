import datetime
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from wishlist.models import (
    Event,
    ManualPriceAdjustment,
    PriceCalculationRevision,
    PricingPackage,
    PricingPackageOverride,
    PricingProfile,
    PricingWorkflow,
)


def create_event(name="Test Event"):
    return Event.objects.create(
        name=name,
        date=datetime.date(2999, 1, 1),
        location="Test Location",
    )


def create_profile(name="Standard", version=1, status=PricingProfile.Status.DRAFT):
    return PricingProfile.objects.create(
        name=name,
        version=version,
        status=status,
        valid_from=timezone.now(),
        config_json={},
    )


def create_revision(event, revision_number, offer_number=None, **snapshot_versions):
    return PriceCalculationRevision.objects.create(
        event=event,
        kind=PriceCalculationRevision.Kind.DRAFT,
        status=PriceCalculationRevision.Status.DRAFT,
        revision_number=revision_number,
        offer_number=offer_number,
        engine_version="2.0",
        input_json={},
        result_json={},
        gross_total=Decimal("100.00"),
        **snapshot_versions,
    )


@pytest.mark.django_db
def test_only_one_active_pricing_profile():
    create_profile(name="First", status=PricingProfile.Status.ACTIVE)

    with pytest.raises(IntegrityError), transaction.atomic():
        create_profile(name="Second", status=PricingProfile.Status.ACTIVE)

    assert create_profile(name="Draft", status=PricingProfile.Status.DRAFT)


@pytest.mark.django_db
def test_profile_name_and_version_are_unique():
    create_profile(name="Standard", version=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        create_profile(name="Standard", version=1)

    assert create_profile(name="Standard", version=2)


@pytest.mark.django_db
def test_offer_numbers_are_unique_per_event_while_null_is_allowed():
    event = create_event()
    create_revision(event, revision_number=1, offer_number=1001)

    with pytest.raises(IntegrityError), transaction.atomic():
        create_revision(event, revision_number=2, offer_number=1001)

    create_revision(event, revision_number=3)
    create_revision(event, revision_number=4)
    other_event = create_event("Other Event")
    assert create_revision(other_event, revision_number=1, offer_number=1001)


@pytest.mark.django_db
def test_revision_numbers_are_unique_per_event():
    event = create_event()
    create_revision(event, revision_number=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        create_revision(event, revision_number=1)

    other_event = create_event("Other Event")
    assert create_revision(other_event, revision_number=1)


@pytest.mark.django_db
def test_manual_adjustment_requires_nonempty_reason():
    event = create_event()

    with pytest.raises(IntegrityError), transaction.atomic():
        ManualPriceAdjustment.objects.create(
            event=event,
            label="Correction",
            amount=Decimal("10.00"),
            reason="",
        )

    adjustment = ManualPriceAdjustment(
        event=event,
        label="Correction",
        amount=Decimal("10.00"),
        reason="   ",
    )
    with pytest.raises(ValidationError, match="reason"):
        adjustment.full_clean()


@pytest.mark.django_db
def test_manual_adjustment_rejects_zero_and_accepts_signed_nonzero_amounts():
    event = create_event()

    with pytest.raises(IntegrityError), transaction.atomic():
        ManualPriceAdjustment.objects.create(
            event=event,
            label="No change",
            amount=Decimal("0.00"),
            reason="Required test",
        )

    positive = ManualPriceAdjustment.objects.create(
        event=event,
        label="Surcharge",
        amount=Decimal("10.00"),
        reason="Additional work",
    )
    negative = ManualPriceAdjustment.objects.create(
        event=event,
        label="Discount",
        amount=Decimal("-10.00"),
        reason="Service recovery",
    )
    assert positive.amount == Decimal("10.00")
    assert negative.amount == Decimal("-10.00")


@pytest.mark.django_db
def test_pricing_package_override_unique_per_profile_and_package():
    profile = create_profile()
    package = PricingPackage.objects.create(name="Basic")
    PricingPackageOverride.objects.create(profile=profile, package=package)

    with pytest.raises(IntegrityError), transaction.atomic():
        PricingPackageOverride.objects.create(profile=profile, package=package)


@pytest.mark.django_db
def test_pricing_v2_defaults_and_version_fields():
    event = create_event()
    package = PricingPackage.objects.create(name="Basic")
    workflow = PricingWorkflow.objects.create(name="Default")

    assert event.pricing_region == "NW"
    assert event.setup_minutes is None
    assert event.teardown_minutes is None
    assert package.version == 1
    assert workflow.version == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (PricingPackage, {"name": "Zero package", "version": 0}),
        (PricingWorkflow, {"name": "Zero workflow", "version": 0}),
        (
            PricingProfile,
            {
                "name": "Zero profile",
                "version": 0,
                "valid_from": timezone.now(),
                "config_json": {},
            },
        ),
    ],
    ids=["package", "workflow", "profile"],
)
def test_pricing_model_versions_reject_zero(model, kwargs):
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.create(**kwargs)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("revision_number", "offer_number"),
    [(0, None), (1, 0)],
    ids=["revision-number", "offer-number"],
)
def test_revision_and_nonnull_offer_numbers_reject_zero(
    revision_number, offer_number,
):
    with pytest.raises(IntegrityError), transaction.atomic():
        create_revision(
            create_event(),
            revision_number=revision_number,
            offer_number=offer_number,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "version_field",
    ["profile_version", "package_version", "workflow_version"],
)
def test_nonnull_snapshot_versions_reject_zero(version_field):
    with pytest.raises(IntegrityError), transaction.atomic():
        create_revision(
            create_event(),
            revision_number=1,
            **{version_field: 0},
        )


@pytest.mark.django_db
def test_revision_preserves_nullable_snapshot_identity_fields():
    revision = create_revision(create_event(), revision_number=1)

    assert revision.profile_id_snapshot is None
    assert revision.profile_version is None
    assert revision.package_id_snapshot is None
    assert revision.package_version is None
    assert revision.workflow_id_snapshot is None
    assert revision.workflow_version is None


@pytest.mark.django_db
def test_only_one_active_default_workflow_but_nondefault_or_inactive_are_allowed():
    PricingWorkflow.objects.create(name="Primary", is_default=True, is_active=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        PricingWorkflow.objects.create(name="Duplicate", is_default=True, is_active=True)
    assert PricingWorkflow.objects.create(name="Inactive", is_default=True, is_active=False)
    assert PricingWorkflow.objects.create(name="Alternate", is_default=False, is_active=True)
