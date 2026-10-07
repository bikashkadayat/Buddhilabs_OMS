"""Shared helpers for the Phase A backfill migrations.

Twenty-seven tables get the same three operations in the same order, and the
backfill is identical for all of them. Writing it once means the verification
step -- "no row may be left without an organization" -- is also written once
and cannot be forgotten on the twenty-fourth table.

These functions are called from migrations, so they take the HISTORICAL model
registry (``apps``) and never import a live model.
"""

NIF_SLUG = "nif"


def default_organization_id(apps, *, slug=NIF_SLUG):
    """The id of the organization existing rows belong to.

    Phase S2 runs against a single-tenant database, so this is unambiguous.
    It refuses rather than guesses if that stops being true: picking one of
    several organizations would stamp somebody else's rows.
    """
    Organization = apps.get_model("tenancy", "Organization")

    organization = Organization.objects.filter(slug=slug).first()
    if organization is not None:
        return organization.pk

    candidates = list(Organization.objects.all()[:2])
    if len(candidates) == 1:
        return candidates[0].pk
    if not candidates:
        raise RuntimeError(
            "No Organization exists; tenancy.0003_seed_nif_organization must "
            "run before any Phase A backfill.")
    raise RuntimeError(
        f"More than one Organization exists and none has slug '{slug}'. A "
        f"backfill cannot choose which tenant owns the existing rows.")


def backfill(apps, app_label, model_names, *, slug=NIF_SLUG, field="organization"):
    """Assign every row of each model to the default organization.

    Returns ``{model_name: rows_updated}``. Then VERIFIES that nothing is left
    unassigned and raises if anything is -- Step 3 of the phase brief, enforced
    by the migration rather than checked by hand afterwards.
    """
    org_id = default_organization_id(apps, slug=slug)
    updated = {}

    for name in model_names:
        model = apps.get_model(app_label, name)
        updated[name] = (model.objects
                         .filter(**{f"{field}__isnull": True})
                         .update(**{f"{field}_id": org_id}))

    orphans = {
        name: apps.get_model(app_label, name).objects
        .filter(**{f"{field}__isnull": True}).count()
        for name in model_names
    }
    remaining = {name: n for name, n in orphans.items() if n}
    if remaining:
        raise RuntimeError(
            f"Phase A backfill left orphan rows in {app_label}: {remaining}. "
            f"Refusing to continue -- the NOT NULL step that follows would "
            f"fail anyway, and an orphan row is a row with no owner.")
    return updated


def unbackfill(apps, app_label, model_names, *, field="organization"):
    """Reverse: clear the column. The column itself is dropped by AlterField."""
    for name in model_names:
        apps.get_model(app_label, name).objects.update(**{f"{field}_id": None})
