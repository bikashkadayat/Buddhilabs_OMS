"""
Retire the approval chain and the four registers; adopt the E-minute manual's workflow.

Hand-ordered rather than left as the autodetector emitted it, for two reasons:

  * the new columns must exist, and the data step must have run, BEFORE `meeting_date`
    and `meeting_time` become NOT NULL - otherwise the alter fails on any existing row
    holding NULL;
  * the retired models are dropped with DeleteModel in child-to-parent order rather
    than by removing their fields one at a time. The generated order stripped fields
    off tables whose own indexes still referenced them, which SQLite cannot do: it
    rebuilds the table and then fails recreating an index over a column that has just
    gone.
"""
import datetime

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


# ---------------------------------------------------------------------------
# Carrying the old shape forward
# ---------------------------------------------------------------------------
# Three things have to happen to existing rows before the old columns are dropped:
#
#   1. `meeting_date` and `meeting_time` become required. They were nullable, so a row
#      holding NULL would break the NOT NULL alter. The minute's own `minute_date` is
#      the best available answer for the date and midnight for the time - both are
#      visible in the UI and can be corrected, which a failed deployment cannot.
#   2. `background` and `analysis` are dropped. Their text is authored content, so it
#      is carried into `agenda_body` rather than deleted - a minute that recorded a
#      discussion still records it after this runs.
#   3. The status vocabulary shrinks from nine values to four. Every retired value is
#      mapped to the state that means the same thing under the new workflow.
STATUS_MAP = {
    # Mid-approval under the old chain: still in progress, nobody has acknowledged.
    "under_review": "draft_for_review",
    "recommended": "draft_for_review",
    "supported": "draft_for_review",
    # Approval used to be what OPENED the acknowledgement round, so an approved minute
    # is exactly one that is now awaiting acknowledgement.
    "approved": "pending_acknowledgement",
    # Returned to its author - the state its author can edit.
    "rejected": "draft",
    # Withdrawn minutes are closed records. There is no cancelled state any more, and
    # archiving them keeps them readable rather than resurrecting them as live drafts.
    "cancelled": "archived",
}


def carry_forward(apps, schema_editor):
    Minute = apps.get_model("minutes", "Minute")
    MinuteParticipant = apps.get_model("minutes", "MinuteParticipant")

    for minute in Minute.objects.all().iterator():
        fields = []
        if minute.meeting_date is None:
            minute.meeting_date = minute.minute_date
            fields.append("meeting_date")
        if minute.meeting_time is None:
            minute.meeting_time = datetime.time(0, 0)
            fields.append("meeting_time")

        carried = "\n".join(
            part for part in (minute.background, minute.analysis) if part)
        if carried and not minute.agenda_body:
            minute.agenda_body = carried
            fields.append("agenda_body")

        mapped = STATUS_MAP.get(minute.status)
        if mapped:
            minute.status = mapped
            fields.append("status")

        if fields:
            minute.save(update_fields=fields)

    # Attendance absorbs the old participant_role: an invitee was a ROLE before and is
    # an attendance group now. "Excused" is gone - for the purpose of who must
    # acknowledge, excused and absent are the same thing.
    for row in MinuteParticipant.objects.all().iterator():
        fields = []
        attendance = row.attendance
        if row.participant_role == "invitee":
            attendance = "invitee"
        elif attendance == "excused":
            attendance = "absent"
        if attendance != row.attendance:
            row.attendance = attendance
            fields.append("attendance")

        # Only members present are asked; everyone else is Not Required. A declined
        # acknowledgement becomes pending, because declining no longer exists and the
        # honest reading is that they have not acknowledged.
        if attendance != "present":
            ack = "not_required"
        elif row.ack_status in ("declined", "pending"):
            ack = "pending"
        else:
            ack = row.ack_status
        if ack != row.ack_status:
            row.ack_status = ack
            fields.append("ack_status")

        if fields:
            row.save(update_fields=fields)


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0017_merge_20260806_1728'),
        ('minutes', '0007_alter_minuteauditlog_action'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- 1. the new columns, so the data step has somewhere to write ---
        migrations.AddField(
            model_name='minute',
            name='agenda_body',
            field=models.TextField(blank=True, help_text='Sanitized HTML. The agenda, discussion and decisions table.'),
        ),
        migrations.AddField(
            model_name='minute',
            name='fro',
            field=models.ForeignKey(blank=True, help_text='First reporting officer. Receives the minute on Submit for Draft Review, and may access it throughout.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='minutes_as_fro', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='minute',
            name='fro_name',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='minute',
            name='sent_for_review_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # --- 2. carry the data across while both shapes still exist ---
        migrations.RunPython(carry_forward, migrations.RunPython.noop),

        # --- 3. indexes that name columns about to go ---
        migrations.RemoveIndex(
            model_name='minute',
            name='minutes_min_departm_9cbe8a_idx',
        ),
        migrations.RemoveIndex(
            model_name='minuteparticipant',
            name='minutes_min_minute__536695_idx',
        ),
        migrations.AlterModelOptions(
            name='minuteparticipant',
            options={'ordering': ['attendance', 'user_name']},
        ),

        # --- 4. the surviving columns take their new shape ---
        migrations.AlterField(
            model_name='minute',
            name='acknowledgement_due_days',
            field=models.PositiveSmallIntegerField(default=7, help_text='Days present members have to acknowledge once the round opens.'),
        ),
        migrations.AlterField(
            model_name='minute',
            name='created_by',
            field=models.ForeignKey(help_text="The initiator - 'Initiated By' in the archive list.", on_delete=django.db.models.deletion.CASCADE, related_name='minutes_initiated', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='minute',
            name='department_name',
            field=models.CharField(blank=True, help_text='Snapshot, so a renamed department does not rewrite history on an archived minute.', max_length=120),
        ),
        migrations.AlterField(
            model_name='minute',
            name='meeting_date',
            field=models.DateField(default=django.utils.timezone.localdate, help_text='Date* - the date of the meeting.'),
        ),
        migrations.AlterField(
            model_name='minute',
            name='meeting_time',
            field=models.TimeField(help_text='Time* - the meeting start time.'),
        ),
        migrations.AlterField(
            model_name='minute',
            name='minute_type',
            field=models.ForeignKey(help_text='DEPARTMENT / BRANCH / MANCOM / OTHERS.', on_delete=django.db.models.deletion.PROTECT, related_name='minutes', to='minutes.minutetype'),
        ),
        migrations.AlterField(
            model_name='minute',
            name='reference_number',
            field=models.CharField(blank=True, help_text="The previous meeting's minute reference, if this minute cites one.", max_length=64),
        ),
        migrations.AlterField(
            model_name='minute',
            name='status',
            field=models.CharField(choices=[('draft', 'Draft'), ('draft_for_review', 'Draft For Review'), ('pending_acknowledgement', 'Pending Acknowledgement'), ('archived', 'Archived')], db_index=True, default='draft', max_length=24),
        ),
        migrations.AlterField(
            model_name='minute',
            name='subject',
            field=models.CharField(blank=True, help_text="Optional. The manual's form has no subject; lists identify a minute by type and meeting date.", max_length=255),
        ),
        migrations.AlterField(
            model_name='minuteauditlog',
            name='action',
            field=models.CharField(choices=[('created', 'Created'), ('updated', 'Edited'), ('sent_for_review', 'Submitted for draft review'), ('review_returned', 'Returned by reviewer'), ('sent_for_ack', 'Submitted for acknowledgement'), ('acknowledged', 'Acknowledged'), ('ack_reminded', 'Acknowledgement reminder sent'), ('participants_changed', 'Participants updated'), ('archived', 'Archived'), ('deleted', 'Deleted'), ('exported', 'Exported')], db_index=True, max_length=24),
        ),
        migrations.AlterField(
            model_name='minuteparticipant',
            name='ack_status',
            field=models.CharField(choices=[('not_required', 'Not Required'), ('pending', 'Pending Acknowledgement'), ('acknowledged', 'Acknowledged')], db_index=True, default='not_required', max_length=16),
        ),
        migrations.AlterField(
            model_name='minuteparticipant',
            name='attendance',
            field=models.CharField(choices=[('present', 'Present'), ('absent', 'Absent'), ('invitee', 'Invitee')], db_index=True, default='present', max_length=12),
        ),
        migrations.AlterField(
            model_name='minuteparticipant',
            name='reminded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),

        # --- 5. the columns the manual has no use for ---
        migrations.RemoveField(
            model_name='minuteparticipant',
            name='participant_role',
        ),
        migrations.RemoveField(model_name='minute', name='priority'),
        migrations.RemoveField(model_name='minute', name='minute_sub_type'),
        migrations.RemoveField(model_name='minute', name='analysis'),
        migrations.RemoveField(model_name='minute', name='approved_at'),
        migrations.RemoveField(model_name='minute', name='background'),
        migrations.RemoveField(model_name='minute', name='cc_users'),
        migrations.RemoveField(model_name='minute', name='meeting_end_time'),
        migrations.RemoveField(model_name='minute', name='meeting_title'),
        migrations.RemoveField(model_name='minute', name='minute_date'),
        migrations.RemoveField(model_name='minute', name='submitted_at'),
        migrations.RemoveField(model_name='minute', name='to_users'),
        migrations.RemoveField(model_name='minute', name='venue'),
        migrations.AddIndex(
            model_name='minute',
            index=models.Index(fields=['minute_type', '-meeting_date'], name='minutes_min_minute__1d78f2_idx'),
        ),

        # --- 6. the retired tables, children first ---
        migrations.DeleteModel(name='MinuteActionItem'),
        migrations.DeleteModel(name='MinuteDecision'),
        migrations.DeleteModel(name='MinuteAgendaItem'),
        migrations.DeleteModel(name='MinuteWorkflowStep'),
        migrations.DeleteModel(name='MinuteChildSequence'),
        migrations.DeleteModel(name='MinuteRole'),
        migrations.DeleteModel(name='MinutePriority'),
        migrations.DeleteModel(name='MinuteSubType'),
    ]
