from django.apps import AppConfig


class TasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tasks"
    verbose_name = "Task Management"

    def ready(self):
        """
        Connect the notification receivers (Phase T4.3).

        `ready()` runs once per process, and every connection carries a
        `dispatch_uid`, so the receivers cannot be attached twice — which would
        deliver every task notification twice, a bug that is invisible in
        development and obvious to everybody else.

        Imported HERE rather than at module scope: `tasks.receivers` imports the
        notifications app's models, and importing those while the app registry
        is still populating raises AppRegistryNotReady.
        """
        from . import receivers

        receivers.connect()

        # Phase T6.1. Publish this module as an evidence source. The registry is
        # a contract app that knows nothing about tasks; the dependency points
        # this way, so the six sources that are not yet integrated need no
        # change here or there.
        from evidence.registry import register
        from evidence.schema import Source

        from .evidence import task_evidence_provider

        register(Source.TASK, task_evidence_provider)
