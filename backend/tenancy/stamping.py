"""Stamp the organization on Phase A rows that are saved without one.

WHY THIS IS REQUIRED, NOT A CONVENIENCE
---------------------------------------
Phase S2 made ``organization`` NOT NULL on 27 tables. Every existing caller --
views, services, management commands, fixtures, the test suite -- creates those
rows without passing one, because until this phase there was nothing to pass.
Without a default, `Department.objects.create(name="HR", code="HR")` becomes an
IntegrityError, and that is a behaviour change visible to NIF on the first
request. The phase brief forbids exactly that.

So the column gets filled the same way everything else in Phase S2 resolves a
tenant: ``tenancy.scoping.active_organization``, which while TENANCY_ENABLED is
False is "the single organization that exists".

WHY A SIGNAL AND NOT TenantScopedModel
--------------------------------------
``TenantScopedModel`` would also install ``TenantManager`` as the default
manager, which FILTERS every read. That is Phase S3's job and it would change
the result of every query in the project that touches a Phase A table -- the
one regression this phase must not cause. A ``pre_save`` receiver sets a
default and changes no read path at all.

WHAT THIS DOES NOT COVER
------------------------
``bulk_create`` and ``QuerySet.update`` do not emit ``pre_save``. That is
deliberate and checked: ``tenancy/tests/test_stamping.py`` asserts no Phase A
or Phase B model is written through ``bulk_create`` without naming an
organization, so if somebody adds one the test fails rather than the insert.
"""
import logging

from django.db.models.signals import pre_save

logger = logging.getLogger(__name__)

RECEIVER_UID = "tenancy.stamp_organization"


def stamp_organization(sender, instance, **kwargs):
    """Derive and verify ``organization`` before the row is written.

    THREE STEPS, IN THIS ORDER (Phase S4 added the first two):

      1. **Inherit from the parent.** A Phase B child row's owner is already
         recorded one level up -- a TaskComment belongs to its Task, a
         MemoAttachment to its Memo. ``tenancy.ownership`` declares the chain
         and resolves it, which is correct even with no request in hand.

      2. **Refuse a cross-tenant attach.** If the row names one organization
         and its parent names another, that is not a default to pick between:
         ``ownership.enforce`` raises ``CrossTenantWrite``. This is the step
         that makes the column PROVE ownership rather than merely record it --
         filing tenant A's comment against tenant B's task is impossible, not
         just unlikely.

      3. **Fall back to context** for a Phase B root (a Task, a Memo) or a
         Phase A row, which have no parent to inherit from -- unless the
         caller has already decided there is no tenant, see step 0.

      0. **Respect a decided None.** Added in Phase S6. For the handful of
         models whose ``organization`` is nullable, "no tenant" is a VALID
         answer that some callers compute deliberately --
         ``tenancy.ownership.resolve_for_audit`` is the authority for
         ``audit.AuditLog``, and it returns None for a platform operator's own
         actions. This receiver was then overwriting that None with the
         ambient context, which while TENANCY_ENABLED is False means the
         single-tenant default: a platform operator's LOGIN event was filed
         under NIF, in a customer's audit trail, saying somebody who does not
         work for them signed into their workspace. Row-level security
         surfaced it by refusing the write; the misattribution had been
         happening silently.

         Callers signal the decision by setting ``_organization_decided`` on
         the instance. Nothing else is affected: a model with a NOT NULL
         column cannot use it (the database would refuse the row), and no
         existing caller sets it.
    """
    from .ownership import enforce

    # Steps 1 and 2. Raises on a genuine cross-tenant attach.
    if enforce(instance) is not None:
        return

    if getattr(instance, "_organization_decided", False):
        return

    from .scoping import active_organization

    organization = active_organization(required=False)
    if organization is None:
        # Let the database raise. A NOT NULL violation naming the column is a
        # far better error than a row silently stamped with a guess -- and once
        # TENANCY_ENABLED is True this is the path that SHOULD fail, because it
        # means a write was attempted with no tenant in context.
        logger.warning(
            "%s saved with no organization and none resolvable from context",
            sender.__name__)
        return
    instance.organization_id = organization.pk


def stamp_all(objects):
    """Derive and verify ``organization`` on every object, then return them.

    FOR ``bulk_create``, WHICH DOES NOT EMIT ``pre_save``.

    Phase S4 added a non-null organization to 55 transactional models, and 24
    places in this project insert those models in bulk -- checklist items when
    a template is applied, workflow steps when a memo is routed, leave-day
    records when leave is approved, punches on device ingest. Every one of them
    failed with ``NOT NULL constraint failed: ..._organization_id`` the first
    time the suite ran, because the receiver below never fires for a bulk
    insert.

    The fix is this function rather than ``organization=...`` repeated 24
    times, for two reasons:

      * it runs the SAME derivation as the signal -- inherit from the declared
        parent, refuse a cross-tenant attach, fall back to context -- so a bulk
        insert and a single save cannot disagree about who owns a row; and
      * it is one place to audit, and one place for the next caller to find.

    Usage::

        TaskChecklistItem.objects.bulk_create(stamp_all(items))
    """
    objects = list(objects)
    for obj in objects:
        stamp_organization(type(obj), obj)
    return objects


def connect():
    """Attach the receiver to every Phase A model that has the column.

    Driven by ``PHASE_A | PHASE_B | PHASE_C`` rather than a hand-kept list, so
    a model added to any registry is covered without anybody remembering to
    come back here.

    PHASE_C WAS MISSED ON THE FIRST PASS, and the symptom was 1,032 test
    failures reading ``NOT NULL constraint failed:
    tasks_taskauditlog.organization_id`` -- every per-module audit trail, every
    derived summary and every draft. Driving the list from the registry is what
    makes that a one-line fix instead of twenty-three.
    """
    from django.apps import apps as global_apps

    from .inventory import PHASE_A, PHASE_B, PHASE_C

    connected = []
    for label in sorted(PHASE_A | PHASE_B | PHASE_C):
        app_label, model_name = label.split(".")
        try:
            model = global_apps.get_model(app_label, model_name)
        except LookupError:                    # pragma: no cover - defensive
            continue
        # users.User does its own stamping in save(), because it also has to
        # skip platform accounts and cope with deferred fields.
        if label == "users.User":
            continue
        if not any(f.name == "organization" for f in model._meta.local_fields):
            continue
        pre_save.connect(stamp_organization, sender=model,
                         dispatch_uid=f"{RECEIVER_UID}.{label}")
        connected.append(label)
    return connected
