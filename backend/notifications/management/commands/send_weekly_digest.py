"""Send the opt-in weekly digest email. Run Monday 09:00 via cron."""
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.db.models import Q

from notifications import emails
from django.template.loader import render_to_string
from django.utils import timezone

from leaves.models import Leave, LeaveDayRecord
from memos.models import Memo, MemoApprovalStep, MemoWorkflowStep
from notifications.emails import _frontend
from notifications.models import Category, NotificationPreference

from monitoring import heartbeat


class Command(BaseCommand):
    help = "Email the weekly digest to users who opted in (WEEKLY_DIGEST email enabled)."

    def handle(self, *args, **options):
        # Phase 11 (audit finding M5): record a heartbeat so a job that stops
        # running becomes a red tile and an alert instead of silence. On an
        # exception the heartbeat is written FAILED and the error re-raised, so
        # cron still sees a non-zero exit.
        with heartbeat.heartbeat("WEEKLY_DIGEST"):
            return self._handle(*args, **options)

    def _handle(self, *args, **options):
        since = timezone.now() - timedelta(days=7)
        prefs = NotificationPreference.objects.filter(
            category=Category.WEEKLY_DIGEST, email_enabled=True,
        ).select_related("user")

        sent = 0
        for pref in prefs:
            user = pref.user
            if not user or not user.is_active or not user.email:
                continue

            stats = {
                "leave_days": LeaveDayRecord.objects.filter(
                    user=user, status=LeaveDayRecord.Status.APPROVED, created_at__gte=since,
                ).count(),
                "memos_actioned": MemoApprovalStep.objects.filter(actor=user, acted_at__gte=since).count(),
                "pending": (
                    Leave.objects.filter(approver=user, status="pending", is_deleted=False).count()
                    # "Pending on me" is an outstanding matrix step assigned to
                    # this user - the two-slot routing columns this used to read
                    # no longer exist.
                    + Memo.objects.filter(
                        workflow_steps__assignee=user,
                        workflow_steps__status=MemoWorkflowStep.StepStatus.ACTIVE,
                    ).exclude(status__in=["approved", "archived", "rejected", "cancelled"])
                    .distinct().count()
                ),
            }

            ctx = {
                "recipient_name": user.get_full_name() or user.username,
                "title": "Your weekly summary",
                "body": "",
                "stats": stats,
                "action_url": _frontend("/leave"),
                "unsubscribe_url": _frontend("/notifications"),
                "category_label": "Weekly digest",
            }
            html = render_to_string("emails/weekly_digest.html", ctx)
            text = (
                f"Weekly summary for {ctx['recipient_name']}:\n"
                f"- Leave days taken: {stats['leave_days']}\n"
                f"- Memos actioned: {stats['memos_actioned']}\n"
                f"- Pending your action: {stats['pending']}\n\n"
                f"Manage preferences: {ctx['unsubscribe_url']}\n"
                f"{emails.powered_by()}"
            )
            msg = EmailMultiAlternatives(
                f"{emails.subject_prefix()} Your weekly summary", text,
                settings.DEFAULT_FROM_EMAIL, [user.email],
            )
            msg.attach_alternative(html, "text/html")
            msg.send(fail_silently=True)
            sent += 1

        self.stdout.write(self.style.SUCCESS(f"Weekly digest sent to {sent} user(s)."))
