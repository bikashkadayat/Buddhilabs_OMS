from django.apps import AppConfig

class LeavesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'leaves'

    def ready(self):
        # Register Phase 4 signal handlers.
        from . import signals  # noqa: F401
