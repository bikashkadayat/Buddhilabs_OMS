from django.apps import AppConfig


class AppraisalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "appraisal"
    verbose_name = "Performance Appraisal"

    def ready(self):
        """
        Connect the notification receivers (Phase RELEASE).

        `ready()` runs once per process and every connection carries a
        `dispatch_uid`, so the receivers cannot be attached twice — which would
        deliver every appraisal notification twice, a bug that is invisible in
        development and obvious to everybody else.

        Imported HERE rather than at module scope: `appraisal.receivers` imports
        the notifications app's models, and importing those while the app
        registry is still populating raises AppRegistryNotReady.
        """
        from . import receivers

        receivers.connect()
