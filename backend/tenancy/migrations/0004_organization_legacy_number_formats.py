"""Which document-number format family an organization uses, and NIF's answer.

Four of the existing number formats carry no organisation prefix at all --
MIN-, CIR-, TRF-, DSP-. Adding one would change the numbers NIF's own documents
are issued under, and a document number is a historical record: you cannot
restate one that has already been printed, signed and filed.

So NIF is flagged as using the legacy formats and every future tenant uses the
uniform prefixed scheme. See tenancy/numbering.py for both families.
"""
from django.db import migrations, models


def flag_nif(apps, schema_editor):
    """NIF, and only NIF, keeps the pre-SaaS formats."""
    Organization = apps.get_model("tenancy", "Organization")
    Organization.objects.filter(slug="nif").update(legacy_number_formats=True)


def unflag(apps, schema_editor):
    Organization = apps.get_model("tenancy", "Organization")
    Organization.objects.update(legacy_number_formats=False)


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0003_seed_nif_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="legacy_number_formats",
            field=models.BooleanField(
                default=False,
                help_text="Emit the pre-SaaS document number formats. NIF only."),
        ),
        migrations.RunPython(flag_nif, unflag),
    ]
