"""
Backfill Memo.department / department_name from each memo's author.

Memo.save() snapshots the department at creation, but that only helps memos
created from now on. Without this backfill every pre-existing memo would have an
empty department, so the "Department Memo" menu and the archive's Department
column would read blank for the entire existing history - and a department head
would see nothing in a menu that is supposed to show their unit's traffic.

Done in bulk batches keyed on the author's department rather than row by row:
the memo table is the one that grows without bound here, and a per-row save()
would be one UPDATE per memo.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    Memo = apps.get_model("memos", "Memo")
    User = apps.get_model("users", "User")

    # Only authors who actually have a department are worth iterating.
    authors = User.objects.exclude(
        department_ref__isnull=True, department__isnull=True,
    ).values("id", "department_ref_id", "department")

    for author in authors.iterator(chunk_size=500):
        label = author["department"] or ""
        if author["department_ref_id"] and not label:
            Department = apps.get_model("leaves", "Department")
            found = Department.objects.filter(pk=author["department_ref_id"]).first()
            label = found.name if found else ""

        Memo.objects.filter(
            created_by_id=author["id"], department__isnull=True,
        ).update(
            department_id=author["department_ref_id"],
            department_name=label,
        )


def unbackfill(apps, schema_editor):
    """Reverse cleanly so the migration is not a one-way door in development."""
    Memo = apps.get_model("memos", "Memo")
    Memo.objects.update(department=None, department_name="")


class Migration(migrations.Migration):

    dependencies = [
        ("memos", "0007_memoattachment_memoworkflowstep_memo_approved_at_and_more"),
        ("leaves", "0017_merge_20260806_1728"),
        ("users", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
