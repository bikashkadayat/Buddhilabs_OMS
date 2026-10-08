"""Customer Success 2.0: from reactive support to proactive customer success.

Everything here answers "which customer needs us, and why" BEFORE they tell
us -- the health score, the command center, adoption, onboarding milestones,
the executive view, a customer's timeline, and the alerts that turn those
into action (SLA alerts on tickets, success tasks on customers).

PRIVACY, AND WHY IT WORKS UNDER ROW-LEVEL SECURITY. Not one function here
reads a tenant table. The inputs are platform tables only: ``PlatformMetric``
(daily counts per organization -- "3 people active", never who),
``Organization`` (incl. ``seat_count``), ``Subscription`` /
``SubscriptionEvent``, ``Payment``, ``SupportRequest``, ``TenantDomain`` and
``PlatformAuditLog``. That is also what lets the console compute it with no
tenant bound and no BYPASSRLS role.
"""
import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db.models import Avg, Min, Sum
from django.utils import timezone

from .context import no_tenant

logger = logging.getLogger(__name__)

WINDOW_DAYS = 30
# Roughly the working days in a 30-day window: a customer active on 20 of
# them is fully active, not two-thirds active.
WORKING_DAYS = 20
NEW_CUSTOMER_DAYS = 14

# The six components and their weights (sum 100). Kept as data so the
# console can show each one beside the total -- a score nobody can explain
# is a score nobody acts on.
WEIGHTS = {"login": 25, "adoption": 20, "attendance": 20, "tasks": 10,
           "documents": 10, "support": 15}
COMPONENT_LABELS = {"login": "Login activity", "adoption": "User adoption",
                    "attendance": "Attendance usage", "tasks": "Task usage",
                    "documents": "Document usage", "support": "Support volume"}
BANDS = ((75, "healthy", "Healthy"), (50, "watch", "Watch"), (0, "at_risk", "Needs attention"))


def _pct(value, full):
    return 0 if full <= 0 else max(0, min(100, round(100 * value / full)))


def _band(score):
    for floor, key, label in BANDS:
        if score >= floor:
            return key, label
    return "at_risk", "Needs attention"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def _usage(days=WINDOW_DAYS):
    """Per organization: per-key totals and per-key set of active days."""
    from .models import PlatformMetric

    since = timezone.localdate() - timedelta(days=days - 1)
    totals = defaultdict(lambda: defaultdict(int))
    active_days = defaultdict(lambda: defaultdict(set))
    last_active = {}
    for row in (PlatformMetric.objects.filter(day__gte=since, organization__isnull=False)
                .values("organization_id", "key", "day").annotate(n=Sum("count"))):
        org, key = row["organization_id"], row["key"]
        totals[org][key] += row["n"]
        if row["n"]:
            active_days[org][key].add(row["day"])
        if key == PlatformMetric.Key.ACTIVE_USER and row["n"]:
            if row["day"] == timezone.localdate():
                totals[org]["_today"] += row["n"]
            if org not in last_active or row["day"] > last_active[org]:
                last_active[org] = row["day"]
    return totals, active_days, last_active


def _support_by_org():
    from .models import SupportRequest

    now = timezone.now()
    out = defaultdict(lambda: {"open": 0, "critical": 0, "overdue": 0, "csat": None})
    for t in (SupportRequest.objects.filter(status__in=SupportRequest.OPEN_STATES,
                                            organization__isnull=False)
              .exclude(kind__in=[SupportRequest.Kind.FEEDBACK, SupportRequest.Kind.FEATURE])
              .only("organization_id", "priority", "sla_due_at", "sla_paused_at")):
        row = out[t.organization_id]
        row["open"] += 1
        row["critical"] += t.priority == SupportRequest.Priority.CRITICAL
        row["overdue"] += bool(t.sla_due_at and t.sla_paused_at is None and t.sla_due_at < now)
    for r in (SupportRequest.objects.filter(satisfaction_at__gte=now - timedelta(days=90))
              .values("organization_id").annotate(avg=Avg("satisfaction_rating"))):
        out[r["organization_id"]]["csat"] = r["avg"]
    return out


def _orgs():
    from .models import Organization

    return list(Organization.objects.exclude(status=Organization.Status.ARCHIVED)
                .select_related("subscription", "subscription__plan").order_by("name"))


# ---------------------------------------------------------------------------
# Part 2: the health score
# ---------------------------------------------------------------------------
def score_components(*, seats, usage, days_by_key, support):
    """The six components, each 0-100. Pure, so it can be tested alone."""
    from .models import PlatformMetric as M

    K = M.Key
    active_days = len(days_by_key.get(K.ACTIVE_USER, ()))
    person_days = usage.get(K.ACTIVE_USER, 0)
    sup = 100
    sup -= 25 * support.get("overdue", 0)
    sup -= 15 * support.get("critical", 0)
    sup -= 5 * max(support.get("open", 0) - 2, 0)
    if support.get("csat") is not None and support["csat"] < 3:
        sup -= 20
    return {
        "login": _pct(active_days, WORKING_DAYS),
        "adoption": _pct(person_days, max(seats, 1) * WORKING_DAYS) if seats > 1 else 0,
        "attendance": _pct(len(days_by_key.get(K.ATTENDANCE_USED, ())), WORKING_DAYS),
        "tasks": _pct(len(days_by_key.get(K.TASK_USED, ())), 6),
        "documents": _pct(len(days_by_key.get(K.DOCUMENT_USED, ())), 4),
        "support": max(0, min(100, sup)),
    }


def total(components):
    return round(sum(components[k] * w for k, w in WEIGHTS.items()) / 100)


def health(days=WINDOW_DAYS):
    """Every organization's health, worst first, with components and reasons."""
    from .models import PlatformMetric as M, Subscription

    K = M.Key
    today = timezone.localdate()
    with no_tenant():
        orgs = _orgs()
        totals, active_days, last_active = _usage(days)
        support = _support_by_org()
    rows = []
    for org in orgs:
        usage, days_by_key, sup = totals.get(org.pk, {}), active_days.get(org.pk, {}), support[org.pk]
        seats = getattr(org, "seat_count", 0) or 0
        comps = score_components(seats=seats, usage=usage, days_by_key=days_by_key, support=sup)
        score = total(comps)
        band, band_label = _band(score)
        age = (today - timezone.localtime(org.created_at).date()).days
        is_new = age < NEW_CUSTOMER_DAYS
        sub = getattr(org, "subscription", None)
        end = getattr(sub, "current_period_end", None)
        trial_end = getattr(sub, "trial_end", None) if sub and sub.status == Subscription.Status.TRIAL else None
        last = last_active.get(org.pk)

        reasons = []
        if seats <= 1:
            reasons.append(("no_employees", "No employees added yet"))
        if last is None:
            reasons.append(("inactive", f"Nobody has signed in for {days}+ days"))
        elif (today - last).days >= 14:
            reasons.append(("inactive", f"Nobody active for {(today - last).days} days"))
        if not usage.get(K.ATTENDANCE_USED):
            reasons.append(("no_attendance", f"No attendance recorded in {days} days"))
        if not usage.get(K.TASK_USED):
            reasons.append(("no_tasks", f"No tasks created in {days} days"))
        if seats > 1 and comps["adoption"] < 25:
            reasons.append(("low_adoption", f"Only {comps['adoption']}% of possible daily use"))
        if sup["overdue"]:
            reasons.append(("support_overdue", f"{sup['overdue']} support ticket(s) past SLA"))
        if trial_end is not None and 0 <= (trial_end - today).days <= 7:
            reasons.append(("trial_ending", f"Trial ends in {(trial_end - today).days} days"))
        elif end is not None and 0 <= (end - today).days <= 14:
            reasons.append(("expiring", f"Subscription ends in {(end - today).days} days"))
        elif end is not None and (end - today).days < 0:
            reasons.append(("expired", "Subscription has lapsed"))

        rows.append({
            "slug": org.slug, "name": org.name, "status": org.status,
            "status_display": org.get_status_display(),
            "created_at": org.created_at, "age_days": age, "is_new": is_new,
            "seats": seats,
            "health": score, "band": "onboarding" if is_new and band != "healthy" else band,
            "band_label": "Onboarding" if is_new and band != "healthy" else band_label,
            "components": [{"key": k, "label": COMPONENT_LABELS[k], "score": comps[k],
                            "weight": WEIGHTS[k]} for k in WEIGHTS],
            "active_today": usage.get("_today", 0),
            "active_days_30d": len(days_by_key.get(K.ACTIVE_USER, ())),
            "active_person_days_30d": usage.get(K.ACTIVE_USER, 0),
            "last_active": last,
            "usage_30d": {"attendance": usage.get(K.ATTENDANCE_USED, 0),
                          "leave": usage.get(K.LEAVE_USED, 0),
                          "task": usage.get(K.TASK_USED, 0),
                          "document": usage.get(K.DOCUMENT_USED, 0)},
            "support": sup,
            "plan": getattr(getattr(sub, "plan", None), "name", ""),
            "subscription_status": getattr(sub, "status", ""),
            "subscription_ends": end, "trial_end": trial_end,
            "reasons": [{"code": c, "text": t} for c, t in reasons],
        })
    rows.sort(key=lambda r: (r["health"], r["name"]))
    return rows


# ---------------------------------------------------------------------------
# Part 1: the command center
# ---------------------------------------------------------------------------
SEGMENTS = (
    ("at_risk", "Organizations at risk", lambda r: r["band"] == "at_risk"),
    ("inactive", "Inactive organizations", lambda r: _has(r, "inactive")),
    ("low_adoption", "Low adoption", lambda r: _has(r, "low_adoption")),
    ("no_attendance", "No attendance usage", lambda r: _has(r, "no_attendance")),
    ("no_tasks", "No tasks usage", lambda r: _has(r, "no_tasks")),
    ("no_employees", "No employees added", lambda r: _has(r, "no_employees")),
    ("trial_ending", "Trial ending soon", lambda r: _has(r, "trial_ending")),
    ("new", "New customers", lambda r: r["age_days"] <= 30),
)


def _has(row, code):
    return any(x["code"] == code for x in row["reasons"])


def command_center(days=WINDOW_DAYS):
    rows = health(days)
    segments = []
    for key, label, test in SEGMENTS:
        members = [r for r in rows if test(r)]
        segments.append({"key": key, "label": label, "count": len(members),
                         "organizations": [r["slug"] for r in members]})
    scored = [r["health"] for r in rows]
    return {
        "segments": segments,
        "organizations": rows,
        "average_health": round(sum(scored) / len(scored)) if scored else None,
        "bands": {b: sum(1 for r in rows if r["band"] == b)
                  for b in ("healthy", "watch", "at_risk", "onboarding")},
        "weights": [{"key": k, "label": COMPONENT_LABELS[k], "weight": w} for k, w in WEIGHTS.items()],
        "generated_at": timezone.now(),
    }


# ---------------------------------------------------------------------------
# Part 8: adoption -- organizations, not people
# ---------------------------------------------------------------------------
def adoption(weeks=12):
    """Daily / weekly / monthly active ORGANIZATIONS and module adoption.

    Organizations, unlike people, can be counted exactly from the daily
    counters: an organization is active on a day if it has any ACTIVE_USER
    count that day. So these numbers are distinct counts, not upper bounds.
    """
    from .models import PlatformMetric as M

    K = M.Key
    today = timezone.localdate()
    since = today - timedelta(days=weeks * 7 - 1)
    with no_tenant():
        rows = list(M.objects.filter(day__gte=since, organization__isnull=False, count__gt=0,
                                     key__in=[K.ACTIVE_USER, K.ATTENDANCE_USED, K.TASK_USED,
                                              K.DOCUMENT_USED, K.LEAVE_USED])
                    .values_list("organization_id", "key", "day").distinct())
        live = sum(1 for _ in _orgs())
    by_key_day = defaultdict(lambda: defaultdict(set))
    for org, key, day in rows:
        by_key_day[key][day].add(org)

    def orgs_between(key, start, end):
        out = set()
        for day, orgs in by_key_day[key].items():
            if start <= day <= end:
                out |= orgs
        return out

    def window(n):
        return today - timedelta(days=n - 1), today

    active = {name: len(orgs_between(K.ACTIVE_USER, *window(n)))
              for name, n in (("daily", 1), ("weekly", 7), ("monthly", 30))}
    month_active = orgs_between(K.ACTIVE_USER, *window(30)) or set()

    def share(key):
        users = orgs_between(key, *window(30))
        return {"organizations": len(users), "percent": _pct(len(users & month_active) if month_active else 0,
                                                             len(month_active))}

    trend = []
    for w in range(weeks):
        start = since + timedelta(days=7 * w)
        end = start + timedelta(days=6)
        act = orgs_between(K.ACTIVE_USER, start, end)
        point = {"week_start": start.isoformat(), "active_orgs": len(act)}
        for name, key in (("attendance", K.ATTENDANCE_USED), ("tasks", K.TASK_USED),
                          ("documents", K.DOCUMENT_USED)):
            point[f"{name}_pct"] = _pct(len(orgs_between(key, start, end) & act), len(act)) if act else 0
        trend.append(point)
    daily = [{"day": (today - timedelta(days=29 - i)).isoformat(),
              "active_orgs": len(by_key_day[K.ACTIVE_USER].get(today - timedelta(days=29 - i), ()))}
             for i in range(30)]
    return {
        "organizations": live,
        "active_orgs": active,
        "adoption_30d": {"attendance": share(K.ATTENDANCE_USED), "tasks": share(K.TASK_USED),
                         "documents": share(K.DOCUMENT_USED), "leave": share(K.LEAVE_USED)},
        "weekly_trend": trend, "daily_active_orgs": daily,
    }


# ---------------------------------------------------------------------------
# Part 9: onboarding milestones
# ---------------------------------------------------------------------------
MILESTONES = (
    ("first_login", "First login"), ("first_employee", "First employee"),
    ("first_attendance", "First attendance"), ("first_leave", "First leave"),
    ("first_task", "First task"), ("first_payment", "First payment"),
)


def milestones():
    """When each organization first did each thing. From counters and payments.

    The counters began with the customer-success phase, so an organization
    that did something before then shows it from the first counted day.
    """
    from .models import Payment, PlatformMetric as M

    K = M.Key
    keys = {"first_login": K.ACTIVE_USER, "first_employee": K.EMPLOYEE_ADDED,
            "first_attendance": K.ATTENDANCE_USED, "first_leave": K.LEAVE_USED,
            "first_task": K.TASK_USED}
    with no_tenant():
        orgs = _orgs()
        firsts = defaultdict(dict)
        for row in (M.objects.filter(key__in=keys.values(), organization__isnull=False, count__gt=0)
                    .values("organization_id", "key").annotate(first=Min("day"))):
            firsts[row["organization_id"]][row["key"]] = row["first"]
        paid = dict(Payment.objects.filter(status=Payment.Status.VERIFIED)
                    .values("organization_id").annotate(first=Min("verified_at"))
                    .values_list("organization_id", "first"))
    out, done_counts = [], defaultdict(int)
    for org in orgs:
        marks = []
        for key, label in MILESTONES:
            when = paid.get(org.pk) if key == "first_payment" else firsts[org.pk].get(keys[key])
            if when is not None and hasattr(when, "date"):
                when = timezone.localtime(when).date()
            marks.append({"key": key, "label": label, "reached_on": when})
            done_counts[key] += when is not None
        done = sum(1 for m in marks if m["reached_on"])
        out.append({"slug": org.slug, "name": org.name, "created_at": org.created_at,
                    "milestones": marks, "completed": done, "total": len(MILESTONES),
                    "percent": _pct(done, len(MILESTONES))})
    out.sort(key=lambda r: (r["percent"], r["name"]))
    return {"organizations": out,
            "completion": [{"key": k, "label": label, "organizations": done_counts[k],
                            "percent": _pct(done_counts[k], len(orgs))} for k, label in MILESTONES]}


# ---------------------------------------------------------------------------
# Part 10: the executive view
# ---------------------------------------------------------------------------
def executive(actor):
    from . import console, support
    from .models import PlatformMetric as M, Subscription

    rows = health()
    today = timezone.localdate()
    forecast = console.revenue_forecast(actor)
    with no_tenant():
        renewals = [{
            "slug": s.organization.slug, "name": s.organization.name,
            "status": s.status, "ends_on": s.current_period_end or s.trial_end,
            "days_left": ((s.current_period_end or s.trial_end) - today).days,
            "plan": getattr(s.plan, "name", ""),
        } for s in (Subscription.objects.filter(
            status__in=[Subscription.Status.ACTIVE, Subscription.Status.TRIAL, Subscription.Status.GRACE])
            .select_related("organization", "plan"))
            if (s.current_period_end or s.trial_end)
            and 0 <= ((s.current_period_end or s.trial_end) - today).days <= 30]
        renewals.sort(key=lambda r: r["days_left"])
        overview = support.overview()
        deflected = M.objects.filter(key=M.Key.TICKET_DEFLECTED,
                                     day__gte=today - timedelta(days=29)).aggregate(n=Sum("count"))["n"] or 0
    return {
        "top_active": sorted(rows, key=lambda r: -r["active_person_days_30d"])[:5],
        "at_risk": [r for r in rows if r["band"] == "at_risk"][:5],
        "renewals_due": renewals[:10],
        "support_load": {k: overview[k] for k in ("open", "critical", "overdue", "unread",
                                                  "waiting_customer", "avg_resolution_hours_30d",
                                                  "avg_first_response_hours_30d")},
        "satisfaction": overview["csat_30d"],
        "tickets_deflected_30d": deflected,
        "currency": forecast["currency"],
        "mrr_minor": forecast["monthly_minor"],
        "arr_minor": forecast["annual_minor"],
        "average_health": round(sum(r["health"] for r in rows) / len(rows)) if rows else None,
    }


# ---------------------------------------------------------------------------
# Part 5: one organization's timeline
# ---------------------------------------------------------------------------
def timeline(organization, limit=200):
    """Everything that happened to one customer, in one list, newest first."""
    from .models import (Payment, PlatformAuditLog, PlatformMetric as M, SubscriptionEvent,
                         SuccessTask, SupportRequest, TenantDomain)

    K = M.Key
    events = []

    def add(at, kind, title, detail="", link=""):
        if at is None:
            return
        if not hasattr(at, "hour"):              # a date: noon local, for ordering
            at = timezone.make_aware(timezone.datetime(at.year, at.month, at.day, 12))
        events.append({"at": at, "kind": kind, "title": title, "detail": detail, "link": link})

    with no_tenant():
        add(organization.created_at, "created", "Organization created",
            f"{organization.name} · {organization.slug}")
        for e in SubscriptionEvent.objects.filter(organization=organization):
            add(e.effective_at, f"subscription_{e.event}", f"Subscription: {e.get_event_display().lower()}",
                e.note or "")
        firsts = dict(M.objects.filter(organization=organization, count__gt=0,
                                       key__in=[K.ACTIVE_USER, K.EMPLOYEE_ADDED, K.ATTENDANCE_USED,
                                                K.LEAVE_USED, K.TASK_USED, K.DOCUMENT_USED])
                      .values("key").annotate(first=Min("day")).values_list("key", "first"))
        labels = {K.ACTIVE_USER: ("first_login", "First sign-in by their team"),
                  K.EMPLOYEE_ADDED: ("employees", "Employees added"),
                  K.ATTENDANCE_USED: ("attendance", "Attendance enabled (first record)"),
                  K.LEAVE_USED: ("leave", "First leave application"),
                  K.TASK_USED: ("task", "First task created"),
                  K.DOCUMENT_USED: ("document", "First document created")}
        for key, day in firsts.items():
            kind, title = labels[key]
            add(day, kind, title)
        added = M.objects.filter(organization=organization, key=K.EMPLOYEE_ADDED).aggregate(
            n=Sum("count"))["n"] or 0
        if added:
            for e in events:
                if e["kind"] == "employees":
                    e["detail"] = f"{added} added so far"
        for p in Payment.objects.filter(organization=organization).select_related("plan"):
            plan = getattr(p.plan, "name", "") or ""
            label = f"{p.payment_reference} {plan}".strip()
            if p.submitted_at:
                add(p.submitted_at, "payment_submitted", "Payment submitted", label)
            if p.status == Payment.Status.VERIFIED and p.verified_at:
                add(p.verified_at, "payment_verified", "Renewal approved", label)
            elif p.status == Payment.Status.REJECTED:
                add(p.updated_at, "payment_rejected", "Payment rejected", label)
        for t in SupportRequest.objects.filter(organization=organization).exclude(
                kind=SupportRequest.Kind.FEEDBACK):
            if t.kind == SupportRequest.Kind.FEATURE:
                add(t.created_at, "feature_request", "Feature request created",
                    f"{t.reference} · {t.subject}", "/platform/support")
            else:
                add(t.created_at, "ticket_opened", "Support ticket opened",
                    f"{t.reference} · {t.get_category_display()} · {t.subject}", "/platform/support")
                if t.escalated_at:
                    add(t.escalated_at, "ticket_escalated", "Support ticket escalated",
                        f"{t.reference} · level {t.escalation_level}", "/platform/support")
                if t.resolved_at:
                    add(t.resolved_at, "ticket_resolved", "Support ticket resolved", t.reference)
        # Usage, one line per month: how much the team actually used it.
        monthly = defaultdict(lambda: defaultdict(int))
        for m in (M.objects.filter(organization=organization,
                                   day__gte=timezone.localdate() - timedelta(days=185),
                                   key__in=[K.ACTIVE_USER, K.ATTENDANCE_USED, K.LEAVE_USED,
                                            K.TASK_USED, K.DOCUMENT_USED])
                  .values("day", "key", "count")):
            monthly[m["day"].replace(day=1)][m["key"]] += m["count"]
        this_month = timezone.localdate().replace(day=1)
        for month, n in monthly.items():
            if not any(n.values()):
                continue
            last = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            parts = [f"{n[K.ACTIVE_USER]} active person-days"]
            parts += [f"{n[k]} {label}" for k, label in ((K.ATTENDANCE_USED, "attendance records"),
                                                       (K.LEAVE_USED, "leave applications"),
                                                       (K.TASK_USED, "tasks"),
                                                       (K.DOCUMENT_USED, "documents")) if n[k]]
            add(timezone.localdate() if month == this_month else last, "usage",
                f"Usage in {month:%B %Y}" + (" so far" if month == this_month else ""),
                " · ".join(parts))
        for d in TenantDomain.objects.filter(organization=organization):
            add(d.created_at, "domain_claimed", "Custom domain claimed", d.hostname)
            if d.verified_at:
                add(d.verified_at, "domain_verified", "Custom domain verified", d.hostname)
        for task in SuccessTask.objects.filter(organization=organization):
            add(task.created_at, "success_task", f"{task.get_kind_display()} planned", task.title)
            if task.completed_at:
                add(task.completed_at, "success_task_done", f"{task.get_kind_display()} done", task.title)
        skip = {"subscription_changed", "plan_changed", "trial_started", "tenant_created"}
        for log in PlatformAuditLog.objects.filter(organization=organization).order_by("-created_at")[:100]:
            if log.action in skip:
                continue
            add(log.created_at, f"platform_{log.action}", log.get_action_display(),
                log.note or "")
    events.sort(key=lambda e: e["at"], reverse=True)
    return events[:limit]


# ---------------------------------------------------------------------------
# Parts 3 and 11: alerts
# ---------------------------------------------------------------------------
NO_RESPONSE_SHARE = 0.5            # unanswered for half its SLA = alert
STALE_DAYS = 3                     # in progress, nothing happened for this long
UNRESOLVED_DAYS = 7


def ticket_alerts():
    """Tickets that need the team now. Derived, every time."""
    from . import support
    from .models import SupportRequest as SR

    now = timezone.now()
    out = {"critical": [], "overdue": [], "no_response": [], "stale": [], "escalated": []}
    with no_tenant():
        open_tickets = (SR.objects.filter(status__in=SR.OPEN_STATES)
                        .exclude(kind__in=[SR.Kind.FEEDBACK, SR.Kind.FEATURE])
                        .select_related("organization", "assigned_to"))
        for t in open_tickets:
            entry = {"id": str(t.id), "reference": t.reference, "subject": t.subject,
                     "organization": getattr(t.organization, "name", ""),
                     "priority": t.priority, "status": t.status,
                     "assigned_to": (t.assigned_to.get_full_name() or t.assigned_to.username)
                     if t.assigned_to_id else None,
                     "created_at": t.created_at, "sla_due_at": t.sla_due_at}
            if t.priority == SR.Priority.CRITICAL:
                out["critical"].append(entry)
            if t.escalated:
                out["escalated"].append({**entry, "escalation_level": t.escalation_level,
                                         "escalated_at": t.escalated_at})
            if t.is_overdue:
                out["overdue"].append(entry)
            sla = support.sla_hours(t.priority)
            if (t.first_response_at is None and t.status != SR.Status.WAITING_CUSTOMER
                    and now - t.created_at > timedelta(hours=sla * NO_RESPONSE_SHARE)):
                out["no_response"].append(entry)
            if (t.status == SR.Status.IN_PROGRESS
                    and now - t.updated_at > timedelta(days=STALE_DAYS)):
                out["stale"].append(entry)
    return out


def customer_alerts():
    """Customers the team should reach out to (Part 11)."""
    from .models import SupportRequest as SR

    rows = health()
    now = timezone.now()
    with no_tenant():
        unresolved = defaultdict(int)
        for t in (SR.objects.filter(status__in=SR.OPEN_STATES,
                                    created_at__lt=now - timedelta(days=UNRESOLVED_DAYS))
                  .exclude(kind__in=[SR.Kind.FEEDBACK, SR.Kind.FEATURE])
                  .values_list("organization_id", flat=True)):
            unresolved[t] += 1
        from .models import Organization
        ids = dict(Organization.objects.values_list("slug", "pk"))
    out = []
    for r in rows:
        codes = {x["code"]: x["text"] for x in r["reasons"]}
        found = []
        if "trial_ending" in codes:
            found.append(("trial_expiring", codes["trial_ending"]))
        if "no_employees" in codes and r["age_days"] >= 3:
            found.append(("no_employees", "No employees added after "
                                          f"{r['age_days']} days"))
        if "no_attendance" in codes and r["age_days"] >= 7:
            found.append(("no_attendance", codes["no_attendance"]))
        if "inactive" in codes and not r["is_new"]:
            found.append(("inactive", codes["inactive"]))
        if unresolved.get(ids.get(r["slug"])):
            found.append(("unresolved_ticket",
                          f"{unresolved[ids[r['slug']]]} ticket(s) open over {UNRESOLVED_DAYS} days"))
        for code, text in found:
            out.append({"slug": r["slug"], "name": r["name"], "code": code, "text": text,
                        "health": r["health"]})
    return out


# The task each customer alert opens, so a signal becomes somebody's job.
ALERT_TASK = {
    "trial_expiring": ("call", "Call before the trial ends"),
    "no_employees": ("onboarding_review", "Help them add their employees"),
    "no_attendance": ("training", "Attendance training — nothing recorded yet"),
    "inactive": ("follow_up", "Follow up — the team has gone quiet"),
    "unresolved_ticket": ("follow_up", "Follow up on a long-open ticket"),
}


def _recipients():
    from django.contrib.auth import get_user_model

    target = getattr(settings, "PLATFORM_SUPPORT_EMAIL", "")
    if target:
        return [target]
    with no_tenant():
        return list(get_user_model().all_tenants.filter(
            is_platform_staff=True, organization__isnull=True, is_active=True)
            .exclude(email="").values_list("email", flat=True))


def _send(subject, lines):
    from django.core.mail import send_mail

    to = _recipients()
    if not to or not lines:
        return False
    try:
        send_mail(subject, "\n".join(lines), settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
        return True
    except Exception:                              # noqa: BLE001
        logger.warning("customer success email failed: %s", subject, exc_info=True)
        return False


# How often the same ticket alert may repeat: once for critical/no-response
# (they are facts that do not get worse), daily while overdue or stale.
REPEAT = {"critical": None, "no_response": None, "overdue": timedelta(hours=24),
          "stale": timedelta(hours=24)}
ALERT_LABEL = {"critical": "Critical ticket", "overdue": "Overdue (past SLA)",
               "no_response": "No response yet", "stale": "Stale (no activity)"}


def run_ticket_alerts(now=None):
    """Email the team about ticket alerts they have not been told about yet."""
    from .models import SupportRequest

    now = now or timezone.now()
    alerts = ticket_alerts()
    fresh = []
    with no_tenant():
        for kind, entries in alerts.items():
            if kind not in REPEAT:
                continue          # escalations email leadership themselves (desk.escalate)
            for entry in entries:
                ticket = SupportRequest.objects.get(pk=entry["id"])
                sent = (ticket.sla_alerts or {}).get(kind)
                repeat = REPEAT[kind]
                if sent and (repeat is None or now - timezone.datetime.fromisoformat(sent) < repeat):
                    continue
                ticket.sla_alerts = {**(ticket.sla_alerts or {}), kind: now.isoformat()}
                SupportRequest.objects.filter(pk=ticket.pk).update(sla_alerts=ticket.sla_alerts)
                fresh.append((kind, entry))
    lines = [f"[{ALERT_LABEL[k]}] {e['reference']} · {e['organization']} · {e['subject']}"
             f" · {e['priority']} · {e['assigned_to'] or 'unassigned'}" for k, e in fresh]
    if lines:
        _send(f"Support alerts: {len(lines)} ticket(s) need attention", lines + [
            "", "Open the support desk: Platform console → Support."])
    return {"alerted": len(fresh), "by_kind": {k: sum(1 for x, _ in fresh if x == k) for k in REPEAT}}


def run_customer_alerts():
    """Open a success task for each new customer signal, and email a digest."""
    from .models import Organization, SuccessTask

    alerts = customer_alerts()
    opened = []
    with no_tenant():
        orgs = {o.slug: o for o in Organization.objects.all()}
        for a in alerts:
            org = orgs.get(a["slug"])
            if org is None:
                continue
            if SuccessTask.objects.filter(organization=org, auto_reason=a["code"],
                                          status=SuccessTask.Status.OPEN).exists():
                continue
            kind, title = ALERT_TASK[a["code"]]
            SuccessTask.objects.create(organization=org, kind=kind, title=title,
                                       notes=a["text"], auto_reason=a["code"],
                                       due_date=timezone.localdate() + timedelta(days=2))
            opened.append(a)
    if opened:
        _send(f"Customer success: {len(opened)} customer(s) to reach out to", [
            f"{a['name']} (health {a['health']}): {a['text']}" for a in opened
        ] + ["", "Each has a task under Platform console → Customer success → Tasks."])
    return {"signals": len(alerts), "tasks_opened": len(opened)}


# ---------------------------------------------------------------------------
# Part 4: tasks
# ---------------------------------------------------------------------------
def task_row(t):
    return {"id": str(t.id), "organization": t.organization.slug,
            "organization_name": t.organization.name, "kind": t.kind,
            "kind_display": t.get_kind_display(), "title": t.title, "notes": t.notes,
            "due_date": t.due_date, "status": t.status, "status_display": t.get_status_display(),
            "assigned_to": str(t.assigned_to_id) if t.assigned_to_id else None,
            "assigned_to_name": (t.assigned_to.get_full_name() or t.assigned_to.username)
            if t.assigned_to_id else None,
            "auto_reason": t.auto_reason,
            "ticket": str(t.ticket_id) if t.ticket_id else None,
            "ticket_reference": t.ticket.reference if t.ticket_id else None,
            "campaign": str(t.campaign_id) if t.campaign_id else None,
            "campaign_name": t.campaign.name if t.campaign_id else None,
            "overdue": bool(t.due_date and t.status == t.Status.OPEN
                            and t.due_date < timezone.localdate()),
            "completed_at": t.completed_at, "created_at": t.created_at}
