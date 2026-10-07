"""
Add MemoWorkflowStep.activated_at and .assignee_name, and make .assignee
nullable (SET_NULL instead of PROTECT).

Hand-written and kept ahead of 0010 so the legacy-routing conversion there can
populate both new columns as it builds each step. Autogenerating these together
with the field removals in 0011 would have placed them after the data migration
that needs them.

The assignee change matters beyond tidiness: PROTECT made any user who had ever
appeared in a memo workflow permanently undeletable, which broke the admin
console's account deletion outright. With the name snapshotted on the row,
SET_NULL keeps the approval record complete and legible while letting the account
go.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("memos", "0008_backfill_memo_department"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="memoworkflowstep",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="memoworkflowstep",
            name="assignee_name",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
        migrations.AlterField(
            model_name="memoworkflowstep",
            name="assignee",
            field=models.ForeignKey(
                blank=True,
                help_text="SET_NULL, not CASCADE: deleting a user must never erase "
                          "a row of a completed approval matrix. The row survives "
                          "with its assignee_name snapshot intact.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="memo_workflow_steps",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
