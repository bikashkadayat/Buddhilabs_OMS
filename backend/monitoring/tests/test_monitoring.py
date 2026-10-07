"""Phase 11 monitoring, heartbeat and alerting tests.

The alerting tests carry most of the weight here. Detecting a problem is the
easy half; the half that decides whether anyone still reads the alerts in three
months is deduplication, and that is asserted explicitly.
"""
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from monitoring import alerts, heartbeat, metrics

pytestmark = pytest.mark.django_db


# ===========================================================================
# heartbeats (audit finding M5)
# ===========================================================================
class TestHeartbeat:
    def test_every_registered_job_reports_a_status(self):
        statuses = heartbeat.all_statuses()
        assert len(statuses) == len(heartbeat.CRON_JOBS)
        assert all(entry["state"] in ("green", "amber", "red") for entry in statuses)

    def test_a_job_that_has_never_run_is_red_not_missing(self):
        """'No data' reads as 'not my problem'; red reads as a job to fix."""
        entry = heartbeat.status("BACKUP")
        assert entry["state"] == "red"
        assert entry["reason"] == "never_run"

    def test_a_recent_run_is_green(self, beat):
        beat("PROCESS_PUNCHES", minutes_ago=2)
        assert heartbeat.status("PROCESS_PUNCHES")["state"] == "green"

    def test_a_late_run_goes_amber_then_red(self, beat):
        # PROCESS_PUNCHES runs every 10 minutes.
        beat("PROCESS_PUNCHES", minutes_ago=16)
        assert heartbeat.status("PROCESS_PUNCHES")["state"] == "amber"
        AuditLog.objects.all().delete()
        beat("PROCESS_PUNCHES", minutes_ago=25)
        assert heartbeat.status("PROCESS_PUNCHES")["state"] == "red"

    def test_a_failed_run_is_red_however_recent(self, beat):
        beat("BACKUP", minutes_ago=1, ok=False)
        entry = heartbeat.status("BACKUP")
        assert entry["state"] == "red"
        assert entry["reason"] == "last_run_failed"

    def test_the_context_manager_records_success(self):
        with heartbeat.heartbeat("PURGE_REPORTS"):
            pass
        assert heartbeat.status("PURGE_REPORTS")["state"] == "green"

    def test_a_failure_is_recorded_and_re_raised(self):
        """Swallowing here would make a broken job look healthy — the exact
        failure the heartbeat exists to prevent."""
        with pytest.raises(ValueError):
            with heartbeat.heartbeat("PURGE_REPORTS"):
                raise ValueError("boom")
        timestamp, ok = heartbeat.last_run("PURGE_REPORTS")
        assert timestamp is not None and ok is False

    def test_recording_never_raises(self, monkeypatch):
        """A monitoring write must not be able to fail the job it monitors."""
        def explode(*args, **kwargs):
            raise RuntimeError("audit table is gone")

        monkeypatch.setattr("audit.services.log_action", explode)
        assert heartbeat.record("BACKUP") is None  # logged, not raised

    def test_the_registry_covers_every_cron_line(self):
        """A job in the crontab with no registry entry is silently unmonitored,
        which is the bug M5 described."""
        import re
        from pathlib import Path

        crontab = Path(__file__).resolve().parents[2] / "deploy" / "crontab"
        commands = set(re.findall(r"manage\.py (\w+)", crontab.read_text()))
        commands -= {"record_heartbeat"}  # the helper itself is not a job

        expected = {
            "recompute_summaries", "audit_data_integrity", "process_year_end",
            "run_scheduled_reports", "purge_expired_reports", "send_weekly_digest",
            "reconcile_approval_notifications", "process_punches",
            "check_device_health", "reap_stuck_reports", "check_alerts",
            "backup_verify", "device_sync",
            # Phase 111.17: autosave snapshots are working state, not records,
            # so they are purged on a retention window like generated reports.
            "purge_expired_drafts",
            # Phase T4.4/T4.5: task due reminders, review nudges and overdue
            # escalation. Registered as CRITICAL because its failure mode is
            # silence — nobody is chased, nothing escalates, and the first
            # symptom is a missed deadline nobody was warned about.
            "send_appraisal_reminders",
            "send_task_reminders",
            # Phase T5.8: the daily evidence snapshot. Not critical — a missed
            # day loses granularity and can be backfilled with --date.
            "snapshot_task_evidence",
            # Phase ASSET-LIFECYCLE-DISPOSAL: warranty, AMC and end-of-life alerts.
            "send_asset_lifecycle_alerts",
        }
        assert commands == expected, (
            "The crontab changed. Add the new job to monitoring.heartbeat."
            "CRON_JOBS and to this list, or it ships unmonitored.")


# ===========================================================================
# metrics
# ===========================================================================
class TestMetrics:
    def test_the_board_collects_without_error(self):
        board = metrics.collect()
        assert board["status"] in ("green", "amber", "red")
        assert len(board["sections"]) == len(metrics.SECTIONS)

    def test_a_failing_probe_becomes_a_red_metric_not_a_crash(self, monkeypatch):
        """A monitoring page that 500s tells you nothing when you most need it."""
        def explode():
            raise RuntimeError("probe exploded")

        wrapped = metrics.safe(explode)
        result = wrapped()
        assert result[0]["state"] == "red"
        assert "probe exploded" in result[0]["detail"]

    def test_database_metrics_are_green_on_a_healthy_system(self):
        results = {m["key"]: m for m in metrics.database()}
        assert results["db_reachable"]["state"] == "green"
        assert results["db_latency_ms"]["value"] is not None

    def test_redis_absent_is_green_not_red(self, settings):
        """Running without Redis is a supported configuration, so flagging it
        red would train people to ignore the board."""
        settings.REDIS_URL = ""
        assert metrics.redis()[0]["state"] == "green"

    def test_mapping_and_derivation_queues_are_reported_separately(self):
        keys = {m["key"] for m in metrics.devices()}
        assert "punches_awaiting_derivation" in keys
        assert "punches_awaiting_mapping" in keys

    def test_backups_are_red_until_one_has_run(self):
        results = {m["key"]: m for m in metrics.backups()}
        assert results["backup_backup"]["state"] == "red"

    def test_backups_go_green_after_a_successful_run(self, beat):
        beat("BACKUP", minutes_ago=60)
        beat("BACKUP_MEDIA", minutes_ago=60)
        beat("BACKUP_VERIFIED", minutes_ago=60)
        results = {m["key"]: m for m in metrics.backups()}
        assert all(results[key]["state"] == "green" for key in results)

    def test_a_backup_older_than_a_day_is_amber(self, beat):
        beat("BACKUP", minutes_ago=27 * 60)
        results = {m["key"]: m for m in metrics.backups()}
        assert results["backup_backup"]["state"] == "amber"

    def test_thresholds_are_published_with_the_value(self):
        """The dashboard, the alert rule and the runbook must not each hold
        their own opinion about what 'too many' means."""
        for metric in metrics.database():
            if metric["key"] == "disk_free_pct":
                assert metric["thresholds"] == {"amber": 20, "red": 10}


# ===========================================================================
# alerting
# ===========================================================================
class TestAlerting:
    def test_a_missing_backup_fires_critical(self):
        firing = {alert["key"]: alert for alert in alerts.evaluate()}
        assert "backup_backup" in firing
        assert firing["backup_backup"]["severity"] == "critical"

    def test_a_healthy_backup_does_not_fire(self, beat):
        beat("BACKUP", minutes_ago=60)
        assert "backup_backup" not in {a["key"] for a in alerts.evaluate()}

    def test_redis_down_is_info_and_never_paged(self, settings):
        """The system is designed to survive Redis. Paging at 03:00 for a
        condition that degrades nothing trains people to ignore alerts."""
        severity, _title, _action = alerts.RULES["redis_reachable"]
        assert severity == "info"

    def test_an_alert_notifies_once_then_deduplicates(self, alert_recipients):
        mail.outbox.clear()
        first = alerts.process()
        assert "backup_backup" in first["notified"]
        assert len(mail.outbox) >= 1

        mail.outbox.clear()
        second = alerts.process()
        assert "backup_backup" in second["suppressed"]
        assert "backup_backup" not in second["notified"]
        assert mail.outbox == [], "a still-firing alert must not re-notify"

    def test_a_persisting_alert_digests_after_a_day(self, alert_recipients):
        alerts.process()
        AuditLog.objects.filter(changes__alert_key="backup_backup").update(
            created_at=timezone.now() - timedelta(hours=25))
        mail.outbox.clear()
        result = alerts.process()
        assert "backup_backup" in result["notified"]

    def test_recovery_notifies_once(self, alert_recipients, beat):
        alerts.process()               # fires
        beat("BACKUP", minutes_ago=10)
        beat("BACKUP_MEDIA", minutes_ago=10)
        beat("BACKUP_VERIFIED", minutes_ago=10)
        mail.outbox.clear()

        result = alerts.process()
        assert "backup_backup" in result["resolved"]
        assert any("RESOLVED" in message.subject for message in mail.outbox)

        mail.outbox.clear()
        again = alerts.process()
        assert "backup_backup" not in again["resolved"]

    def test_every_alert_carries_an_action(self):
        """An alert nobody knows what to do with is noise."""
        for key, (_severity, title, action) in alerts.RULES.items():
            assert action, f"{key} has no remediation text"
            assert title, f"{key} has no title"

    def test_every_alert_rule_maps_to_a_declared_metric(self):
        """A rule keyed on a metric that no longer exists never fires, and
        nothing would ever tell you.

        Checked against the declared key set rather than one collect() call:
        several metrics are conditional (Redis needs REDIS_URL and a client
        exposing maxmemory; the sync rate needs an ingest batch), so asserting
        against whatever the current environment produced would have passed
        while three rules were dead.
        """
        missing = set(alerts.RULES) - metrics.ALL_METRIC_KEYS
        assert not missing, f"alert rules reference unknown metrics: {missing}"

    def test_the_declared_key_set_matches_what_is_emitted(self, settings):
        """The other half: a metric emitted but not declared would let a dead
        alert rule slip past the test above."""
        from biometric.models import BiometricDevice, DeviceSyncLog

        emitted = self._keys()
        settings.REDIS_URL = "redis://localhost:6379/0"
        emitted |= self._keys()

        device = BiometricDevice.objects.create(
            name="Verify Gate", label="verify", is_active=True)
        DeviceSyncLog.objects.create(
            device=device, sync_type=DeviceSyncLog.SyncType.LIVE,
            status=DeviceSyncLog.Status.SUCCESS, records_received=1,
            records_created=1)
        emitted |= self._keys()

        undeclared = emitted - metrics.ALL_METRIC_KEYS - {"cron_" + job.lower()
                                                          for job in heartbeat.CRON_JOBS}
        assert not undeclared, (
            f"metrics emitted but not declared in ALL_METRIC_KEYS: {undeclared}")

    @staticmethod
    def _keys():
        return {m["key"] for section in metrics.collect()["sections"]
                for m in section["metrics"]}

    def test_alerts_are_recorded_even_with_no_recipients(self, settings):
        """Delivery is optional; the record is not. Otherwise a misconfigured
        mailer means an incident leaves no trace at all."""
        settings.ALERT_EMAILS = ""
        settings.ALERT_WEBHOOK_URL = ""
        alerts.process()
        assert AuditLog.objects.filter(changes__event="ALERT_FIRING").exists()

    def test_delivery_failure_does_not_break_the_run(self, settings, monkeypatch):
        settings.ALERT_EMAILS = "ops@nif.test"

        def explode(*args, **kwargs):
            raise RuntimeError("smtp is down")

        monkeypatch.setattr("monitoring.alerts.send_mail", explode)
        result = alerts.process()  # must not raise
        assert result["firing"]

    def test_cron_jobs_raise_one_alert_not_fifteen(self):
        """A dead cron container is one fact, not fifteen emails."""
        firing = [a for a in alerts.evaluate() if a["key"].startswith("cron")]
        assert len(firing) <= 1


# ===========================================================================
# API
# ===========================================================================
class TestMonitoringApi:
    ENDPOINTS = ["monitoring-health", "monitoring-cron", "monitoring-alerts"]

    @pytest.mark.parametrize("url_name", ENDPOINTS)
    def test_hr_and_admin_can_read(self, url_name, auth, hr_user, admin_user):
        assert auth(hr_user).get(reverse(url_name)).status_code == 200

    @pytest.mark.parametrize("url_name", ENDPOINTS)
    def test_managers_and_employees_cannot(self, url_name, auth, manager, employee):
        assert auth(manager).get(reverse(url_name)).status_code == 403
        assert auth(employee).get(reverse(url_name)).status_code == 403

    @pytest.mark.parametrize("url_name", ENDPOINTS)
    def test_anonymous_cannot(self, url_name, api):
        assert api.get(reverse(url_name)).status_code in (401, 403)

    def test_health_payload_shape(self, auth, admin_user):
        data = auth(admin_user).get(reverse("monitoring-health")).data
        assert set(data) == {"status", "generated_at", "sections", "summary"}
        for section in data["sections"]:
            assert {"key", "label", "state", "metrics"} <= set(section)

    def test_cron_payload_lists_every_registered_job(self, auth, admin_user):
        data = auth(admin_user).get(reverse("monitoring-cron")).data
        assert data["registered"] == len(heartbeat.CRON_JOBS)
        assert len(data["jobs"]) == len(heartbeat.CRON_JOBS)

    def test_alerts_payload_reports_whether_delivery_is_configured(
            self, auth, admin_user, settings):
        settings.ALERT_EMAILS = ""
        settings.ALERT_WEBHOOK_URL = ""
        data = auth(admin_user).get(reverse("monitoring-alerts")).data
        assert data["configured"] is False


# ===========================================================================
# reaper (audit finding M4)
# ===========================================================================
class TestReapStuckReports:
    def test_a_stuck_report_is_failed_with_a_truthful_reason(self, admin_user):
        from django.core.management import call_command

        from reports.models import ReportRun

        run = ReportRun.objects.create(
            report_type="monthly_attendance", status=ReportRun.Status.GENERATING,
            requested_by=admin_user)
        ReportRun.objects.filter(pk=run.pk).update(
            created_at=timezone.now() - timedelta(hours=2))

        call_command("reap_stuck_reports", "--quiet")

        run.refresh_from_db()
        assert run.status == ReportRun.Status.FAILED
        assert "request the report again" in run.error.lower()

    def test_a_recent_report_is_left_alone(self, admin_user):
        from django.core.management import call_command

        from reports.models import ReportRun

        run = ReportRun.objects.create(
            report_type="monthly_attendance", status=ReportRun.Status.GENERATING,
            requested_by=admin_user)
        call_command("reap_stuck_reports", "--quiet")
        run.refresh_from_db()
        assert run.status == ReportRun.Status.GENERATING

    def test_the_reaper_records_its_own_heartbeat(self, admin_user):
        from django.core.management import call_command

        call_command("reap_stuck_reports", "--quiet")
        assert heartbeat.status("REAP_STUCK_REPORTS")["state"] == "green"
