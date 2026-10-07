"""
Bring the memo's form into line with the E-memo manual (p.3-4, p.9).

Hand-ordered rather than left as the autodetector emitted it: the new columns and the
MemoSection table must exist, and the data step must have run, BEFORE `body`,
`classification`, `priority` and `title` are dropped - otherwise their contents go with
them.

What moves where:

  * `title` is retired. The manual's form has a Subject and no Title, and in practice
    the two carried the same sentence. A memo whose subject is empty inherits its title
    so nothing is left unnamed.
  * `body` becomes the first MemoSection, titled "Background" - the manual's own first
    block (p.3). A second, empty "Recommendation" section is created alongside it,
    because that is the pair the form opens with.
  * `classification` folds into `memo_type`. The manual has ONE Memo Type dropdown with
    three values; sensitivity lived in a second column and now lives in this one.
  * `priority` is retired with the SLA table it keyed. The manual's form has no
    priority, and the inbox's Due Days column now uses one default interval.
  * the unavailability reasons are replaced by the manual's five (p.9).
"""
import django.db.models.deletion
import uuid
from django.db import migrations, models


# Old memo_type carried a business category; old classification carried sensitivity.
# The manual has one dropdown, so sensitivity wins: anything marked confidential (at
# either level) becomes CONFIDENTIAL, and everything else becomes GENERAL. No existing
# memo becomes a DRAFT - the DRAFT type is new behaviour and applying it to historical
# memos would silently stop their logs.
RESTRICTED_CLASSIFICATIONS = {"confidential", "highly_confidential"}

# The retired reasons mapped onto the manual's five. Annual and sick leave are both
# "On Leave"; "Emergency" has no counterpart and is recorded as On Leave, which is what
# it meant operationally - the person was not at their desk.
REASON_MAP = {
    "annual_leave": "on_leave",
    "sick_leave": "on_leave",
    "emergency": "on_leave",
    "training": "on_training",
    "meeting": "on_conference_meeting",
    "official_visit": "field_site_visit",
}


def carry_forward(apps, schema_editor):
    Memo = apps.get_model("memos", "Memo")
    MemoSection = apps.get_model("memos", "MemoSection")
    MemoStepUnavailability = apps.get_model("memos", "MemoStepUnavailability")
    MemoTemplate = apps.get_model("memos", "MemoTemplate")

    for memo in Memo.objects.all().iterator():
        fields = []
        if not memo.subject and memo.title:
            memo.subject = memo.title[:500]
            fields.append("subject")

        mapped = ("confidential" if memo.classification in RESTRICTED_CLASSIFICATIONS
                  else "general")
        if memo.memo_type != mapped:
            memo.memo_type = mapped
            fields.append("memo_type")

        if fields:
            memo.save(update_fields=fields)

        # The authored body becomes the Background block, and the empty
        # Recommendation block the form opens with is created beside it.
        if not MemoSection.objects.filter(memo=memo).exists():
            MemoSection.objects.create(
                memo=memo, position=0, title="Background", body=memo.body or "")
            MemoSection.objects.create(
                memo=memo, position=1, title="Recommendation", body="")

    for row in MemoStepUnavailability.objects.all().iterator():
        mapped = REASON_MAP.get(row.reason)
        if mapped:
            row.reason = mapped
            row.save(update_fields=["reason"])

    for template in MemoTemplate.objects.all().iterator():
        if template.memo_type not in ("general", "confidential", "draft"):
            template.memo_type = "general"
            template.save(update_fields=["memo_type"])


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0017_merge_20260806_1728'),
        ('memos', '0014_memo_transfer_kind_labels'),
    ]

    operations = [
        # --- 1. the new shape, so the data step has somewhere to write ---
        migrations.AddField(
            model_name='memo',
            name='to_line',
            field=models.CharField(blank=True, default='', help_text='Who the memo is addressed to, by functional title (e.g. CEO).', max_length=255),
        ),
        migrations.AddField(
            model_name='memo',
            name='cc_departments',
            field=models.ManyToManyField(blank=True, help_text='Departments copied in. On a GENERAL memo they may also read it once archived.', related_name='memos_cc', to='leaves.department'),
        ),
        migrations.AddField(
            model_name='memo',
            name='department_unit',
            field=models.ForeignKey(blank=True, help_text='Optional unit within the department, to narrow view access.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='memos_as_unit', to='leaves.department'),
        ),
        migrations.AddField(
            model_name='memo',
            name='department_unit_name',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='memo',
            name='department_sub_unit',
            field=models.ForeignKey(blank=True, help_text='Optional sub-unit, to narrow view access further.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='memos_as_sub_unit', to='leaves.department'),
        ),
        migrations.AddField(
            model_name='memo',
            name='department_sub_unit_name',
            field=models.CharField(blank=True, default='', max_length=150),
        ),
        migrations.AddField(
            model_name='memoattachment',
            name='display_name',
            field=models.CharField(blank=True, default='', help_text='The name the uploader gave the file. Falls back to the original filename when left blank.', max_length=255),
        ),
        migrations.CreateModel(
            name='MemoSection',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('position', models.PositiveIntegerField(default=0, help_text="Render order. The list's order IS the document's.")),
                ('title', models.CharField(max_length=150)),
                ('body', models.TextField(blank=True, default='', help_text='Sanitized HTML.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('memo', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sections', to='memos.memo')),
            ],
            options={
                'ordering': ['position', 'created_at'],
                'indexes': [models.Index(fields=['memo', 'position'], name='memos_memos_memo_id_3a3ada_idx')],
            },
        ),

        # --- 2. carry the data across while both shapes still exist ---
        migrations.RunPython(carry_forward, migrations.RunPython.noop),

        # --- 3. the surviving columns take their new shape ---
        migrations.AlterField(
            model_name='memo',
            name='memo_type',
            field=models.CharField(choices=[('general', 'GENERAL'), ('confidential', 'CONFIDENTIAL'), ('draft', 'DRAFT')], db_index=True, default='general', max_length=20),
        ),
        migrations.AlterField(
            model_name='memo',
            name='reference_number',
            field=models.CharField(blank=True, db_index=True, default='', help_text='The reference code of a related memo, if this memo cites one.', max_length=100),
        ),
        migrations.AlterField(
            model_name='memostepunavailability',
            name='reason',
            field=models.CharField(choices=[('field_site_visit', 'Field/Site Visit'), ('on_leave', 'On Leave'), ('branch_visit', 'Branch Visit'), ('on_training', 'On Training'), ('on_conference_meeting', 'On Conference/Meeting')], max_length=24),
        ),
        migrations.AlterField(
            model_name='memotemplate',
            name='memo_type',
            field=models.CharField(choices=[('general', 'GENERAL'), ('confidential', 'CONFIDENTIAL'), ('draft', 'DRAFT')], max_length=20),
        ),

        # --- 4. the columns the manual has no use for ---
        migrations.RemoveField(model_name='memo', name='body'),
        migrations.RemoveField(model_name='memo', name='classification'),
        migrations.RemoveField(model_name='memo', name='priority'),
        migrations.RemoveField(model_name='memo', name='title'),
    ]
