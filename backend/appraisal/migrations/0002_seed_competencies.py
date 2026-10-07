"""
Seed the ten competencies the specification names.

A data migration rather than a fixture: the catalogue is part of the module
working at all — an appraisal with nothing to rate is not an appraisal — and a
fixture somebody has to remember to load is one that will be missing on the
deployment where it matters.

Idempotent by `code`, and the reverse is a no-op: unseeding would orphan the
ratings that reference these rows, and a migration that can destroy somebody's
appraisal record to tidy a lookup table is not a migration worth having.
"""
from django.db import migrations

COMPETENCIES = [
    ("leadership", "Leadership",
     "Setting direction, and taking responsibility for outcomes beyond one's "
     "own work."),
    ("communication", "Communication",
     "Being understood, in writing and in person, by the people who need to "
     "act on it."),
    ("teamwork", "Teamwork",
     "Working with others so the whole is better, including across teams."),
    ("innovation", "Innovation",
     "Finding better ways of doing things, and making the case for them."),
    ("problem_solving", "Problem Solving",
     "Getting to the real cause and dealing with it, rather than the symptom."),
    ("accountability", "Accountability",
     "Owning commitments and outcomes, including when they go wrong."),
    ("initiative", "Initiative",
     "Acting without being asked, within the bounds of the role."),
    ("technical_skills", "Technical Skills",
     "The craft the role actually requires, and keeping it current."),
    ("time_management", "Time Management",
     "Prioritising, and being realistic about what fits."),
    ("attendance_reliability", "Attendance Reliability",
     "Being where colleagues can depend on you being."),
]


def seed(apps, schema_editor):
    Competency = apps.get_model("appraisal", "Competency")
    for position, (code, name, description) in enumerate(COMPETENCIES, start=1):
        Competency.objects.update_or_create(
            code=code,
            defaults={"name": name, "description": description,
                      "ordering": position * 10, "is_active": True},
        )


def unseed(apps, schema_editor):
    """Deliberately a no-op. See the module docstring."""


class Migration(migrations.Migration):
    dependencies = [("appraisal", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
