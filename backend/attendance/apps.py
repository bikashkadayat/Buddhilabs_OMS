from django.apps import AppConfig


class AttendanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "attendance"

    def ready(self):
        # Connects the post_save receiver that keeps CompensatoryLedger in step
        # with derived attendance. Imported here rather than at module scope
        # because it touches models.
        from .policy import comp_off  # noqa: F401

        # Evict the cached per-tenant attendance configuration when an
        # operator changes it, so a console edit takes effect on the next
        # request instead of within the cache TTL. Without this, changing a
        # customer's office location appears not to work for five minutes,
        # which is long enough for somebody to change it again.
        from django.db.models.signals import post_save

        from . import config

        def _evict(sender, instance, **kwargs):
            config.forget(getattr(instance, "organization_id", None))

        from tenancy.models import OrganizationSettings

        post_save.connect(_evict, sender=OrganizationSettings,
                          dispatch_uid="attendance.config.evict")
