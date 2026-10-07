"""
Seed the ten categories Phase 70.3 names.

A data migration rather than a fixture, so a deployment that runs `migrate` gets
them without a second manual step - and idempotent, so an environment that already
has some of them keeps what it has. Nothing is renamed or deleted: an organisation
with its own categories keeps them alongside.

Reversible as a no-op. Deleting categories on rollback would orphan every asset
pointing at one, which is a worse outcome than leaving ten unused rows behind.
"""
from django.db import migrations

CATEGORIES = [
    ("Laptop", "Portable computers issued to staff"),
    ("Desktop", "Fixed workstations"),
    ("Monitor", "Displays and screens"),
    ("Printer", "Printers, scanners and multifunction devices"),
    ("Projector", "Projectors and presentation equipment"),
    ("Network Device", "Routers, switches, access points and firewalls"),
    ("Mobile Device", "Phones, tablets and dongles"),
    ("Furniture", "Desks, chairs, cabinets and fittings"),
    ("Accessories", "Chargers, bags, keyboards, mice and cables"),
    ("Other", "Anything not covered by the categories above"),
]


def seed(apps, schema_editor):
    InventoryCategory = apps.get_model("inventory", "InventoryCategory")
    for name, description in CATEGORIES:
        InventoryCategory.objects.get_or_create(
            name=name, defaults={"description": description})


def unseed(apps, schema_editor):
    # Deliberately a no-op - see the module docstring.
    pass


class Migration(migrations.Migration):
    dependencies = [("inventory", "0005_assetlifecycleevent_assetrequest_assetreturn_and_more")]
    operations = [migrations.RunPython(seed, unseed)]
