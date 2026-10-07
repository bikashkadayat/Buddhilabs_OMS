from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "analytics"
    verbose_name = "Executive Analytics"

    def ready(self):
        # Signal receivers that bump the cache generation on history-altering
        # writes. Imported here (not at module scope) so importing the app never
        # pulls in models before the registry is populated.
        from . import signals  # noqa: F401
