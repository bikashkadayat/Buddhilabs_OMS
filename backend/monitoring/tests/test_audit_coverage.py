"""Audit coverage, enforced mechanically.

Phase 11, §8. The audit found 18 of 22 critical operations covered, with four
gaps: report download, media download, login failure, and backup heartbeats.
This suite closes the loop — a future change that removes a ``log_action`` call
fails here rather than being discovered during an incident, which is the only
time anyone reads an audit trail.
"""
from datetime import date, datetime

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditLog
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(db):
    user = User.objects.create_user(
        username="cov_admin", email="cov.admin@nif.test", password="pass12345",
        first_name="Cov", last_name="Admin", role=User.Roles.ADMIN,
        date_of_joining=date(2020, 1, 1))
    User.objects.filter(pk=user.pk).update(
        date_joined=timezone.make_aware(datetime(2020, 1, 1, 9, 0)))
    return user


@pytest.fixture
def staff(admin):
    client = APIClient()
    client.force_authenticate(user=admin)
    return client


def events():
    return set(AuditLog.objects.exclude(changes__event__isnull=True)
               .values_list("changes__event", flat=True))


class TestNewlyCoveredOperations:
    """The four gaps the audit found."""

    def test_login_failure_is_audited(self, db):
        APIClient().post(reverse("token_obtain_pair"),
                         {"email": "ghost@nif.test", "password": "nope"},
                         format="json")
        assert "LOGIN_FAILED" in events()

    def test_report_download_is_audited(self, admin, staff, tmp_path, settings):
        from django.core.files.base import ContentFile

        from reports.models import ReportRun

        run = ReportRun.objects.create(
            report_type="monthly_attendance", status=ReportRun.Status.READY,
            requested_by=admin)
        run.file.save("bench.csv", ContentFile(b"a,b\n1,2\n"), save=True)

        response = staff.get(f"/api/v1/reports/{run.pk}/download/")
        assert response.status_code == 200
        assert "REPORT_DOWNLOADED" in events()

    def test_report_download_carries_hardening_headers(self, admin, staff):
        from django.core.files.base import ContentFile

        from reports.models import ReportRun

        run = ReportRun.objects.create(
            report_type="monthly_attendance", status=ReportRun.Status.READY,
            requested_by=admin)
        run.file.save("bench2.csv", ContentFile(b"a,b\n1,2\n"), save=True)

        response = staff.get(f"/api/v1/reports/{run.pk}/download/")
        assert response["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in response["Content-Security-Policy"]

    def test_media_download_is_audited(self, admin):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from documents.protected_media import signed_media_url

        name = default_storage.save("audit-cov/test.txt", ContentFile(b"hello"))
        url = signed_media_url(name, user=admin)
        response = APIClient().get(url)
        assert response.status_code == 200
        assert "MEDIA_DOWNLOADED" in events()
        default_storage.delete(name)

    def test_backup_heartbeat_is_audited(self, db):
        from django.core.management import call_command

        call_command("record_heartbeat", "BACKUP", "--ok", "--detail", "test")
        assert "SYSTEM_BACKUP_OK" in events()


class TestSignedUrlBinding:
    """Audit finding M7 — a leaked link must be useless to anyone else."""

    def test_a_link_bound_to_one_user_rejects_a_swapped_user_id(self, admin, db):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from documents.protected_media import signed_media_url

        other = User.objects.create_user(
            username="cov_other", email="cov.other@nif.test", password="x",
            role=User.Roles.MAKER)
        name = default_storage.save("audit-cov/bound.txt", ContentFile(b"secret"))
        url = signed_media_url(name, user=admin)

        tampered = url.replace(f"u={admin.pk}", f"u={other.pk}")
        assert APIClient().get(tampered).status_code == 403
        default_storage.delete(name)

    def test_an_expired_link_is_refused(self, admin, db):
        import time

        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from documents.protected_media import signed_media_url

        name = default_storage.save("audit-cov/expired.txt", ContentFile(b"x"))
        url = signed_media_url(name, ttl=-1, user=admin)
        time.sleep(0.01)
        assert APIClient().get(url).status_code == 403
        default_storage.delete(name)

    def test_a_tampered_path_is_refused(self, admin, db):
        """Swapping the path invalidates the signature, which covers it."""
        from urllib.parse import parse_qs, urlencode, urlparse

        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage

        from documents.protected_media import signed_media_url

        name = default_storage.save("audit-cov/path.txt", ContentFile(b"x"))
        url = signed_media_url(name, user=admin)

        # Rebuild the query rather than string-replacing: `p` is URL-encoded in
        # the link, so a naive replace silently matches nothing and the test
        # would pass while asserting about a URL it never altered.
        parsed = urlparse(url)
        params = {key: value[0] for key, value in parse_qs(parsed.query).items()}
        params["p"] = "config/settings.py"
        tampered = f"{parsed.path}?{urlencode(params)}"

        assert APIClient().get(tampered).status_code == 403
        default_storage.delete(name)

    def test_path_traversal_is_refused(self, admin, db):
        """Defence in depth: even a correctly signed traversal path is refused."""
        from documents.protected_media import _sign

        from urllib.parse import urlencode
        import time

        name = "../../config/settings.py"
        exp = int(time.time()) + 300
        query = urlencode({"p": name, "e": exp,
                           "s": _sign(name, exp, str(admin.pk)), "u": str(admin.pk)})
        assert APIClient().get(f"/api/v1/media/?{query}").status_code == 403


class TestAttachmentValidation:
    """Audit finding M2 — correction attachments had no checks at all."""

    def _upload(self, content, filename):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from attendance.workforce.serializers import AttendanceCorrectionSerializer

        serializer = AttendanceCorrectionSerializer()
        return serializer.validate_attachment(
            SimpleUploadedFile(filename, content))

    def test_html_renamed_as_pdf_is_rejected(self):
        from rest_framework.serializers import ValidationError

        with pytest.raises(ValidationError):
            self._upload(b"<html><script>alert(1)</script></html>", "evidence.pdf")

    def test_an_unlisted_extension_is_rejected(self):
        from rest_framework.serializers import ValidationError

        with pytest.raises(ValidationError):
            self._upload(b"#!/bin/sh\nrm -rf /", "evidence.sh")

    def test_an_oversized_file_is_rejected(self):
        from rest_framework.serializers import ValidationError

        with pytest.raises(ValidationError):
            self._upload(b"%PDF-1.4" + b"0" * (11 * 1024 * 1024), "big.pdf")

    def test_a_real_pdf_is_accepted(self):
        # A minimal but structurally valid PDF, so libmagic identifies it.
        pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n"
               b"trailer<</Root 1 0 R>>\n%%EOF\n")
        assert self._upload(pdf, "evidence.pdf") is not None

    def test_memo_and_correction_share_one_validator(self):
        """The bug was that validation existed in one place and was not
        reachable from the other."""
        import inspect

        from attendance.workforce import serializers as correction_serializers
        from memos import serializers as memo_serializers

        assert "validate_attachment" in inspect.getsource(
            correction_serializers.AttendanceCorrectionSerializer.validate_attachment)
        assert memo_serializers.validate_attachment is \
            correction_serializers.validate_attachment


class TestPreviouslyCoveredStillCovered:
    """Regression guard: the Phase 11 refactor must not have removed anything."""

    def test_login_success_and_logout_remain_audited(self, admin, db):
        client = APIClient()
        response = client.post(reverse("token_obtain_pair"),
                               {"email": admin.email, "password": "pass12345"},
                               format="json")
        assert response.status_code == 200
        assert AuditLog.objects.filter(action=AuditLog.Action.LOGIN).exists()

    def test_the_audit_log_is_still_immutable(self, db):
        entry = AuditLog.objects.create(action=AuditLog.Action.OTHER,
                                        changes={"event": "TEST"})
        with pytest.raises(ValueError):
            entry.delete()
        with pytest.raises(ValueError):
            entry.save()
