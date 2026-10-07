from django.apps import AppConfig


class TenancyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenancy"
    verbose_name = "Tenancy & Subscriptions"

    def ready(self):
        """Attach the Phase A organization-stamping receivers.

        Imported here rather than at module scope because `tenancy.stamping`
        reads the app registry, and touching it while it is still populating
        raises AppRegistryNotReady. Every connection carries a dispatch_uid, so
        a double `ready()` cannot attach the receiver twice.
        """
        from . import checks, context, seat_signals, stamping  # noqa: F401

        stamping.connect()
        # Phase S6: tenant enforcement stands down for the duration of a
        # migration run. See tenancy.context.connect_migration_signals.
        context.connect_migration_signals()
        # Phase S6: keeps Organization.seat_count true, so the platform
        # dashboard needs no cross-tenant read to show a headcount. See
        # tenancy/counters.py for why that matters.
        seat_signals.connect()
        # Customer success: counts of use per organization per day, so the
        # console can show adoption without reading customer records.
        from . import adoption

        adoption.connect()
