"""A serial number must identify exactly one asset.

`InventoryItem.serial_number` carried no uniqueness of any kind, while
`inventory/lifecycle_views.py` resolves a scanned code with
`filter(serial_number__iexact=code).first()`. Two assets sharing a serial
therefore resolved a scan to an arbitrary one, and the holder permission check
that follows then ran against the wrong asset.

The constraint is deliberately shaped:
  * on ``Lower(serial_number)`` — the lookup is ``__iexact``, so without folding
    case "AB12" and "ab12" would both satisfy a plain constraint yet both match
    a single scan;
  * conditional on a non-blank value — the field defaults to "" for assets that
    genuinely have no serial, and a plain ``unique=True`` would allow only one
    such asset to exist at all.
"""
import pytest
from django.db import IntegrityError, transaction

from inventory.models import InventoryItem


@pytest.mark.django_db
def test_two_assets_cannot_share_a_serial_number(eng):
    InventoryItem.objects.create(
        asset_code="NIF-INV-S001", name="Laptop A", department=eng,
        serial_number="SN-DUPLICATE-1")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryItem.objects.create(
                asset_code="NIF-INV-S002", name="Laptop B", department=eng,
                serial_number="SN-DUPLICATE-1")


@pytest.mark.django_db
def test_serial_uniqueness_is_case_insensitive(eng):
    """Case must fold, because the scan lookup is __iexact."""
    InventoryItem.objects.create(
        asset_code="NIF-INV-S003", name="Laptop C", department=eng,
        serial_number="SN-Case-2")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            InventoryItem.objects.create(
                asset_code="NIF-INV-S004", name="Laptop D", department=eng,
                serial_number="sn-case-2")


@pytest.mark.django_db
def test_many_assets_may_have_no_serial(eng):
    """The blank default must stay usable — this is why the constraint is partial."""
    for n in range(3):
        InventoryItem.objects.create(
            asset_code=f"NIF-INV-B{n:03d}", name=f"Unserialised {n}", department=eng)

    assert InventoryItem.objects.filter(serial_number="").count() == 3


@pytest.mark.django_db
def test_distinct_serials_are_unaffected(eng):
    a = InventoryItem.objects.create(
        asset_code="NIF-INV-S005", name="Laptop E", department=eng, serial_number="SN-A")
    b = InventoryItem.objects.create(
        asset_code="NIF-INV-S006", name="Laptop F", department=eng, serial_number="SN-B")
    assert a.pk != b.pk
