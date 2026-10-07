"""
Leave-workflow notification triggers (in-app + email).

Recipients are resolved dynamically from the applicant's department (Dept Head)
and the HR pool — never hardcoded — and every address is the user's account
(login) email. Every send is best-effort and idempotency-keyed: a notification
failure can NEVER break apply / approve / reject, and a transition can't send the
same email twice.

Workflow: the Department Head's approval is FINAL and grants the leave; HR and
Admin get visibility/record copies but are not a second approval stage.

Trigger map (see the four public functions):
  1. leave_submitted   - new request       -> Dept Head(s) [+ HR & Admin record]
  2. leave_l1_approved - (legacy two-stage) -> HR [+ employee "stage 1 passed"]
  3. leave_finalized   - grant/HR decision  -> employee [+ HR & Admin record]
  4. leave_rejected_l1 - Dept Head reject   -> employee (reason) [+ HR & Admin record]
"""
import logging

from django.conf import settings

from config.nepali_dates import to_bs
from notifications.dispatcher import notify
from notifications.models import Category
from users.models import User
from . import services
from .models import Leave

logger = logging.getLogger("leaves.notifications")

# status_key -> (accent color, badge label)
_STATUS_META = {
    "submitted": ("#F59E0B", "Awaiting Review"),
    "pending_hr": ("#F59E0B", "Awaiting HR"),
    "approved": ("#10B981", "Approved"),
    "rejected": ("#DC2626", "Rejected"),
}


def _safe(label, fn):
    try:
        fn()
    except Exception:  # noqa: BLE001 - notifications must never break the workflow
        logger.exception("leave notification failed (%s)", label)


def _bs(value):
    return to_bs(value) or "—"


def _dept_heads(applicant):
    """Active Department Heads (checkers) in the applicant's department; falls
    back to all Dept Heads if none are assigned to that department."""
    qs = User.objects.filter(role=User.Roles.CHECKER, is_active=True).exclude(id=applicant.id)
    if applicant.department_ref_id:
        scoped = qs.filter(department_ref_id=applicant.department_ref_id)
        if scoped.exists():
            return list(scoped)
    elif applicant.department:
        scoped = qs.filter(department__iexact=applicant.department)
        if scoped.exists():
            return list(scoped)
    return list(qs)


def _hr_users(exclude=None):
    qs = User.objects.filter(role=User.Roles.APPROVER, is_active=True)
    if exclude is not None:
        qs = qs.exclude(id=exclude.id)
    return list(qs)


def _admin_users(exclude=None):
    qs = User.objects.filter(role=User.Roles.ADMIN, is_active=True)
    if exclude is not None:
        qs = qs.exclude(id=exclude.id)
    return list(qs)


def _record_recipients(exclude=None):
    """HR + Admin, deduplicated — the oversight audience that is notified for the
    record on submit and on the Department Head's decision (they do not act)."""
    seen, out = set(), []
    for u in _hr_users(exclude=exclude) + _admin_users(exclude=exclude):
        if u.id not in seen:
            seen.add(u.id)
            out.append(u)
    return out


def _actor_line(actor, when):
    if not actor:
        return ""
    name = actor.get_full_name() or actor.username
    if when:
        return f"{name} on {_bs(when)} BS / {when.date()}"
    return name


def _detail_ctx(leave, status_key, actor_line="", remarks=""):
    color, label = _STATUS_META.get(status_key, ("#2563EB", ""))
    days = services.working_leave_days(leave)
    ctx = {
        "status_color": color, "status_label": label,
        "employee_name": leave.user.get_full_name() or leave.user.username,
        "department": leave.user.department_name or "",
        "leave_type": leave.get_leave_type_display(),
        "start_ad": str(leave.start_date), "start_bs": _bs(leave.start_date),
        "end_ad": str(leave.end_date), "end_bs": _bs(leave.end_date),
        "working_days": days,
        "reason": leave.reason or "",
        "actor_line": actor_line, "remarks": remarks or "",
        # The request's own identifier. A Leave has no human-readable number —
        # the UUID is what the URL carries and what support will be quoted, so
        # it is what the email shows.
        "request_ref": str(leave.id),
    }
    return ctx, days


# ---------------------------------------------------------------------------
# Trigger 1 — new request submitted
# ---------------------------------------------------------------------------
def leave_submitted(leave):
    from .approvals import actionable_approver_ids
    ctx, days = _detail_ctx(leave, "submitted")
    emp, ltype = ctx["employee_name"], ctx["leave_type"]
    title = f"New Leave Request — {emp} ({ltype})"
    body = f"{emp} submitted a {ltype} request for {days} working day(s), awaiting your review."
    from .approvals import routes_to_board
    board_routed = routes_to_board(leave.user)
    category = Category.LEAVE_SUBMITTED
    if board_routed:
        # A Department Head's (or Board member's) leave, decided by the Board
        # (Phase BOD). Its own category so a Board member can find these among
        # everything else they are copied on.
        title = f"Department Head Leave — Board Approval Required — {emp} ({ltype})"
        body = (f"{emp} submitted a {ltype} request for {days} working day(s). As a "
                "Department Head's leave, it awaits a decision by the Board of Directors.")
        category = Category.LEAVE_BOARD_APPROVAL_REQUIRED

    # SINGLE SOURCE: the exact user(s) who must act (Dept Head, or HR fallback when
    # the department has no active Dept Head). These are guaranteed to find the
    # leave in their pending queue (leaves.approvals.pending_actionable_leaves).
    actionable = actionable_approver_ids(leave)
    # WHY it reached HR, when it reached HR for want of a Department Head
    # (Phase DEPARTMENT-GOVERNANCE-HARDENING). HR used to be handed these with
    # no indication that they were covering a gap, so the gap stayed open: the
    # person who could fix it never learned it existed.
    fallback_note = ""
    if not board_routed:
        from . import services as _services

        if not _services.department_has_dept_head(leave.user):
            department = (getattr(leave.user.department_ref, "name", "")
                          or getattr(leave.user, "department", "")
                          or "Their department")
            fallback_note = (f" This has come to you because {department} has no "
                             "active Department Head — please assign one.")

    for approver in User.objects.filter(id__in=actionable):
        # Only a Board member gets the Board category: an HR fallback grantor is
        # told the same facts under the ordinary leave category.
        this_category = (category if approver.role == User.Roles.BOD
                         else Category.LEAVE_SUBMITTED)
        this_body = (body + fallback_note
                     if fallback_note and approver.role == User.Roles.APPROVER
                     else body)
        _safe(f"submitted->approver:{approver.id}", lambda approver=approver, c=this_category, bd=this_body: notify(
            approver, c, title, bd, action_url="/leave/pending",
            idempotency_key=f"leave-{leave.id}-submitted-head-{approver.id}",
            email_context=ctx, object_id=leave.id,
        ))

    # HR + Admin copied for visibility/record — but NOT anyone already actionable
    # (an HR fallback grantor gets the actionable notice, not a CC).
    if getattr(settings, "NOTIFY_CC_HR_ON_SUBMIT", True):
        for u in _record_recipients():
            if u.id in actionable:
                continue
            _safe(f"submitted->cc:{u.id}", lambda u=u: notify(
                u, Category.LEAVE_SUBMITTED, f"{title} (CC)",
                body + " You are copied for visibility.", action_url="/leave/pending",
                idempotency_key=f"leave-{leave.id}-submitted-cc-{u.id}",
                email_context=ctx, object_id=leave.id,
            ))


# ---------------------------------------------------------------------------
# Trigger 2 — Dept Head approved (L1) -> pending HR
# ---------------------------------------------------------------------------
def leave_l1_approved(leave):
    actor_line = _actor_line(leave.department_head_reviewer, leave.department_head_action_date)
    ctx, days = _detail_ctx(leave, "pending_hr", actor_line=actor_line, remarks=leave.remarks or "")
    emp, ltype = ctx["employee_name"], ctx["leave_type"]

    title = "Action Required — Leave Request Awaiting Your Approval"
    body = f"{emp}'s {ltype} request ({days} working day(s)) was approved by the Department Head and awaits your final approval."
    for hr in _hr_users():
        _safe(f"l1->hr:{hr.id}", lambda hr=hr: notify(
            hr, Category.LEAVE_SUBMITTED, title, body, action_url="/leave/pending",
            idempotency_key=f"leave-{leave.id}-l1-hr-{hr.id}",
            email_context=ctx, object_id=leave.id,
        ))

    if getattr(settings, "NOTIFY_EMPLOYEE_ON_L1", True):
        _safe("l1->emp", lambda: notify(
            leave.user, Category.LEAVE_SUBMITTED, "Leave Update — Stage 1 Approved",
            "Your leave request passed Stage 1 (Department Head) and now awaits HR's final decision.",
            action_url="/leave/my-applications",
            idempotency_key=f"leave-{leave.id}-l1-emp",
            email_context=ctx, object_id=leave.id,
        ))


# ---------------------------------------------------------------------------
# Trigger 3 — HR final decision (approved / rejected)
# ---------------------------------------------------------------------------
def leave_finalized(leave):
    approved = leave.status == Leave.Status.APPROVED
    status_key = "approved" if approved else "rejected"
    # Whoever finalized it: the Department Head grants directly now, so fall back
    # to the DH reviewer/date when HR did not act (legacy two-stage still works).
    actor = (leave.board_reviewer or leave.hr_reviewer or leave.department_head_reviewer
             or leave.approver)
    when = leave.board_action_date or leave.hr_action_date or leave.department_head_action_date
    actor_line = _actor_line(actor, when)
    ctx, days = _detail_ctx(leave, status_key, actor_line=actor_line, remarks=leave.remarks or "")
    dates = f"{ctx['start_ad']} to {ctx['end_ad']}"

    if approved:
        title, cat = f"Leave Approved — {dates}", Category.LEAVE_APPROVED
        body = f"Your {ctx['leave_type']} ({days} working day(s)) has been approved and granted."
    else:
        title, cat = f"Leave Rejected — {dates}", Category.LEAVE_REJECTED
        body = f"Your {ctx['leave_type']} request has been rejected."

    _safe("final->emp", lambda: notify(
        leave.user, cat, title, body, action_url="/leave/my-applications",
        idempotency_key=f"leave-{leave.id}-final-{status_key}",
        email_context=ctx, object_id=leave.id,
    ))

    # Record copy to HR + Admin (oversight; not an approval step). Excludes the
    # actor so a fallback-granting HR user is not notified about their own action.
    if getattr(settings, "NOTIFY_AUDIT_COPY_ON_FINAL", True):
        for u in _record_recipients(exclude=actor):
            _safe(f"final->record:{u.id}", lambda u=u: notify(
                u, cat, f"{title} (record)",
                f"{ctx['employee_name']}'s {ctx['leave_type']} was {status_key}.",
                action_url="/leave/pending",
                idempotency_key=f"leave-{leave.id}-final-{status_key}-record-{u.id}",
                email_context=ctx, object_id=leave.id,
            ))


# ---------------------------------------------------------------------------
# Trigger 4 — Dept Head rejected at L1
# ---------------------------------------------------------------------------
def leave_rejected_l1(leave):
    # Named for the decider it had before the Board existed; a Board-routed leave
    # is rejected BY THE BOARD, and saying "the Department Head" to a department
    # head about their own leave would be wrong twice over (Phase BOD).
    board = bool(leave.board_reviewer_id)
    decider = "the Board of Directors" if board else "the Department Head"
    actor_line = (_actor_line(leave.board_reviewer, leave.board_action_date) if board
                  else _actor_line(leave.department_head_reviewer, leave.department_head_action_date))
    ctx, days = _detail_ctx(leave, "rejected", actor_line=actor_line, remarks=leave.remarks or "")
    dates = f"{ctx['start_ad']} to {ctx['end_ad']}"
    _safe("l1reject->emp", lambda: notify(
        leave.user, Category.LEAVE_REJECTED, f"Leave Rejected — {dates}",
        f"Your leave request was rejected by {decider}. See the remarks for details.",
        action_url="/leave/my-applications",
        # Shares the 'final-rejected' key so a rejection is only ever emailed once.
        idempotency_key=f"leave-{leave.id}-final-rejected",
        email_context=ctx, object_id=leave.id,
    ))

    # Record copy to HR + Admin (oversight; not an approval step).
    actor = leave.board_reviewer or leave.department_head_reviewer or leave.approver
    if getattr(settings, "NOTIFY_AUDIT_COPY_ON_FINAL", True):
        for u in _record_recipients(exclude=actor):
            _safe(f"l1reject->record:{u.id}", lambda u=u: notify(
                u, Category.LEAVE_REJECTED, f"Leave Rejected — {dates} (record)",
                f"{ctx['employee_name']}'s {ctx['leave_type']} was rejected by {decider}.",
                action_url="/leave/pending",
                idempotency_key=f"leave-{leave.id}-final-rejected-record-{u.id}",
                email_context=ctx, object_id=leave.id,
            ))
