"""
Phase ASSET-TRANSFER-GOVERNANCE: three approval gates become one.

    before   Draft -> Department Head Review -> HR Review -> Admin Approval -> Completed
    after    Draft -> HR Review -> Completed

WHY A DATA MIGRATION AND NOT JUST A CODE CHANGE
-----------------------------------------------
Transfers already in flight are sitting at gates that no longer exist. Nobody can
approve them - `can_act_at_stage` refuses any status outside REVIEW_STAGES - and
nobody can reject them either, so without this they would be stuck forever, open
against their asset and blocking the partial unique constraint that allows only
one open transfer per item. That is worse than either workflow.

So every OPEN transfer resting at a retired gate is moved to the HR gate, where a
real person can now decide it.

WHAT IS NOT TOUCHED
-------------------
  * COMPLETED, REJECTED and CANCELLED transfers. Their status is the record of
    what happened; a transfer that really was approved by a department head and an
    administrator keeps saying so.
  * Every stamp already written - dept_head_by/at/remarks, approved_by/at - on
    open transfers too. A department head who approved a transfer yesterday did
    approve it, and this migration is not entitled to unsay that. The stage
    tracker reads those stamps and still draws the gate they cleared.
  * The status VALUES themselves. `dept_head_review` and `admin_approval` remain
    in Status so the rows above stay readable.

Every move appends an AssetTransferEvent, so the jump shows up in the transfer's
own timeline rather than looking like a status that changed itself overnight.
Reversing the migration puts nothing back: there is no way to know which of the
two gates a transfer had reached, and guessing would fabricate an approval.
"""
from django.db import migrations

RETIRED = ("dept_head_review", "admin_approval")


def move_onto_the_hr_gate(apps, schema_editor):
    AssetTransfer = apps.get_model("inventory", "AssetTransfer")
    AssetTransferEvent = apps.get_model("inventory", "AssetTransferEvent")

    stranded = AssetTransfer.objects.filter(status__in=RETIRED)
    for transfer in stranded.iterator():
        was = transfer.status
        last = (AssetTransferEvent.objects.filter(transfer=transfer)
                .order_by("-sequence").values_list("sequence", flat=True).first()) or 0
        # actor stays null: no person did this, a release did, and attributing it
        # to whoever ran the deploy would put a name against a decision they
        # never made.
        AssetTransferEvent.objects.create(
            transfer=transfer, sequence=last + 1, action="submitted",
            from_status=was, to_status="hr_review", actor=None, actor_name="System",
            remarks=("Approval was simplified to a single HR gate. This transfer was "
                     "waiting at a stage that no longer exists and has been moved to "
                     "HR Review so it can be decided."),
            metadata={"migration": "inventory.0011", "previous_status": was})
        transfer.status = "hr_review"
        transfer.save(update_fields=["status", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0010_inventorycategory_default_useful_life_months_and_more"),
    ]

    operations = [
        migrations.RunPython(move_onto_the_hr_gate, migrations.RunPython.noop),
    ]
