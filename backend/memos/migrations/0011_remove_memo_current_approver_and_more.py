"""
Drop the legacy two-slot routing columns and the SUBMITTED status.

Safe only because 0010 ran first: it converted every memo those columns routed
into MemoWorkflowStep rows and renamed the SUBMITTED status to DRAFT_FOR_REVIEW,
so nothing here removes information that is not already represented in the
matrix. Reversing this migration restores the columns as empty and nullable;
0010's reverse then repopulates them for in-flight memos.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('memos', '0010_convert_legacy_routing'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='memo',
            name='current_approver',
        ),
        migrations.RemoveField(
            model_name='memo',
            name='current_reviewer',
        ),
        migrations.AlterField(
            model_name='memo',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('draft_for_review', 'Draft For Review'), ('under_review', 'Under Review'), ('recommended', 'Recommended'), ('supported', 'Supported'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('archived', 'Archived'), ('cancelled', 'Cancelled')], db_index=True, default='draft', max_length=20),
        ),
    ]
