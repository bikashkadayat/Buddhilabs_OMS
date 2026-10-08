"""Proactive customer success: tell the team before customers have to.

    python manage.py customer_success_alerts            # tickets (cron, every 15 min)
    python manage.py customer_success_alerts --customers  # customer signals (cron, daily)

TICKETS: first the escalation rules (Support Desk 3.0: a critical ticket
whose SLA clock has run 4+ hours is escalated and support leadership is
emailed -- ``SUPPORT_ESCALATION_HOURS``), then the alerts: critical, overdue
(past SLA), no response (unanswered for half their SLA) and stale (in
progress, untouched for 3 days). Each is emailed
once per condition -- overdue and stale repeat daily while they last -- and
is always visible in the console bell and on the Support desk.

CUSTOMERS: trial expiring, no employees, no attendance, inactive, a ticket
open over a week. Each opens a success task (Follow up, Call, Training...)
assigned to nobody and due in two days, unless one is already open for the
same reason, and the day's new ones are emailed as one digest.

Recipients: PLATFORM_SUPPORT_EMAIL, else every active platform operator.
Reads platform tables only, so it runs under RLS as the application role.
"""
from django.core.management.base import BaseCommand

from monitoring import heartbeat
from tenancy import desk, success


class Command(BaseCommand):
    help = "Email SLA alerts for tickets; with --customers, open success tasks for at-risk customers."

    def add_arguments(self, parser):
        parser.add_argument("--customers", action="store_true",
                            help="Run the daily customer signals instead of ticket alerts.")

    def handle(self, *args, **options):
        job = "SUCCESS_CUSTOMERS" if options["customers"] else "SUPPORT_SLA_ALERTS"
        with heartbeat.heartbeat(job):
            if options["customers"]:
                result = success.run_customer_alerts()
            else:
                result = {"escalations": desk.run_escalations(), **success.run_ticket_alerts()}
        self.stdout.write(f"{job.lower()}: {result}")
