"""Deployment checks for the tenancy configuration.

Run by ``manage.py check`` and therefore by ``runserver``, ``migrate`` and
every deploy gate. These are the mistakes that produce a platform which boots
cleanly and then does not work -- the kind nothing else catches, because every
individual setting is valid on its own.
"""
from django.core.checks import Error, Tags, Warning, register


@register()
def tenancy_configuration(app_configs, **kwargs):
    """Flag combinations that are individually valid and jointly wrong."""
    from django.conf import settings

    problems = []

    if not settings.TENANCY_ENABLED:
        # Nothing below applies while the master switch is off: the
        # single-tenant compatibility shim is in charge and all of this is
        # inert by design.
        return problems

    if not getattr(settings, "TENANCY_BASE_DOMAIN", ""):
        problems.append(Error(
            "TENANCY_ENABLED is on but TENANCY_BASE_DOMAIN is empty, so no "
            "host can resolve to a tenant and every request will be refused "
            "as an unknown workspace.",
            hint="Set TENANCY_BASE_DOMAIN to the domain tenant subdomains "
                 "hang off, e.g. platform.com, and point wildcard DNS and a "
                 "wildcard certificate at it.",
            id="tenancy.E001"))

    if not getattr(settings, "TENANCY_PLATFORM_HOSTS", ""):
        problems.append(Warning(
            "TENANCY_ENABLED is on but TENANCY_PLATFORM_HOSTS is empty, so "
            "the platform console shares a hostname with the tenant "
            "workspaces. It still works, but a platform session is then "
            "established on a customer-facing origin -- one cross-site "
            "scripting bug in the tenant UI away from being a platform "
            "session the customer can drive.",
            hint="Give the console its own hostname (admin.platform.com) and "
                 "set TENANCY_PLATFORM_HOSTS to it. A tenant slug can never "
                 "shadow it -- see the reserved list in tenancy/slugs.py.",
            id="tenancy.W001"))

    # --- Phase S7: public registration ---
    if getattr(settings, "TENANCY_PUBLIC_REGISTRATION", False):
        if not getattr(settings, "TENANCY_PLATFORM_HOSTS", ""):
            problems.append(Warning(
                "TENANCY_PUBLIC_REGISTRATION is on but "
                "TENANCY_PLATFORM_HOSTS is empty, which breaks signup in "
                "both directions at once. Every request must resolve to a "
                "tenant while TENANCY_ENABLED is on, so the registration "
                "pages are UNREACHABLE on any address that is not already a "
                "customer's -- and on the addresses that are, they are "
                "served INSIDE that customer's workspace, where a signup "
                "form reads as ABC School inviting the visitor to create an "
                "account with them.",
                hint="Give the platform its own hostname "
                     "(app.platform.com), set TENANCY_PLATFORM_HOSTS to it, "
                     "and serve /register there. The host guard in "
                     "tenancy.registration_views stands down when this is "
                     "empty, which is what leaves the second half of the "
                     "problem above.",
                id="tenancy.W003"))

        backend = getattr(settings, "EMAIL_BACKEND", "")
        if "smtp" not in backend.lower():
            problems.append(Warning(
                "TENANCY_PUBLIC_REGISTRATION is on but EMAIL_BACKEND is "
                f"{backend!r}, which does not send mail. Verification is the "
                "only way a registration becomes a workspace, so every "
                "signup will stop at 'check your email' and no tenant will "
                "ever be created.",
                hint="Configure SMTP, or leave public registration off until "
                     "mail works.",
                id="tenancy.W004"))

        # A plan to put them on. Checked at DEPLOY time rather than
        # discovered by the first customer: without one,
        # `provision_organization` refuses, and the refusal arrives after
        # somebody has filled in the form, received the email and clicked the
        # link -- the single worst moment to find out.
        try:
            from . import plans as plans_module

            if not plans_module.purchasable_plans().exists():
                problems.append(Warning(
                    "TENANCY_PUBLIC_REGISTRATION is on but no purchasable "
                    "plan exists, so provisioning will refuse every verified "
                    "registration. The registrant has filled in the form, "
                    "received the email and clicked the link by the time "
                    "this happens.",
                    hint="Seed at least one active, public plan with a "
                         "current price. tenancy.plans.purchasable_plans() "
                         "is what the registration path asks.",
                    id="tenancy.W006"))
        except Exception:                          # noqa: BLE001
            # No database yet (a `check` during a build, or before migrate).
            # Not worth failing the check run over.
            pass

        if not getattr(settings, "TENANCY_PUBLIC_BASE_URL", ""):
            hosts = getattr(settings, "TENANCY_PLATFORM_HOSTS", "")
            if not hosts:
                problems.append(Warning(
                    "TENANCY_PUBLIC_REGISTRATION is on with neither "
                    "TENANCY_PUBLIC_BASE_URL nor TENANCY_PLATFORM_HOSTS set, "
                    "so the verification link in the email is built from "
                    "TENANCY_BASE_DOMAIN and may not point anywhere a "
                    "browser can reach.",
                    hint="Set TENANCY_PUBLIC_BASE_URL to the absolute URL "
                         "the signup pages are served from, e.g. "
                         "https://app.platform.com.",
                    id="tenancy.W005"))

    if not getattr(settings, "TENANCY_RLS_ENABLED", False):
        problems.append(Warning(
            "TENANCY_ENABLED is on but TENANCY_RLS_ENABLED is off, so tenant "
            "isolation rests on the application alone. A query that forgets "
            "its tenant then returns another tenant's rows instead of none.",
            hint="Switch TENANCY_RLS_ENABLED on and connect as a "
                 "NOSUPERUSER, NOBYPASSRLS role that does not own the "
                 "tables. See tenancy/rls.py for the three roles.",
            id="tenancy.W002"))

    return problems


@register(Tags.compatibility, deploy=True)
def saas_launch_readiness(app_configs, **kwargs):
    """Phase S6.75 Part 1: refuse to DEPLOY with a critical dependency missing.

    THE ENFORCEMENT. A critical failure from ``tenancy.launch`` becomes a
    Django ``Error`` here, so ``manage.py check --deploy`` fails outright.
    That is the gate, and the deployment checklist has that line on it.

    ``deploy=True``, AND WHY IT HAD TO CHANGE (Phase S9).

    This was registered as an ordinary check, which runs before EVERY
    management command. The intent was "refuse to start", but the effect was
    that an incompletely-configured platform could not be configured:

      * On a fresh database it aborted ``migrate`` -- the command that
        creates the tables it reads. (``_schema_present`` below now stands
        the gate down in that case too; both fixes are needed, because
        ``check --deploy`` on a fresh database would otherwise still fail
        for the same reason.)
      * Once migrated it aborted ``create_platform_admin``, so the first
        operator could not be created until SMTP was live and DEBUG was off
        -- while setting those up is work an operator does. A chicken and
        egg with no way out but ``--skip-checks``, which is the habit this
        gate exists to make unnecessary.

    WHAT IT DOES NOT DO, said plainly rather than implied: a WSGI or ASGI
    server does not run Django's system checks when it boots. Gunicorn and
    Daphne will serve a misconfigured platform all day whatever is
    registered here. So "refuse startup" was never achievable this way, and
    the deploy step always was the real gate -- this makes the registration
    match where the gate actually is, instead of blocking unrelated commands
    on the way.

    ONLY WHEN THE PLATFORM IS ACTUALLY A PLATFORM. With TENANCY_ENABLED off
    this is silent: the single-tenant deployment NIF runs today has no
    purchasable plan, no platform hostname and no SMTP requirement, and none
    of that is a fault. Grading those as errors would make the check
    something people learn to pass with --skip-checks.
    """
    from django.conf import settings

    if not getattr(settings, "TENANCY_ENABLED", False):
        return []

    # THE BOOTSTRAP DEADLOCK THIS AVOIDS, found by running the live drive
    # against an EMPTY database rather than a migrated one.
    #
    # Django runs system checks before `migrate`. This gate reads Plan,
    # Organization and the operator list -- none of which exist until
    # `migrate` has run -- so on a fresh deployment it failed with "could
    # not be read: ProgrammingError" and ABORTED THE MIGRATION THAT WOULD
    # HAVE CREATED ITS OWN TABLES. A new platform could not be stood up at
    # all without `--skip-checks`, which is the habit this gate exists to
    # make unnecessary.
    #
    # An un-migrated database is not an unready platform; it is a platform
    # that has not been built yet, and the honest answer is "cannot assess",
    # as a Warning that does not block.
    if not _schema_present():
        return [Warning(
            "The SaaS launch gate cannot run yet: the tenancy tables do not "
            "exist. Apply migrations, then re-run `manage.py check`.",
            hint="This is expected on a new deployment before `migrate`.",
            id="tenancy.L000")]

    from . import launch

    # ONE ROOT CAUSE, ONE ERROR. `tenancy.E001` already fails the gate when
    # TENANCY_BASE_DOMAIN is empty, and the launch audit's `domains` check
    # fails for the same reason -- so emitting both prints the same sentence
    # twice under two ids, which is how people start skimming gate output.
    # The readiness REPORT still counts it; only the duplicate Error is
    # suppressed. RLS is deliberately not suppressed: W002 is a warning and
    # the launch gate's whole purpose is that this one blocks.
    already_reported = set()
    if not getattr(settings, "TENANCY_BASE_DOMAIN", ""):
        already_reported.add("domains")

    problems = []
    for failure in launch.blocking_failures():
        if failure["key"] in already_reported:
            continue
        problems.append(Error(
            f"LAUNCH BLOCKED -- {failure['label']}: {failure['detail']}.",
            hint=(failure.get("hint")
                  or "See the launch readiness report in the platform "
                     "console, or `manage.py launch_readiness`."),
            id=f"tenancy.L{_LAUNCH_IDS.get(failure['key'], '999')}"))
    return problems


def _schema_present():
    """Have the tenancy tables been created yet?

    Deliberately cheap and deliberately forgiving: any failure to ASK is
    answered "no", because the only thing this gates is whether to attempt
    a set of queries, and the cost of a wrong "no" is a warning instead of
    an assessment -- while the cost of a wrong "yes" is the deadlock above.
    """
    from django.db import connection

    try:
        names = set(connection.introspection.table_names())
    except Exception:                                  # noqa: BLE001
        return False
    return {"tenancy_organization", "tenancy_plan"} <= names


# Stable ids, so a deployment can grep its gate output for the same string
# tomorrow. Numbered in the order the launch report lists them.
_LAUNCH_IDS = {
    "database": "001",
    "connections": "014",
    "error_tracking": "015",
    "logging": "016",
    "alert_delivery": "017",
    "cache": "002",
    "storage": "003",
    "domains": "004",
    "rls": "005",
    "plans": "006",
    "trial_plan": "007",
    "email": "008",
    "provisioning": "009",
    "operators": "010",
    "debug": "011",
    "export_retention": "012",
    "payment_methods": "013",
}
