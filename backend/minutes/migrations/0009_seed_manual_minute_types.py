"""
Seed the Minute Type vocabulary the E-minute manual uses.

The manual's dropdown is "DEPARTMENT/BRANCH/MANCOM etc." (p.4). The original seed
(0002) carried a different, longer taxonomy - Board Meeting, Management Committee,
Audit Committee and so on - which is a reasonable set but not this one.

Both survive. The manual's four are added, and 0002's rows are left in place and
active rather than deleted: a minute already raised against "Board Meeting" must keep
reading correctly, and MinuteType is PROTECTed against deletion precisely so history
cannot be rewritten by tidying the lookup table. An administrator who wants the older
entries gone can deactivate them in the admin, which hides them from the dropdown
without touching the minutes that cite them.
"""
from django.db import migrations

# code, label, ordering - ordered as the manual lists them.
#
# Deliberately ordered AHEAD of 0002's older taxonomy (which starts at 10): the form
# defaults to the first active type, and defaulting a Sanima minute to "Board Meeting"
# because it happened to sort first alphabetically is a wrong answer offered by
# default. The older entries remain available further down the list.
MANUAL_TYPES = [
    ("department", "DEPARTMENT", 1),
    ("branch", "BRANCH", 2),
    ("mancom", "MANCOM", 3),
    ("others", "OTHERS", 4),
]


def seed(apps, schema_editor):
    MinuteType = apps.get_model("minutes", "MinuteType")
    for code, name, ordering in MANUAL_TYPES:
        MinuteType.objects.update_or_create(
            code=code,
            defaults={"name": name, "ordering": ordering, "is_active": True},
        )


def unseed(apps, schema_editor):
    """
    Reversible only where nothing cites them. A type in use is PROTECTed, so this
    deletes the unused ones and leaves the rest - an unapplied migration must not take
    a minute's type out from under it.
    """
    MinuteType = apps.get_model("minutes", "MinuteType")
    for code, _name, _ordering in MANUAL_TYPES:
        row = MinuteType.objects.filter(code=code).first()
        if row is not None and not row.minutes.exists():
            row.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("minutes", "0008_pdf_workflow_no_approval_chain"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
