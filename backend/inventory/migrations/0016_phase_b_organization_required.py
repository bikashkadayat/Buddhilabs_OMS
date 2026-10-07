"""Phase B tenant isolation, step 2 of 2: make `organization` required and
replace the remaining global unique constraints with per-tenant ones.

ORDER WITHIN THIS MIGRATION MATTERS.

  1. `organization` becomes NOT NULL -- safe, because 0015_phase_b_organization
     backfilled every row and refused to finish if any was left over.
  2. The NEW composite constraints are ADDED.
  3. Only then is the OLD global `unique=True` dropped.

Adding before dropping means there is never an instant with no uniqueness at
all. Dropping first would open a window in which a duplicate could be written,
and that duplicate would then stop the new index from ever building.
"""
import django.db.models.deletion
import django.db.models.functions.text
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0015_phase_b_organization"),
    ]

    operations = [
        migrations.AlterField(
            model_name='assetdisposal',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='assetdisposalattachment',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='assetrequest',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='assetreturn',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='assettransfer',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='assettransferattachment',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='inventoryitem',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='itemassignment',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='maintenanceticket',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.AlterField(
            model_name='takeoutrequest',
            name='organization',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='tenancy.organization'),
        ),
        migrations.RemoveConstraint(
            model_name='inventoryitem',
            name='uniq_inventory_serial_number',
        ),
        migrations.AlterField(
            model_name='assetdisposal',
            name='disposal_number',
            field=models.CharField(editable=False, max_length=20),
        ),
        migrations.AlterField(
            model_name='assetrequest',
            name='reference',
            field=models.CharField(editable=False, max_length=32),
        ),
        migrations.AlterField(
            model_name='assetreturn',
            name='reference',
            field=models.CharField(editable=False, max_length=32),
        ),
        migrations.AlterField(
            model_name='assettransfer',
            name='transfer_number',
            field=models.CharField(editable=False, max_length=20),
        ),
        migrations.AlterField(
            model_name='inventoryitem',
            name='asset_code',
            field=models.CharField(editable=False, max_length=32),
        ),
        migrations.AlterField(
            model_name='maintenanceticket',
            name='reference',
            field=models.CharField(editable=False, max_length=32),
        ),
        migrations.AlterField(
            model_name='takeoutrequest',
            name='reference',
            field=models.CharField(editable=False, max_length=32),
        ),
        migrations.AddConstraint(
            model_name='assetdisposal',
            constraint=models.UniqueConstraint(fields=('organization', 'disposal_number'), name='uniq_disposal_number_org'),
        ),
        migrations.AddConstraint(
            model_name='assetrequest',
            constraint=models.UniqueConstraint(fields=('organization', 'reference'), name='uniq_asset_request_reference_org'),
        ),
        migrations.AddConstraint(
            model_name='assetreturn',
            constraint=models.UniqueConstraint(fields=('organization', 'reference'), name='uniq_asset_return_reference_org'),
        ),
        migrations.AddConstraint(
            model_name='assettransfer',
            constraint=models.UniqueConstraint(fields=('organization', 'transfer_number'), name='uniq_transfer_number_org'),
        ),
        migrations.AddConstraint(
            model_name='inventoryitem',
            constraint=models.UniqueConstraint(fields=('organization', 'asset_code'), name='uniq_asset_code_org'),
        ),
        migrations.AddConstraint(
            model_name='inventoryitem',
            constraint=models.UniqueConstraint(models.F('organization'), django.db.models.functions.text.Lower('serial_number'), condition=models.Q(('serial_number', ''), _negated=True), name='uniq_inventory_serial_org'),
        ),
        migrations.AddConstraint(
            model_name='maintenanceticket',
            constraint=models.UniqueConstraint(fields=('organization', 'reference'), name='uniq_maintenance_reference_org'),
        ),
        migrations.AddConstraint(
            model_name='takeoutrequest',
            constraint=models.UniqueConstraint(fields=('organization', 'reference'), name='uniq_takeout_reference_org'),
        ),
    ]
