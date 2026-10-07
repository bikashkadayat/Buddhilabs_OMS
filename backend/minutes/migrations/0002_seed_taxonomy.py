"""
Seed the minute taxonomy: types, sub types, priorities and workflow roles.

Phases 31-C and 35 require these to be data rather than code, so they arrive as rows
an administrator can extend. This migration only establishes a working starting set -
it uses get_or_create keyed on `code`, so re-running it never duplicates and never
overwrites a name an administrator has since edited.

The reverse is deliberately a no-op rather than a delete. Unapplying this migration on
a database where minutes already reference these rows would either fail on PROTECT or
cascade real records away; leaving the taxonomy in place is the safe direction.

Historical models only, no imports from minutes.models: a migration must keep working
when the model file has moved on. Note that historical models carry FIELDS but not
METHODS or class attributes - so nothing here may call a model helper or read
Status.DRAFT off the class. That mistake was made in the memo module's migration 0010
(it called user.get_full_name()) and only a rehearsal against a copy of the real
database caught it, because a fresh test database had no rows to exercise the path.
"""
from django.db import migrations

MINUTE_TYPES = [
    ("board-meeting", "Board Meeting", 10, [
        ("board-regular", "Regular Board Meeting", 10),
        ("board-special", "Special Board Meeting", 20),
        ("board-agm", "Annual General Meeting", 30),
    ]),
    ("management-committee", "Management Committee", 20, [
        ("mc-regular", "Regular Management Meeting", 10),
        ("mc-review", "Performance Review", 20),
        ("mc-budget", "Budget Review", 30),
    ]),
    ("audit-committee", "Audit Committee", 30, [
        ("audit-internal", "Internal Audit Review", 10),
        ("audit-external", "External Audit Review", 20),
        ("audit-compliance", "Compliance Review", 30),
    ]),
    ("governance", "Governance Meeting", 40, [
        ("gov-policy", "Policy Review", 10),
        ("gov-risk", "Risk Review", 20),
    ]),
    ("departmental", "Departmental Meeting", 50, [
        ("dept-weekly", "Weekly Departmental", 10),
        ("dept-project", "Project Review", 20),
    ]),
    ("general", "General Meeting", 60, [
        ("general-other", "Other", 10),
    ]),
]

# rank orders them; sla_days is the per-step allowance the ageing calculation uses,
# which is why Urgent is one day and Normal is a working week.
PRIORITIES = [
    ("normal", "Normal", 0, 5),
    ("medium", "Medium", 1, 3),
    ("high", "High", 2, 2),
    ("urgent", "Urgent", 3, 1),
]

# code, name, rank, is_final_approval, requires_remarks
#
# Reviewer requires remarks because a review that says nothing is not a review. The
# ranks leave gaps (10/20/30/40) so a role can be inserted between two existing ones
# without renumbering the rest.
ROLES = [
    ("reviewer", "Reviewer", 10, False, True),
    ("recommender", "Recommender", 20, False, False),
    ("supporter", "Supporter", 30, False, False),
    ("approver", "Approver", 40, True, False),
]


def seed(apps, schema_editor):
    MinuteType = apps.get_model("minutes", "MinuteType")
    MinuteSubType = apps.get_model("minutes", "MinuteSubType")
    MinutePriority = apps.get_model("minutes", "MinutePriority")
    MinuteRole = apps.get_model("minutes", "MinuteRole")

    for code, name, ordering, sub_types in MINUTE_TYPES:
        minute_type, _ = MinuteType.objects.get_or_create(
            code=code, defaults={"name": name, "ordering": ordering})
        for sub_code, sub_name, sub_ordering in sub_types:
            MinuteSubType.objects.get_or_create(
                code=sub_code,
                defaults={"name": sub_name, "ordering": sub_ordering,
                          "minute_type": minute_type})

    for code, name, rank, sla_days in PRIORITIES:
        MinutePriority.objects.get_or_create(
            code=code,
            defaults={"name": name, "rank": rank, "sla_days": sla_days,
                      "ordering": rank * 10})

    for code, name, rank, is_final, requires_remarks in ROLES:
        MinuteRole.objects.get_or_create(
            code=code,
            defaults={"name": name, "rank": rank, "ordering": rank,
                      "is_final_approval": is_final,
                      "requires_remarks": requires_remarks})


def unseed(apps, schema_editor):
    """
    Intentionally does nothing. See the module docstring: deleting taxonomy that live
    minutes point at would either fail on PROTECT or destroy records.
    """


class Migration(migrations.Migration):
    dependencies = [("minutes", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
