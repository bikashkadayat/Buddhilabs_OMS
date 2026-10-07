"""
Create the Inventory Officer group (Phase 70.9).

`roles.is_inventory_officer()` asks whether a user is in a group named "Inventory
Officer". Until that group EXISTS, it is not in the admin's group picker, so there
is no way to appoint anyone to it - the check would sit in the code answering False
forever and the store would silently be run by whoever happens to be a manager.

Creating the group grants nobody anything. It is empty until an administrator puts
somebody in it, which is the point: appointing the storekeeper is a decision for
the organisation, not for a migration.

Reversible - dropping the group on rollback removes the appointment rather than
any data, and re-running this migration brings it back (memberships are lost,
which is correct: rolling back Phase 70 means there is no lifecycle to officiate).
"""
from django.conf import settings
from django.db import migrations

GROUP = getattr(settings, "INVENTORY_OFFICER_GROUP", "Inventory Officer")


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP)


def drop_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0006_seed_default_categories"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(create_group, drop_group)]
