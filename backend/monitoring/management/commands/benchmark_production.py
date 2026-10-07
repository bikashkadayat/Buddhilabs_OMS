"""Measure real API latency against a production-shaped dataset.

Phase 11, §7 of the approved design. The existing suites assert QUERY COUNTS,
which is the right guard against N+1 but says nothing about wall-clock time on
real data volumes. No production-representative benchmark existed, so the audit
deliberately published no p95 figures rather than invent them.

    python manage.py benchmark_production --seed --employees 250 --months 24
    python manage.py benchmark_production            # measure only, no seeding

Run it against **PostgreSQL**, not SQLite: the query planner is the thing being
measured, and SQLite's is not the one production uses. Seeding is destructive
enough that it refuses to run unless the database looks empty or --force is
given.
"""
import random
import statistics
import time
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, reset_queries
from django.test import Client
from django.utils import timezone

from users.models import User

# p95 budgets in milliseconds, from the approved performance strategy.
BUDGETS = {
    "attendance_list": 300,
    "attendance_dashboard": 500,
    "workforce_me": 500,
    "workforce_team": 500,
    "workforce_hr": 500,
    "analytics_executive": 250,
    "analytics_attendance": 250,
    "analytics_departments": 250,
    "analytics_hr": 250,
    "monitoring_health": 500,
}

ENDPOINTS = [
    ("attendance_list", "/api/v1/attendance/", "hr"),
    ("attendance_dashboard", "/api/v1/attendance/dashboard/", "hr"),
    ("workforce_me", "/api/v1/workforce/me/", "employee"),
    ("workforce_team", "/api/v1/workforce/team/", "manager"),
    ("workforce_hr", "/api/v1/workforce/hr/", "hr"),
    ("analytics_executive", "/api/v1/analytics/executive/", "hr"),
    ("analytics_attendance", "/api/v1/analytics/attendance/", "hr"),
    ("analytics_departments", "/api/v1/analytics/departments/", "hr"),
    ("analytics_hr", "/api/v1/analytics/hr/", "hr"),
    ("monitoring_health", "/api/v1/monitoring/health/", "hr"),
]

ITERATIONS = 20


class Command(BaseCommand):
    help = "Benchmark API latency against a production-shaped dataset."

    def add_arguments(self, parser):
        parser.add_argument("--seed", action="store_true")
        parser.add_argument("--employees", type=int, default=250)
        parser.add_argument("--months", type=int, default=24)
        parser.add_argument("--iterations", type=int, default=ITERATIONS)
        parser.add_argument("--force", action="store_true",
                            help="Seed even if the database already has data.")

    def handle(self, *args, **options):
        if "sqlite" in settings.DATABASES["default"]["ENGINE"]:
            self.stdout.write(self.style.WARNING(
                "Running on SQLite. The query planner being measured is not the "
                "one production uses, so these numbers are indicative only."))

        if options["seed"]:
            self._seed(options)

        self._measure(options)

    # -- seeding ----------------------------------------------------------
    def _seed(self, options):
        from attendance.models import Attendance
        from leaves.models import Department

        existing = User.objects.count()
        if existing > 20 and not options["force"]:
            raise CommandError(
                f"Database already holds {existing} users. Seeding would add "
                f"hundreds more. Re-run with --force if this is a scratch "
                f"database — never on production.")

        count, months = options["employees"], options["months"]
        self.stdout.write(f"Seeding {count} employees x {months} months ...")

        departments = [
            Department.objects.get_or_create(
                code=f"BENCH-{index}", defaults={"name": f"Benchmark Dept {index}"})[0]
            for index in range(12)
        ]

        joined = date(2020, 1, 1)
        # bulk_create does not emit pre_save, so neither User.save() nor the
        # tenancy stamping receiver runs -- the organization has to be set on
        # every instance explicitly or the XOR check constraint rejects the
        # batch (Phase S2).
        from tenancy.scoping import active_organization

        organization = active_organization()
        people = []
        for index in range(count):
            people.append(User(
                username=f"bench_{index}", email=f"bench_{index}@nif.test",
                first_name=f"Bench{index}", last_name="User",
                organization=organization,
                role=User.Roles.MAKER, department_ref=departments[index % 12],
                employment_type=User.EmploymentType.PERMANENT,
                date_of_joining=joined, is_active=True))
        User.objects.bulk_create(people, batch_size=500)
        User.objects.filter(username__startswith="bench_").update(
            date_joined=timezone.make_aware(datetime(2020, 1, 1, 9, 0)))

        people = list(User.objects.filter(username__startswith="bench_"))
        today = timezone.localdate()
        start = today - timedelta(days=months * 30)

        rows, batch = [], 0
        day = start
        while day <= today:
            if day.weekday() != 5:  # Saturday is the weekly holiday
                for person in people:
                    # ~92% attendance, which is realistic and keeps the
                    # absent-derivation path exercised rather than trivial.
                    roll = random.random()
                    if roll > 0.92:
                        continue
                    status = (Attendance.Status.LATE if roll > 0.86
                              else Attendance.Status.PRESENT)
                    rows.append(Attendance(
                        employee=person, date=day, status=status,
                        check_in=timezone.make_aware(
                            datetime(day.year, day.month, day.day, 10, 0)),
                        check_out=timezone.make_aware(
                            datetime(day.year, day.month, day.day, 18, 0)),
                        working_hours=Decimal("8.00"),
                        regular_hours=Decimal("8.00"),
                        overtime_hours=Decimal("0.50") if roll > 0.88 else Decimal("0.00"),
                        late_minutes=35 if status == Attendance.Status.LATE else 0))
                    if len(rows) >= 5000:
                        Attendance.objects.bulk_create(rows, ignore_conflicts=True)
                        batch += len(rows)
                        rows = []
                        self.stdout.write(f"  {batch} rows ...", ending="\r")
            day += timedelta(days=1)
        if rows:
            Attendance.objects.bulk_create(rows, ignore_conflicts=True)
            batch += len(rows)

        self.stdout.write(self.style.SUCCESS(
            f"\nSeeded {len(people)} employees and {batch} attendance rows."))

    # -- measurement ------------------------------------------------------
    def _measure(self, options):
        actors = self._actors()
        if not actors:
            raise CommandError(
                "No suitable users found. Seed first, or create an HR account.")

        client = Client()
        iterations = options["iterations"]
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nLatency over {iterations} iterations "
            f"({User.objects.filter(is_active=True).count()} active employees)\n"))
        self.stdout.write(
            f"{'endpoint':26s} {'p50':>8s} {'p95':>8s} {'max':>8s} "
            f"{'queries':>8s} {'budget':>8s}  verdict")
        self.stdout.write("-" * 84)

        breaches = []
        for name, path, actor_key in ENDPOINTS:
            actor = actors.get(actor_key) or actors.get("hr")
            if actor is None:
                continue
            client.force_login(actor)

            timings, queries = [], 0
            for index in range(iterations):
                if index == 0:
                    # Count queries on a cold call only; the analytics cache
                    # makes every later call a single auth query and would
                    # report a flattering number.
                    reset_queries()
                started = time.perf_counter()
                response = client.get(path)
                elapsed = (time.perf_counter() - started) * 1000
                if index == 0:
                    queries = len(connection.queries)
                if response.status_code != 200:
                    self.stdout.write(self.style.ERROR(
                        f"{name:26s} HTTP {response.status_code} — skipped"))
                    break
                timings.append(elapsed)
            else:
                self._report(name, timings, queries, breaches)

        self._summary(breaches)

    def _report(self, name, timings, queries, breaches):
        timings.sort()
        p50 = statistics.median(timings)
        p95 = timings[max(0, int(len(timings) * 0.95) - 1)]
        budget = BUDGETS.get(name)
        over = budget is not None and p95 > budget
        if over:
            breaches.append((name, round(p95, 1), budget))
        verdict = self.style.ERROR("OVER") if over else self.style.SUCCESS("ok")
        self.stdout.write(
            f"{name:26s} {p50:7.1f}ms {p95:7.1f}ms {max(timings):7.1f}ms "
            f"{queries:8d} {budget or '-':>7}ms  {verdict}")

    def _summary(self, breaches):
        self.stdout.write("")
        if not breaches:
            self.stdout.write(self.style.SUCCESS(
                "All measured endpoints are inside budget."))
            return
        self.stdout.write(self.style.ERROR(
            f"{len(breaches)} endpoint(s) over budget:"))
        for name, p95, budget in breaches:
            self.stdout.write(self.style.ERROR(
                f"  {name}: p95 {p95}ms against a {budget}ms budget"))
        self.stdout.write(
            "\nFor analytics, the documented escape hatch is the materialised "
            "monthly summary table (Phase 10 design, §2.4). For everything "
            "else, check the query count first — a latency breach with a flat "
            "query count is an index problem, not an N+1.")

    @staticmethod
    def _actors():
        return {
            "hr": (User.objects.filter(role=User.Roles.APPROVER, is_active=True).first()
                   or User.objects.filter(role=User.Roles.ADMIN, is_active=True).first()),
            "manager": User.objects.filter(role=User.Roles.CHECKER, is_active=True).first(),
            "employee": User.objects.filter(role=User.Roles.MAKER, is_active=True).first(),
        }
