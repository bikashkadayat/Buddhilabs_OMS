"""
Phase APM-03b — the two model deltas the appraisal UX needs.

1. `promotion_recommended` (boolean) becomes `promotion_readiness` (three
   words plus a blank). The boolean forced a supervisor to answer yes or no to
   a question whose honest answer is usually "yes, once X", and it made "never
   considered" indistinguishable from "not ready".

2. `TrainingPlan.kind` and `mentor`. Everything was previously "training", so
   a mentoring pairing could only be expressed by writing the word in the title
   and HR's training list silently included items nobody had to fund.

The boolean's data is CARRIED ACROSS rather than dropped: add, copy, remove, in
that order and inside one migration, so no deployment ever sees a record whose
recommendation has gone missing. True becomes READY, False becomes
DEVELOPMENT_REQUIRED, NULL becomes the blank — which is the mapping that
preserves the one distinction the old column could express.
"""
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings

READY = "ready"
DEVELOPMENT_REQUIRED = "development_required"


def carry_forward(apps, schema_editor):
    Appraisal = apps.get_model("appraisal", "Appraisal")
    Appraisal.objects.filter(promotion_recommended=True).update(
        promotion_readiness=READY)
    Appraisal.objects.filter(promotion_recommended=False).update(
        promotion_readiness=DEVELOPMENT_REQUIRED)
    # NULL already maps to the field's "" default — the absence is preserved by
    # doing nothing, which is the point of it being a blank rather than a value.


def carry_back(apps, schema_editor):
    """
    Reversible, but LOSSY, and deliberately so.

    Going back collapses READY_WITH_DEVELOPMENT into True: the middle answer
    has no boolean, and mapping it to False would turn "recommended, with
    development" into "not recommended" on somebody's permanent record. Losing
    the nuance is recoverable; inverting the verdict is not.
    """
    Appraisal = apps.get_model("appraisal", "Appraisal")
    Appraisal.objects.filter(promotion_readiness=DEVELOPMENT_REQUIRED).update(
        promotion_recommended=False)
    Appraisal.objects.exclude(
        promotion_readiness__in=["", DEVELOPMENT_REQUIRED]).update(
        promotion_recommended=True)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("appraisal", "0002_seed_competencies"),
    ]

    operations = [
        migrations.AddField(
            model_name="appraisal",
            name="promotion_readiness",
            field=models.CharField(
                blank=True, db_index=True, default="", max_length=24,
                choices=[
                    ("ready", "Ready"),
                    ("ready_with_development", "Ready With Development"),
                    ("development_required", "Development Required"),
                ]),
        ),
        migrations.RunPython(carry_forward, carry_back),
        migrations.RemoveField(
            model_name="appraisal",
            name="promotion_recommended",
        ),
        migrations.AddField(
            model_name="trainingplan",
            name="kind",
            field=models.CharField(
                db_index=True, default="training", max_length=16,
                choices=[
                    ("training", "Training"),
                    ("certification", "Certification"),
                    ("mentorship", "Mentorship"),
                    ("on_the_job", "On-the-job"),
                ]),
        ),
        migrations.AddField(
            model_name="trainingplan",
            name="mentor",
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="mentorship_plans",
                to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="trainingplan",
            name="mentor_name",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
    ]
