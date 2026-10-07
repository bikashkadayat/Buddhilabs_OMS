"""
Phase APM-FINAL — the Goal Approval stage, and the goal lifecycle behind it.

WHY THE STAGE EXISTS
--------------------
"The employee has drafted objectives" and "the supervisor has accepted them as
the basis for the year" are different facts. Collapsed into one transition, an
employee could not tell agreed objectives from ones still under discussion, and
the moment of acceptance — the one somebody is actually held to — left no trace
of its own in the record.

WHAT THIS MIGRATION HAS TO GET RIGHT
------------------------------------
Existing goals have no status, and inventing one wrongly rewrites what people
believe they agreed to. The backfill therefore derives each goal's status from
where its APPRAISAL has already reached, which is the same rule the workflow
applies from now on:

    appraisal at Goal Setting          -> DRAFT     (never approved)
    appraisal at Mid-Year Review       -> APPROVED  (agreed, still adjustable)
    appraisal past Mid-Year Review     -> LOCKED    (measured against)

No existing appraisal can be at Goal Approval — the stage did not exist — so
nothing needs to be moved onto it. Appraisals already past goal setting keep
their `goals_agreed_at`, which is what made this backfill decidable at all.
"""
from django.db import migrations, models

DRAFT, APPROVED, LOCKED = "draft", "approved", "locked"

# Ladder positions AFTER this migration.
GOAL_SETTING = "goal_setting"
MID_YEAR = "mid_year_review"
# Everything at or beyond self-assessment has passed the mid-year lock.
PAST_MID_YEAR = ["self_assessment", "supervisor_review", "review_committee",
                 "final_review", "development_plan", "training_plan", "closed"]


def backfill(apps, schema_editor):
    Goal = apps.get_model("appraisal", "Goal")
    Goal.objects.filter(appraisal__status=GOAL_SETTING).update(status=DRAFT)
    Goal.objects.filter(appraisal__status=MID_YEAR).update(status=APPROVED)
    Goal.objects.filter(appraisal__status__in=PAST_MID_YEAR).update(status=LOCKED)


def unbackfill(apps, schema_editor):
    """Nothing to undo: removing the column takes the values with it."""


class Migration(migrations.Migration):

    dependencies = [
        ("appraisal", "0003_promotion_readiness_and_training_kind"),
    ]

    operations = [
        # The stage itself. A CharField choices change is metadata only — no
        # table rewrite — but it must be declared or `makemigrations --check`
        # reports drift on every future run.
        migrations.AlterField(
            model_name="appraisal",
            name="status",
            field=models.CharField(
                db_index=True, default="goal_setting", max_length=24,
                choices=[
                    ("goal_setting", "Goal Setting"),
                    ("goal_approval", "Goal Approval"),
                    ("mid_year_review", "Mid-Year Review"),
                    ("self_assessment", "Self Assessment"),
                    ("supervisor_review", "Supervisor Review"),
                    ("review_committee", "Review Committee"),
                    ("final_review", "Final Review"),
                    ("development_plan", "Development Plan"),
                    ("training_plan", "Training Plan"),
                    ("closed", "Closed"),
                ]),
        ),
        migrations.AddField(
            model_name="goal",
            name="status",
            field=models.CharField(
                db_index=True, default="draft", max_length=10,
                choices=[("draft", "Draft"), ("approved", "Approved"),
                         ("locked", "Locked")]),
        ),
        migrations.RunPython(backfill, unbackfill),
        migrations.AlterField(
            model_name="appraisalauditlog",
            name="action",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("created", "Created"),
                    ("updated", "Updated"),
                    ("goals_submitted", "Goals submitted for approval"),
                    ("goals_agreed", "Goals approved"),
                    ("goals_locked", "Goals locked"),
                    ("goal_changed", "Goal changed"),
                    ("promotion_recorded", "Promotion status changed"),
                    ("mid_year_recorded", "Mid-year review recorded"),
                    ("self_assessed", "Self assessment submitted"),
                    ("supervisor_reviewed", "Supervisor review recorded"),
                    ("committee_reviewed", "Committee review recorded"),
                    ("finalised", "Final review recorded"),
                    ("development_planned", "Development plan agreed"),
                    ("training_planned", "Training plan agreed"),
                    ("returned", "Returned to an earlier stage"),
                    ("closed", "Closed"),
                    ("rated", "Competency rated"),
                    ("evidence_attached", "Evidence attached"),
                ]),
        ),
    ]
