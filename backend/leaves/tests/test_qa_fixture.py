"""
Phase 100.1 — browser QA fixtures for the leave, notification and report modules.

WHY THIS EXISTS

The Phase 100 audit could measure Memo, Minute, Circular and Inventory because each has a
generator like this one. Leave, Notifications and Reports had none, so the audit could only
say "the global CSS fixes apply to them" — an inference, not a measurement. Blockers 4, 5
and 6 of Phase 100.1 ask for exactly that gap to be closed.

The rule the Phase 47 rewrite established still holds and is the whole point: fixtures are
NOT hand-written. A hand-written payload drifts the moment a serializer gains a field, and
the harness then screenshots a page that no longer exists while reporting it clean. Every
payload below is whatever the real API returned for a real object built through the real
endpoints.

Marked `qa` and excluded from the default run (see pytest.ini) because it writes outside
the backend tree. Regenerate with:

    cd backend && DATABASE_ENGINE=sqlite3 DJANGO_DEBUG=True DJANGO_SECRET_KEY=test-secret \
      .venv/bin/python -m pytest -q -p no:randomly -m qa leaves/tests/test_qa_fixture.py
"""
import datetime
import json
import pathlib

import pytest
from rest_framework.test import APIClient

from users.models import User

FIXTURE_PATH = (pathlib.Path(__file__).resolve().parents[3]
                / "frontend" / "qa" / "leave-fixtures.json")


def _plain(payload):
    """DRF payloads carry UUIDs, dates and Decimals; JSON-round-trip them to plain data."""
    return json.loads(json.dumps(payload, default=str))


def _get(client, actor, url):
    client.force_authenticate(actor)
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code, response.data)
    return _plain(response.data)


@pytest.fixture
def leave_cast(db):
    """A maker, the department head who reviews their leave, and an HR approver.

    Long-tenured permanent staff on purpose: the category engine resolves Category A
    (Annual 12 / Sick 12) for them, so the balance panels render real non-zero numbers
    instead of the zeroes a freshly-joined user would produce.
    """
    common = dict(
        employment_type=User.EmploymentType.PERMANENT,
        date_of_joining=datetime.date(2018, 1, 1),
        department="ENG",
    )
    maker = User.objects.create_user(
        username="qa_leave_maker", email="qa_leave_maker@nif.test", password="pass12345",
        first_name="Leave", last_name="Applicant", role=User.Roles.MAKER,
        designation="Officer", **common,
    )
    head = User.objects.create_user(
        username="qa_leave_head", email="qa_leave_head@nif.test", password="pass12345",
        first_name="Dept", last_name="Head", role=User.Roles.CHECKER,
        designation="Department Head", **common,
    )
    hr = User.objects.create_user(
        username="qa_leave_hr", email="qa_leave_hr@nif.test", password="pass12345",
        first_name="HR", last_name="Approver", role=User.Roles.APPROVER,
        designation="Head of Human Resources", **common,
    )
    return maker, head, hr


def _apply(client, maker, head, *, start, end, reason, leave_type="annual"):
    client.force_authenticate(maker)
    response = client.post("/api/v1/leaves/", {
        "leave_type": leave_type,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "reason": reason,
        "approver": str(head.id),
    }, format="json")
    assert response.status_code == 201, response.data
    return response.data["id"]


@pytest.mark.qa
@pytest.mark.django_db
def test_dump_the_leave_qa_fixtures(leave_cast, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    client = APIClient()
    maker, head, hr = leave_cast

    # A mid-week window well clear of the seeded holidays, so the working-day count
    # is not silently reduced to something that reads as a bug in the screenshot.
    monday = datetime.date.fromisocalendar(2026, 24, 1)

    # ---- 1. A PENDING application, which is what the approver queue renders ------
    # The reason is deliberately long. A one-word reason fits any layout; the wrap
    # behaviour that Phase 100.3 cares about only shows up on real prose.
    pending_id = _apply(
        client, maker, head,
        start=monday, end=monday + datetime.timedelta(days=2),
        reason=(
            "Attending the annual regional internet governance forum in Pokhara as "
            "the Foundation's nominated delegate. Handover notes have been shared "
            "with the ICT team and the standing infrastructure roster is covered."
        ),
    )

    # ---- 2. An APPROVED application, for the history and calendar views ----------
    approved_id = _apply(
        client, maker, head,
        start=monday + datetime.timedelta(days=21),
        end=monday + datetime.timedelta(days=22),
        reason="Family obligation; cover arranged with the operations desk.",
    )
    client.force_authenticate(head)
    decision = client.post(
        f"/api/v1/leaves/{approved_id}/dept-head-review/",
        {"decision": "approve", "remarks": "Cover is in place. Approved."},
        format="json",
    )
    assert decision.status_code == 200, decision.data

    # ---- 3. A REJECTED application, so the rejected state has real remarks -------
    rejected_id = _apply(
        client, maker, head,
        start=monday + datetime.timedelta(days=35),
        end=monday + datetime.timedelta(days=39),
        reason="Extended personal leave.",
    )
    client.force_authenticate(head)
    rejection = client.post(
        f"/api/v1/leaves/{rejected_id}/dept-head-review/",
        {
            "decision": "reject",
            "remarks": (
                "Clashes with the quarter-end audit window; please resubmit for the "
                "first week of the following month."
            ),
        },
        format="json",
    )
    assert rejection.status_code == 200, rejection.data

    # ---- payloads, exactly as each screen's own request would receive them -------
    payload = {
        "pending_id": pending_id,
        "approved_id": approved_id,
        "rejected_id": rejected_id,
        # The applicant's own identity — the harness's auth stub renders as this user
        # for the leave screens, so `is_mine` style comparisons resolve correctly.
        "maker": _plain({
            "id": str(maker.id), "username": maker.username,
            "full_name": maker.get_full_name(), "role": maker.role,
            "department": maker.department, "designation": maker.designation,
        }),
        "head": _plain({
            "id": str(head.id), "full_name": head.get_full_name(), "role": head.role,
        }),
        # The maker's own list — MyApplications and the leave dashboard.
        "my_leaves": _get(client, maker, "/api/v1/leaves/"),
        "my_entitlements": _get(client, maker, "/api/v1/leaves/my-entitlements/"),
        "my_balance": _get(client, maker, "/api/v1/leaves/balance"),
        "leave_policy": _get(client, maker, "/api/v1/leaves/leave-policy/"),
        # The reviewer's queue — PendingApprovals, and the review drawer's detail call.
        "pending_queue": _get(client, head, "/api/v1/leaves/?queue=actionable"),
        "pending_detail": _get(client, head, f"/api/v1/leaves/{pending_id}/"),
        # Read as the APPLICANT, not the head: once a leave is decided it leaves the
        # reviewer's queryset (a decided leave is no longer theirs to act on) and the
        # detail endpoint 404s for them. The applicant is also the realistic caller —
        # these two payloads feed the history and calendar screens, not the queue.
        "approved_detail": _get(client, maker, f"/api/v1/leaves/{approved_id}/"),
        "rejected_detail": _get(client, maker, f"/api/v1/leaves/{rejected_id}/"),
        # ApplyLeave populates its approver <select> from the user directory. The
        # endpoint is /users/ (readable by any authenticated user), NOT /admin/users/
        # — that one is admin-only and 403s for an applicant, which is exactly who
        # loads this screen. See adminService.getUsers().
        "users": _get(client, maker, "/api/v1/users/"),
    }

    # ---- notifications (Blocker 5) ----------------------------------------------
    # The approve and reject above already emitted real notification rows for the
    # applicant, which is why nothing here is seeded by hand: the categories, titles
    # and action_urls are whatever the notification layer really produced.
    #
    # Blocker 5 names four states to cover, and two of them need more than the two
    # rows those decisions left behind:
    #
    #   unread      the two decision notifications, untouched
    #   read        one is marked read through the real /read/ endpoint below
    #   long lists  a further eight applications are filed and decided, so the list
    #               is long enough to overflow a 390x780 viewport — which is the only
    #               condition under which the panel's scroll can be tested at all
    #   permission  `preferences` returns a row per category with the in-app/email
    #               toggles, which is the permission surface the page renders
    # Single-day SICK leave, not annual. The three applications above already consume
    # most of the 12-day annual entitlement, and the API enforces that balance for
    # real — eight more two-day annual requests are rejected with
    # "exceed your remaining Annual Leave balance", which is the serializer working
    # correctly, not a fixture problem. Sick carries its own 12-day entitlement.
    for offset in range(8):
        day = monday + datetime.timedelta(days=63 + offset * 7)
        extra = _apply(
            client, maker, head, leave_type="sick",
            start=day, end=day,
            reason=f"Scheduled absence {offset + 1} — cover arranged in advance.",
        )
        client.force_authenticate(head)
        verdict = "approve" if offset % 2 == 0 else "reject"
        response = client.post(
            f"/api/v1/leaves/{extra}/dept-head-review/",
            {"decision": verdict,
             "remarks": f"Reviewed under the standing delegation ({verdict}d)."},
            format="json",
        )
        assert response.status_code == 200, response.data

    # Mark exactly one row read, through the endpoint the UI uses — so the fixture
    # carries a genuine read/unread mix rather than a hand-edited is_read flag.
    client.force_authenticate(maker)
    inbox = _plain(client.get("/api/v1/notifications/").data)
    first_id = (inbox.get("results") or inbox)[0]["id"]
    marked = client.post(f"/api/v1/notifications/{first_id}/read/")
    assert marked.status_code in (200, 204), marked.status_code

    payload["notifications"] = _get(client, maker, "/api/v1/notifications/")
    payload["notification_unread"] = _get(client, maker, "/api/v1/notifications/unread-count/")
    payload["notification_prefs"] = _get(client, maker, "/api/v1/notifications/preferences/")
    payload["notification_read_id"] = first_id

    # ---- reports (Blocker 6) -----------------------------------------------------
    # Real ReportRun rows, requested through the real endpoint. An empty list would
    # have let ReportsHub render its empty state and the probe call that clean — the
    # opposite of what Blocker 6 asks for, which is a LARGE table with export buttons.
    # One run per format the type supports, so the hub's PDF/Excel/CSV controls all
    # render rather than only the default one.
    requested = [
        ("monthly_workforce_summary", "excel"),
        ("department_attendance", "pdf"),
        ("attendance_vs_leave", "csv"),
        ("late_arrival_summary", "pdf"),
        ("overtime_summary", "excel"),
        ("comp_off_report", "csv"),
        ("shift_utilization", "pdf"),
        ("attendance_vs_wfh", "excel"),
    ]
    client.force_authenticate(hr)
    for report_type, fmt in requested:
        run = client.post("/api/v1/reports/request/",
                          {"report_type": report_type, "params": {"format": fmt}},
                          format="json")
        assert run.status_code in (200, 201, 202), (report_type, run.status_code, run.data)

    payload["reports_mine"] = _get(client, hr, "/api/v1/reports/?mine=true")
    payload["reports_all"] = _get(client, hr, "/api/v1/reports/")
    payload["hr"] = _plain({
        "id": str(hr.id), "full_name": hr.get_full_name(), "role": hr.role,
    })

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(payload, indent=1, sort_keys=True))

    # A generator that silently writes an empty file is worse than one that fails:
    # the harness would render empty states and the probe would call them clean.
    assert payload["my_leaves"], "no leaves in the applicant's own list"
    assert payload["pending_detail"].get("id"), "review detail payload is empty"

    def _rows(block):
        return block.get("results", block) if isinstance(block, dict) else block

    # Each of these guards a Blocker: an empty block means the harness renders an
    # empty state and the probe reports it clean, which is how an unmeasured module
    # gets certified. Fail here instead.
    assert len(_rows(payload["notifications"])) >= 8, "notification list too short to scroll"
    assert any(row["is_read"] for row in _rows(payload["notifications"])), "no read row"
    assert any(not row["is_read"] for row in _rows(payload["notifications"])), "no unread row"
    assert payload["notification_prefs"], "no notification permission rows"
    assert len(_rows(payload["reports_mine"])) >= 8, "report table too small to be wide"
