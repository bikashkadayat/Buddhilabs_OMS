from django.db import migrations

SEED = [
    ("Electronics", "Laptops, monitors, projectors, peripherals"),
    ("Furniture", "Desks, chairs, cabinets"),
    ("Networking", "Routers, switches, access points"),
    ("Office Supplies", "Stationery and consumables"),
    ("Vehicles", "Official vehicles and accessories"),
]


def seed(apps, schema_editor):
    Category = apps.get_model("inventory", "InventoryCategory")
    for name, desc in SEED:
        Category.objects.get_or_create(name=name, defaults={"description": desc})


def unseed(apps, schema_editor):
    Category = apps.get_model("inventory", "InventoryCategory")
    Category.objects.filter(name__in=[n for n, _ in SEED]).delete()


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
