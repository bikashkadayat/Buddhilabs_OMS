"""Phase C tenant isolation, step 2 of 2: make `organization` required on the
models that always have an owner to derive.

The five deliberately-nullable models are absent from this file -- see
0009_phase_c_organization for why.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("appraisal", "0009_phase_c_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name='appraisalauditlog',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='evidencereference',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),

    ]
