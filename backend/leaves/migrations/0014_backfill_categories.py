"""
Phase 4 backfill: resolve a leave category for every existing user and generate
their category-driven LeaveBalance rows for the current year. Flagged/fallback
cases are left with user.category_flag set so the HR review list surfaces them.

Idempotent (re-resolves + refreshes allocations), non-destructive (never lowers
used_so_far, which is derived), reverse is a no-op.
"""
from django.db import migrations


def backfill(apps, schema_editor):
    # Import live helpers: this runs at migration head, so the model layer matches.
    from django.utils import timezone
    from leaves import category_engine
    from django.contrib.auth import get_user_model

    User = get_user_model()
    year = timezone.localdate().year
    flagged = []

    # defer(...): this migration depends only on users.0008 (see the
    # dependencies note below), so on a FRESH database it runs before several
    # later users migrations add their columns. The live User model always
    # declares every field, so a plain User.objects.all() would emit
    # `SELECT ... biometric_id, organization_id, is_platform_staff ...` and fail
    # with "column does not exist" even though the table is empty at this point.
    # Deferring those columns keeps the query valid; the backfill reads none of
    # them.
    #
    # ANY FIELD ADDED TO User AFTER users.0008 MUST BE ADDED HERE. That is the
    # cost of the dependency edge this migration deliberately does not have, and
    # it is cheaper than the InconsistentMigrationHistory the edge would cause
    # on an already-migrated production database.
    #
    #   biometric_id                  users.0009
    #   organization, is_platform_staff  users.0012 (Phase S1 tenancy)
    #   ui_state                      users.0018 (guided tours)
    #
    # User.save() skips its platform-staff guard when either tenancy field is
    # deferred, for exactly this reason -- see users/models.py.
    for user in User.objects.defer("biometric_id", "organization",
                                   "is_platform_staff", "ui_state"):
        try:
            # Default maternity/paternity eligibility from gender if still at the
            # zero-default (existing rows: gender undisclosed => both stay False).
            mat, pat = category_engine.default_eligibility(user.gender)
            update_fields = []
            if user.gender == User.Gender.FEMALE and not user.maternity_eligible:
                user.maternity_eligible = True; update_fields.append("maternity_eligible")
            if user.gender == User.Gender.MALE and not user.paternity_eligible:
                user.paternity_eligible = True; update_fields.append("paternity_eligible")
            if update_fields:
                user.save(update_fields=update_fields)

            category, flag = category_engine.resolve_and_cache(user)
            category_engine.ensure_category_balances(user, year)
            if flag:
                flagged.append((user.get_username(), category, flag))
        except Exception:
            # Never block the deploy on one bad row; the HR review list + nightly
            # integrity audit will surface anything left unresolved.
            continue

    if flagged:
        print(f"\n[category backfill] {len(flagged)} user(s) flagged for HR review:")
        for username, category, flag in flagged:
            print(f"  - {username} [{category}]: {flag}")


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("leaves", "0013_seed_entitlement_matrix"),
        # This must stay at users.0008 — the migration that was already applied
        # on production BEFORE this backfill ran. Do NOT bump it to a later users
        # migration (e.g. 0009_user_biometric_id): production applied 0014 long
        # ago without 0009, so declaring that dependency retroactively makes
        # Django's check_consistent_history reject the database with
        # "InconsistentMigrationHistory: 0014 is applied before its dependency
        # 0009". Columns added by later users migrations are handled with
        # defer() in backfill() above, not by a dependency edge.
        ("users", "0008_user_address_user_bio_user_date_of_birth_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
