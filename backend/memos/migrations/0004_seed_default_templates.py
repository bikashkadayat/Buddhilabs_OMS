"""Seed 3 default memo templates (idempotent)."""
from django.db import migrations

TEMPLATES = [
    {
        "name": "General Announcement",
        "memo_type": "general",
        "subject_template": "Announcement: [Topic]",
        "body_template": (
            "<p>Dear team,</p>"
            "<p>[Message body]</p>"
            "<p>Regards,<br>[Your name]</p>"
        ),
    },
    {
        "name": "HR Notice",
        "memo_type": "hr",
        "subject_template": "HR Notice: [Subject]",
        "body_template": (
            "<p>This notice is regarding <strong>[subject]</strong>.</p>"
            "<p>[Details]</p>"
            "<p>For questions, contact the HR department.</p>"
        ),
    },
    {
        "name": "Meeting Minutes",
        "memo_type": "internal",
        "subject_template": "Minutes: [Meeting Name] - [Date]",
        "body_template": (
            "<p><strong>Attendees:</strong></p><ul><li>[Name]</li></ul>"
            "<p><strong>Agenda:</strong></p><ol><li>[Item]</li></ol>"
            "<p><strong>Decisions / Action items:</strong></p><ul><li>[Action]</li></ul>"
        ),
    },
]


def seed(apps, schema_editor):
    MemoTemplate = apps.get_model("memos", "MemoTemplate")
    for tpl in TEMPLATES:
        MemoTemplate.objects.update_or_create(
            name=tpl["name"],
            defaults={
                "memo_type": tpl["memo_type"],
                "subject_template": tpl["subject_template"],
                "body_template": tpl["body_template"],
                "is_active": True,
            },
        )


def unseed(apps, schema_editor):
    MemoTemplate = apps.get_model("memos", "MemoTemplate")
    MemoTemplate.objects.filter(name__in=[t["name"] for t in TEMPLATES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("memos", "0003_alter_memo_current_reviewer"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
