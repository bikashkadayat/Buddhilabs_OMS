"""Phase S6.75: the launch readiness report, from a shell.

The same audit the console shows and the deploy gate enforces -- printed, so
it can go in a deployment log or be read over the phone during an incident.

    manage.py launch_readiness            # the report
    manage.py launch_readiness --strict   # and a non-zero exit if blocked
"""
from django.core.management.base import BaseCommand

from tenancy import launch

MARK = {True: "ok  ", False: "FAIL"}


class Command(BaseCommand):
    help = ("Report whether this deployment is fit to sell workspaces: "
            "plans, email, storage, domains, RLS, provisioning.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict", action="store_true",
            help="Exit non-zero when a critical dependency is missing, for "
                 "use as a deploy gate.")

    def handle(self, *args, **options):
        report = launch.audit()

        for check in report["checks"]:
            line = (f"  [{MARK[check['ready']]}] "
                    f"{check['label']:<22} {check['detail']}")
            if check["ready"]:
                self.stdout.write(line)
            elif check["severity"] == launch.CRITICAL:
                self.stdout.write(self.style.ERROR(line))
            else:
                self.stdout.write(self.style.WARNING(line))
            if not check["ready"] and check.get("hint"):
                self.stdout.write(f"         -> {check['hint']}")

        counts = report["counts"]
        self.stdout.write("")
        summary = (f"{report['verdict']}: score {report['score']}%, "
                   f"{counts['ready']}/{counts['total']} checks ready, "
                   f"{counts['critical_failing']} critical failing")
        style = self.style.SUCCESS if report["ready"] else self.style.ERROR
        self.stdout.write(style(summary))
        mode = report["mode"]
        self.stdout.write(
            f"mode: tenancy={'on' if mode['tenancy'] else 'off'}, "
            f"public registration="
            f"{'on' if mode['public_registration'] else 'off'}")

        if options["strict"] and not report["ready"]:
            # SystemExit rather than CommandError: a gate wants an exit code,
            # not a traceback.
            raise SystemExit(1)
