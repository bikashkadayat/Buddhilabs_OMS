from django.db import migrations

# Phase 2.6: organization departments. Seeded idempotently by code so re-runs
# and existing data are safe.
DEPARTMENTS = [
    ("ICT", "ICT Department"),
    ("ADMIN", "Administrative Department"),
    ("OPS", "Operational Department"),
    ("HR", "Human Resource Department"),
]


def seed_departments(apps, schema_editor):
    Department = apps.get_model("leaves", "Department")
    for code, name in DEPARTMENTS:
        Department.objects.get_or_create(code=code, defaults={"name": name, "is_active": True})


def unseed_departments(apps, schema_editor):
    # Reverse is a no-op: departments may be referenced by users/leaves, so we
    # never delete them automatically.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("leaves", "0008_leave_department_head_action_date_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_departments, unseed_departments),
    ]
