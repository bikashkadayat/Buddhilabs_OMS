"""Support Desk 3.0: the support desk as an operations center.

Built on the Support Center (``tenancy.support``) and Customer Success 2.0
(``tenancy.success``). This module adds the internal side the customer never
sees:

OWNERSHIP. Every ticket belongs to a ``SupportTeam`` from the moment it is
opened -- routed by category -- and, when the team has an available member,
to the least-loaded of them. Reassign moves it between people, Transfer
between teams (re-routing it inside the new one), Escalate raises its level
and tells the team's leads. "Unassigned" can still happen (an empty team, an
agent who let go) but "nobody's" cannot: the team queue owns it.

INTERNAL WORK. @mentions in internal notes, links between tickets about the
same thing, follow-up tasks on a ticket (the same ``SuccessTask`` list the
success team works from), and known issues: a problem written up once, linked
to every ticket it caused, offered to customers by the assistant, and fixed
for all of them at once.

RULES. ``run_escalations`` (every 15 minutes, with the SLA alerts) escalates a
ticket whose SLA clock has run past ``SUPPORT_ESCALATION_HOURS`` for its
priority -- critical after 4 hours by default -- and emails support
leadership. The SLA board and the console bell show the result.

CAMPAIGNS. A segment (inactive, low health, trial, near renewal) becomes one
outreach task per customer, tracked to done as a campaign.

PRIVACY. Platform tables only, read with no tenant bound, as in
``tenancy.success``. None of it is exported to the customer or shown to them:
the customer-side serialiser in ``tenancy.support`` never reads these fields.
"""
import logging
import re
from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .context import no_tenant
from .exceptions import TenancyError
from .models import (KnownIssue, SuccessCampaign, SuccessTask, SupportMention, SupportMessage,
                     SupportRequest, SupportTeam, SupportTeamMember, SupportTicketLink)

logger = logging.getLogger(__name__)
SR = SupportRequest
S = SupportRequest.Status
P = SupportRequest.Priority
K = SupportRequest.Kind


def _require(actor):
    from .console import require_platform
    require_platform(actor)


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _staff():
    return get_user_model().all_tenants.filter(is_platform_staff=True, organization__isnull=True)


def _staff_member(user_id, message="Only platform staff can be chosen."):
    agent = _staff().filter(pk=user_id, is_active=True).first() if user_id else None
    if user_id and agent is None:
        raise TenancyError(message)
    return agent


def _note(record, text, actor=None):
    """An internal line in the thread: the desk's history explains itself."""
    from . import support
    support._system(record, text, internal=True, actor=actor)


def _tickets():
    return SR.objects.exclude(kind=K.FEEDBACK)


def _open_tickets():
    return _tickets().filter(status__in=SR.OPEN_STATES).exclude(kind=K.FEATURE)


# ===========================================================================
# Part 1: teams and agents
# ===========================================================================
def route(category):
    """The team that owns a new ticket of this category."""
    teams = list(SupportTeam.objects.filter(is_active=True))
    for team in teams:
        if category and category in (team.categories or []):
            return team
    return next((t for t in teams if t.is_default), None)


def _load():
    """Open tickets per agent -- what "least loaded" means."""
    return Counter(_open_tickets().filter(assigned_to__isnull=False)
                   .values_list("assigned_to_id", flat=True))


def pick_agent(team, *, exclude=None):
    """The available member of ``team`` with the fewest open tickets."""
    if team is None:
        return None
    members = list(team.members.filter(is_available=True, user__is_active=True)
                   .select_related("user").order_by("created_at"))
    members = [m for m in members if m.user_id != exclude]
    if not members:
        return None
    load = _load()
    return min(members, key=lambda m: load.get(m.user_id, 0)).user


def assign_new(record):
    """Give a just-opened ticket its owner: the routed team, then an agent.

    Called from ``support.submit`` inside its no_tenant block. Never raises:
    a ticket that cannot be routed is still a ticket.
    """
    if record.kind in (K.FEEDBACK,):
        return record
    try:
        team = route(record.category)
        agent = pick_agent(team) if team and team.auto_assign and record.kind != K.FEATURE else None
        record.team, record.assigned_to = team, agent
        if agent and record.status == S.OPEN:
            record.status = S.IN_PROGRESS
        record.save(update_fields=["team", "assigned_to", "status", "updated_at"])
        if team:
            _note(record, f"Routed to {team.name}" + (f", assigned to {_name(agent)}." if agent else "."))
    except Exception:                                    # noqa: BLE001
        logger.warning("support routing failed for %s", record.pk, exc_info=True)
    return record


def team_row(team, load=None):
    load = load if load is not None else _load()
    members = list(team.members.select_related("user").order_by("role", "created_at"))
    open_qs = _open_tickets().filter(team=team)
    return {
        "id": str(team.id), "key": team.key, "name": team.name,
        "description": team.description, "categories": team.categories or [],
        "is_default": team.is_default, "auto_assign": team.auto_assign,
        "is_active": team.is_active,
        "members": [{"id": str(m.id), "user": str(m.user_id), "name": _name(m.user),
                     "email": m.user.email, "role": m.role, "role_display": m.get_role_display(),
                     "is_available": m.is_available, "open_tickets": load.get(m.user_id, 0)}
                    for m in members],
        "open": open_qs.count(),
        "unassigned": open_qs.filter(assigned_to__isnull=True).count(),
        "overdue": open_qs.filter(sla_due_at__lt=timezone.now(), sla_paused_at__isnull=True).count(),
    }


def teams(actor):
    _require(actor)
    with no_tenant():
        load = _load()
        return [team_row(t, load) for t in SupportTeam.objects.all()]


def save_team(actor, data, team_id=None):
    """Create or edit a team. ``categories`` it claims are taken from others."""
    _require(actor)
    valid = set(SR.Category.values)
    with no_tenant(), transaction.atomic():
        team = SupportTeam.objects.filter(pk=team_id).first() if team_id else SupportTeam()
        if team is None:
            raise TenancyError("That team does not exist.")
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                raise TenancyError("Give the team a name.")
            team.name = name[:80]
        if not team.key:
            from django.utils.text import slugify
            base = slugify(team.name)[:36] or "team"
            key, n = base, 1
            while SupportTeam.objects.filter(key=key).exists():
                n += 1
                key = f"{base}-{n}"
            team.key = key
        for field in ("description",):
            if field in data:
                setattr(team, field, (data.get(field) or "").strip()[:300])
        for flag in ("auto_assign", "is_active", "is_default"):
            if flag in data:
                setattr(team, flag, bool(data[flag]))
        if "categories" in data:
            cats = [c for c in (data.get("categories") or []) if c in valid]
            team.categories = cats
        team.save()
        if "categories" in data:
            # One owner per category: claiming it here releases it elsewhere.
            for other in SupportTeam.objects.exclude(pk=team.pk):
                kept = [c for c in (other.categories or []) if c not in team.categories]
                if kept != (other.categories or []):
                    other.categories = kept
                    other.save(update_fields=["categories"])
        if team.is_default:
            SupportTeam.objects.exclude(pk=team.pk).update(is_default=False)
        return team_row(team)


def set_member(actor, team_id, user_id, *, role=None, is_available=None, remove=False):
    _require(actor)
    with no_tenant():
        team = SupportTeam.objects.filter(pk=team_id).first()
        if team is None:
            raise TenancyError("That team does not exist.")
        user = _staff_member(user_id, "Only platform staff can join a support team.")
        if remove:
            SupportTeamMember.objects.filter(team=team, user=user).delete()
            return team_row(team)
        member, _ = SupportTeamMember.objects.get_or_create(team=team, user=user)
        if role is not None:
            if role not in SupportTeamMember.Role.values:
                raise TenancyError("Unknown role.")
            member.role = role
        if is_available is not None:
            member.is_available = bool(is_available)
        member.save()
        return team_row(team)


def agents(actor):
    """Every platform agent with their teams and open load."""
    _require(actor)
    with no_tenant():
        load = _load()
        memberships = {}
        for m in SupportTeamMember.objects.select_related("team"):
            memberships.setdefault(m.user_id, []).append(
                {"team": str(m.team_id), "name": m.team.name, "role": m.role,
                 "is_available": m.is_available})
        return [{"id": str(u.pk), "name": _name(u), "email": u.email,
                 "username": u.username, "open_tickets": load.get(u.pk, 0),
                 "teams": memberships.get(u.pk, [])}
                for u in _staff().filter(is_active=True).order_by("first_name", "username")]


def _leads(team):
    if team is None:
        return []
    return [m.user for m in team.members.filter(role=SupportTeamMember.Role.LEAD,
                                                user__is_active=True).select_related("user")]


def leadership_emails(team=None):
    """Who hears about escalations: SUPPORT_LEADERSHIP_EMAILS, then the
    team's leads; the platform support inbox if neither is set."""
    from . import success

    configured = getattr(settings, "SUPPORT_LEADERSHIP_EMAILS", None) or []
    if isinstance(configured, str):
        configured = [e.strip() for e in configured.split(",") if e.strip()]
    emails = list(configured) + [u.email for u in _leads(team) if u.email]
    if not emails:
        emails = success._recipients()
    return list(dict.fromkeys(emails))


def _mail(subject, body, to):
    from django.core.mail import send_mail

    if not to:
        return False
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
        return True
    except Exception:                                    # noqa: BLE001
        logger.warning("support desk email failed: %s", subject, exc_info=True)
        return False


# ===========================================================================
# Part 2: assign, reassign, transfer, escalate
# ===========================================================================
def _ticket(ticket_id):
    from . import support
    return support._platform_ticket(ticket_id)


def assign(actor, record, agent_id):
    """Assign or reassign (the thread says which). Inside a no_tenant block."""
    agent = _staff_member(agent_id, "Tickets can only be assigned to platform staff.")
    if (agent.pk if agent else None) == record.assigned_to_id:
        return False
    before = record.assigned_to
    record.assigned_to = agent
    if agent and before:
        _note(record, f"Reassigned from {_name(before)} to {_name(agent)}.", actor)
    elif agent:
        _note(record, f"Assigned to {_name(agent)}.", actor)
    else:
        owner = f" — stays in the {record.team.name} queue" if record.team_id else ""
        _note(record, f"Unassigned{owner}.", actor)
    if record.status == S.OPEN and agent:
        record.status = S.IN_PROGRESS
    return True


def transfer(actor, ticket_id, team_id, *, agent_id=None, note=""):
    """Hand the ticket to another team; it is routed again inside that team."""
    from . import support

    _require(actor)
    with no_tenant():
        record = _ticket(ticket_id)
        team = SupportTeam.objects.filter(pk=team_id, is_active=True).first()
        if team is None:
            raise TenancyError("Choose a team to transfer to.")
        if team.pk == record.team_id and not agent_id:
            raise TenancyError(f"It is already with {team.name}.")
        before = record.team
        agent = _staff_member(agent_id) if agent_id else (
            pick_agent(team) if team.auto_assign else None)
        record.team, record.assigned_to = team, agent
        if agent and record.status == S.OPEN:
            record.status = S.IN_PROGRESS
        record.save()
        why = f" — {note.strip()}" if (note or "").strip() else ""
        _note(record, f"Transferred from {before.name if before else 'no team'} to {team.name}"
                      f"{', assigned to ' + _name(agent) if agent else ''}{why}.", actor)
        if agent and agent.email and agent != actor:
            _mail(f"[{record.reference}] Transferred to you: {record.subject or record.get_category_display()}",
                  f"{_name(actor)} transferred {record.reference} to {team.name} and assigned it to you."
                  f"{why}\n\nOpen it: Platform console → Support.\n", [agent.email])
        return support.row(_ticket(record.pk), for_platform=True)


def escalate(record, *, actor=None, reason="", auto=False):
    """Raise the escalation level, bump priority one step, tell leadership.

    Inside a no_tenant block; the caller saves. Returns the emails told."""
    from . import support

    now = timezone.now()
    new = support.ESCALATION[record.priority]
    if record.sla_due_at and new != record.priority:
        record.sla_due_at += timedelta(hours=support.sla_hours(new) - support.sla_hours(record.priority))
    record.priority = new
    record.escalated = True
    record.escalation_level = (record.escalation_level or 0) + 1
    record.escalated_at = now
    if record.assigned_to_id is None:
        lead = next(iter(_leads(record.team)), None)
        if lead:
            record.assigned_to = lead
            if record.status == S.OPEN:
                record.status = S.IN_PROGRESS
    who = "automatically" if auto else f"by {_name(actor)}"
    because = f": {reason}" if reason else ""
    _note(record, f"Escalated {who} to level {record.escalation_level} "
                  f"({record.get_priority_display()}){because}.", actor)
    to = leadership_emails(record.team)
    _mail(f"[Escalation L{record.escalation_level}] {record.reference} · "
          f"{getattr(record.organization, 'name', 'platform')} · {record.subject or record.get_category_display()}",
          f"{record.reference} was escalated {who}{because}.\n\n"
          f"Organization: {getattr(record.organization, 'name', '—')}\n"
          f"Priority: {record.get_priority_display()}\n"
          f"Team: {record.team.name if record.team_id else '—'}\n"
          f"Assigned: {_name(record.assigned_to) or 'nobody'}\n"
          f"Opened: {timezone.localtime(record.created_at):%Y-%m-%d %H:%M}\n\n"
          f"{record.message[:600]}\n\nOpen it: Platform console → Support.\n", to)
    return to


# ===========================================================================
# Part 8: escalation rules
# ===========================================================================
DEFAULT_ESCALATION_HOURS = {"critical": 4}


def escalation_rules():
    rules = getattr(settings, "SUPPORT_ESCALATION_HOURS", None) or DEFAULT_ESCALATION_HOURS
    return {k: float(v) for k, v in rules.items() if k in P.values and v}


def sla_elapsed_hours(record, now=None):
    """Hours the SLA clock has run: paused time (waiting on the customer) and
    priority changes are already folded into ``sla_due_at``."""
    from . import support

    if not record.sla_due_at:
        return 0.0
    now = now or timezone.now()
    start = record.sla_due_at - timedelta(hours=support.sla_hours(record.priority))
    end = record.sla_paused_at or now
    return max(0.0, (end - start).total_seconds() / 3600)


def run_escalations(now=None):
    """Escalate every ticket past its rule, once per rule. Cron, 15 minutes."""
    now = now or timezone.now()
    rules = escalation_rules()
    escalated = []
    with no_tenant():
        candidates = (_open_tickets().filter(priority__in=list(rules), sla_paused_at__isnull=True,
                                             sla_due_at__isnull=False)
                      .select_related("organization", "assigned_to", "team"))
        for record in candidates:
            hours = rules[record.priority]
            key = f"auto_escalated_{record.priority}"
            if (record.sla_alerts or {}).get(key) or sla_elapsed_hours(record, now) < hours:
                continue
            with transaction.atomic():
                locked = SR.objects.select_for_update().get(pk=record.pk)
                if (locked.sla_alerts or {}).get(key):
                    continue
                record.sla_alerts = {**(locked.sla_alerts or {}), key: now.isoformat()}
                escalate(record, reason=f"{record.get_priority_display().lower()} ticket open "
                                        f"{hours:g}+ hours", auto=True)
                record.save()
            escalated.append(record.reference)
    return {"escalated": len(escalated), "tickets": escalated}


# ===========================================================================
# Part 7: mentions, links
# ===========================================================================
MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9._-]{1,60})")


def resolve_mentions(body):
    """@username, @email-local-part or @firstname (when unique) -> users."""
    handles = {h.rstrip(".").lower() for h in MENTION.findall(body or "")}
    if not handles:
        return []
    staff = list(_staff().filter(is_active=True))
    found = {}
    for h in handles:
        exact = [u for u in staff if u.username.lower() == h
                 or (u.email and u.email.split("@")[0].lower() == h)]
        if not exact:
            exact = [u for u in staff if (u.first_name or "").lower() == h]
        if len(exact) == 1:
            found[exact[0].pk] = exact[0]
    return list(found.values())


def record_mentions(actor, record, message):
    """Called for an internal note: who it names hears about it."""
    users = [u for u in resolve_mentions(message.body) if u.pk != getattr(actor, "pk", None)]
    for u in users:
        try:
            with transaction.atomic():
                SupportMention.objects.create(message=message, ticket=record, user=u)
        except IntegrityError:
            continue
        if u.email:
            _mail(f"[{record.reference}] {_name(actor)} mentioned you",
                  f"{_name(actor)} mentioned you in an internal note on {record.reference} "
                  f"({record.subject or record.get_category_display()}):\n\n{message.body[:1000]}\n\n"
                  f"Open it: Platform console → Support.\n", [u.email])
    return users


def mentions_for(actor, *, unread_only=False):
    _require(actor)
    with no_tenant():
        qs = (SupportMention.objects.filter(user=actor)
              .select_related("ticket", "message", "ticket__organization"))
        if unread_only:
            qs = qs.filter(read_at__isnull=True)
        return {"unread": SupportMention.objects.filter(user=actor, read_at__isnull=True).count(),
                "mentions": [{"id": str(m.id), "ticket": str(m.ticket_id),
                              "reference": m.ticket.reference, "subject": m.ticket.subject,
                              "organization": getattr(m.ticket.organization, "name", ""),
                              "by": m.message.author_name, "body": m.message.body[:300],
                              "read": m.read_at is not None, "created_at": m.created_at}
                             for m in qs[:50]]}


def mark_mentions_read(actor, ticket_id=None):
    _require(actor)
    with no_tenant():
        qs = SupportMention.objects.filter(user=actor, read_at__isnull=True)
        if ticket_id:
            qs = qs.filter(ticket_id=ticket_id)
        return qs.update(read_at=timezone.now())


def _brief(t):
    return {"id": str(t.id), "reference": t.reference, "subject": t.subject,
            "status": t.status, "status_display": t.get_status_display(),
            "organization": getattr(t.organization, "name", "")}


def linked(record):
    ids = set(SupportTicketLink.objects.filter(from_ticket=record).values_list("to_ticket_id", flat=True))
    ids |= set(SupportTicketLink.objects.filter(to_ticket=record).values_list("from_ticket_id", flat=True))
    return [_brief(t) for t in SR.objects.filter(pk__in=ids).select_related("organization")]


def _find(reference_or_id):
    value = str(reference_or_id or "").strip()
    if value.upper().startswith("SUP-") and value[4:].isdigit():
        return SR.objects.filter(number=int(value[4:])).first()
    try:
        return SR.objects.filter(pk=value).first()
    except (ValueError, Exception):                      # noqa: BLE001 - bad uuid
        return None


def link(actor, ticket_id, other, *, remove=False):
    _require(actor)
    with no_tenant():
        record = _ticket(ticket_id)
        target = _find(other)
        if target is None or target.kind == K.FEEDBACK:
            raise TenancyError("No ticket with that reference.")
        if target.pk == record.pk:
            raise TenancyError("A ticket cannot be linked to itself.")
        pair = Q(from_ticket=record, to_ticket=target) | Q(from_ticket=target, to_ticket=record)
        if remove:
            if SupportTicketLink.objects.filter(pair).delete()[0]:
                _note(record, f"Unlinked from {target.reference}.", actor)
        elif not SupportTicketLink.objects.filter(pair).exists():
            SupportTicketLink.objects.create(from_ticket=record, to_ticket=target, created_by=actor)
            _note(record, f"Linked to {target.reference}.", actor)
            _note(target, f"Linked to {record.reference}.", actor)
        return linked(record)


# ===========================================================================
# Part 3: tasks on a ticket
# ===========================================================================
TICKET_TASK_KINDS = (SuccessTask.Kind.FOLLOW_UP, SuccessTask.Kind.TRAINING,
                     SuccessTask.Kind.DOMAIN_SETUP, SuccessTask.Kind.PAYMENT_VERIFICATION,
                     SuccessTask.Kind.CALL)


def create_ticket_task(actor, ticket_id, data):
    from . import success

    _require(actor)
    kind = data.get("kind") or SuccessTask.Kind.FOLLOW_UP
    if kind not in SuccessTask.Kind.values:
        raise TenancyError("Unknown task type.")
    with no_tenant():
        record = _ticket(ticket_id)
        if record.organization_id is None:
            raise TenancyError("This ticket has no organization to follow up with.")
        title = ((data.get("title") or "").strip()
                 or f"{dict(SuccessTask.Kind.choices)[kind]} — {record.reference}")
        due = data.get("due_date") or (timezone.localdate() + timedelta(days=2))
        task = SuccessTask.objects.create(
            organization_id=record.organization_id, ticket=record, kind=kind, title=title[:200],
            notes=(data.get("notes") or "").strip(), due_date=due,
            assigned_to=_staff_member(data.get("assigned_to")) or actor, created_by=actor)
        _note(record, f"Task: {task.get_kind_display()} — {task.title} "
                      f"(due {task.due_date}, {_name(task.assigned_to)}).", actor)
        task = SuccessTask.objects.select_related("organization", "assigned_to").get(pk=task.pk)
        return success.task_row(task)


# ===========================================================================
# Part 4: known issues
# ===========================================================================
def issue_row(i, *, counts=None):
    counts = counts if counts is not None else {}
    c = counts.get(i.pk, {})
    return {"id": str(i.id), "title": i.title, "symptoms": i.symptoms,
            "workaround": i.workaround, "category": i.category, "state": i.state,
            "state_display": i.get_state_display(), "is_public": i.is_public,
            "tickets": c.get("tickets", 0), "open_tickets": c.get("open", 0),
            "organizations": c.get("orgs", 0), "deflected": i.deflected_count,
            "fixed_at": i.fixed_at, "created_at": i.created_at, "updated_at": i.updated_at}


def _issue_counts():
    out = {}
    for r in (SR.objects.filter(known_issue__isnull=False).values("known_issue_id")
              .annotate(tickets=Count("id"),
                        open=Count("id", filter=Q(status__in=SR.OPEN_STATES)),
                        orgs=Count("organization", distinct=True))):
        out[r["known_issue_id"]] = r
    return out


def known_issues(actor):
    _require(actor)
    with no_tenant():
        counts = _issue_counts()
        return [issue_row(i, counts=counts) for i in KnownIssue.objects.all()[:200]]


def save_issue(actor, data, issue_id=None, *, from_ticket=None):
    """Create/edit a known issue. Marking it fixed can post ``fix_note`` to
    every linked open ticket and resolve them (``resolve_linked``)."""
    from . import support

    _require(actor)
    with no_tenant():
        issue = KnownIssue.objects.filter(pk=issue_id).first() if issue_id else KnownIssue(created_by=actor)
        if issue is None:
            raise TenancyError("That known issue does not exist.")
        source = _ticket(from_ticket) if from_ticket else None
        if not issue_id and source is not None:
            issue.title = issue.title or source.subject or source.message[:120]
            issue.category = source.category
        for field, size in (("title", 200), ("symptoms", 4000), ("workaround", 4000)):
            if field in data:
                setattr(issue, field, (data.get(field) or "").strip()[:size])
        if "category" in data:
            issue.category = data["category"] if data["category"] in SR.Category.values else ""
        if "is_public" in data:
            issue.is_public = bool(data["is_public"])
        fixed_now = False
        if data.get("state"):
            if data["state"] not in KnownIssue.State.values:
                raise TenancyError("Unknown state.")
            fixed_now = data["state"] == KnownIssue.State.FIXED and issue.state != KnownIssue.State.FIXED
            issue.state = data["state"]
            issue.fixed_at = timezone.now() if issue.state == KnownIssue.State.FIXED else None
        if not issue.title:
            raise TenancyError("Give the issue a title.")
        issue.save()
        if source is not None and source.known_issue_id != issue.pk:
            source.known_issue = issue
            source.save(update_fields=["known_issue", "updated_at"])
            _note(source, f"Linked to known issue: {issue.title}.", actor)
        linked_open = list(SR.objects.filter(known_issue=issue, status__in=SR.OPEN_STATES))
    resolved = 0
    if fixed_now and data.get("resolve_linked"):
        text = (data.get("fix_note") or "").strip() or (
            f"Good news — the problem behind this ticket (“{issue.title}”) is fixed. "
            "If you still see it, reply here and the ticket will reopen.")
        for t in linked_open:
            support.platform_update(actor, t.pk, state=S.RESOLVED, response=text)
            resolved += 1
    with no_tenant():
        out = issue_row(issue, counts=_issue_counts())
    out["resolved_now"] = resolved
    return out


def set_ticket_issue(actor, ticket_id, issue_id):
    from . import support

    _require(actor)
    with no_tenant():
        record = _ticket(ticket_id)
        issue = KnownIssue.objects.filter(pk=issue_id).first() if issue_id else None
        if issue_id and issue is None:
            raise TenancyError("That known issue does not exist.")
        if (issue.pk if issue else None) != record.known_issue_id:
            record.known_issue = issue
            record.save(update_fields=["known_issue", "updated_at"])
            _note(record, f"Linked to known issue: {issue.title}." if issue
                  else "No longer linked to a known issue.", actor)
        return support.row(_ticket(record.pk), for_platform=True)


def match_issues(words, category="", limit=3):
    """Public known issues whose title/symptoms share enough of ``words``."""
    from . import support

    if not words:
        return []
    found = []
    for i in KnownIssue.objects.filter(is_public=True).exclude(workaround="", state=KnownIssue.State.INVESTIGATING):
        theirs = support._tokens(f"{i.title} {i.symptoms}")
        if not theirs:
            continue
        score = len(words & theirs) / len(words)
        if category and i.category == category:
            score += 0.15
        if score >= 0.34:
            found.append((score, i))
    found.sort(key=lambda x: -x[0])
    counts = _issue_counts()
    return [{"id": str(i.id), "title": i.title, "state": i.state,
             "state_display": i.get_state_display(), "workaround": i.workaround[:800],
             "resolved_for": counts.get(i.pk, {}).get("orgs", 0),
             "score": round(min(s, 1.0), 2)} for s, i in found[:limit]]


def record_deflection(source):
    """``source`` "issue:<uuid>" credits that known issue too."""
    if isinstance(source, str) and source.startswith("issue:"):
        from django.db.models import F
        try:
            with no_tenant():
                KnownIssue.objects.filter(pk=source[6:]).update(deflected_count=F("deflected_count") + 1)
        except Exception:                                # noqa: BLE001 - bad uuid
            pass


# ===========================================================================
# Ticket detail: everything internal about one ticket
# ===========================================================================
def ticket_extras(record):
    """Team, links, tasks, known issue, escalation, and the customer story."""
    from . import success

    out = {
        "team": str(record.team_id) if record.team_id else None,
        "team_name": record.team.name if record.team_id else None,
        "escalation_level": record.escalation_level, "escalated_at": record.escalated_at,
        "known_issue": ({"id": str(record.known_issue_id), "title": record.known_issue.title,
                         "state": record.known_issue.state,
                         "state_display": record.known_issue.get_state_display()}
                        if record.known_issue_id else None),
        "linked": linked(record),
        "tasks": [success.task_row(t) for t in
                  record.tasks.select_related("organization", "assigned_to")],
        "customer": None,
    }
    org = record.organization
    if org is not None:
        row = next((r for r in success.health() if r["slug"] == org.slug), None)
        others = (_tickets().filter(organization=org).exclude(pk=record.pk)
                  .order_by("-created_at")[:5])
        out["customer"] = {
            "slug": org.slug, "name": org.name,
            "health": row["health"] if row else None,
            "band": row["band"] if row else None, "band_label": row["band_label"] if row else None,
            "plan": row["plan"] if row else "",
            "subscription_status": row["subscription_status"] if row else "",
            "reasons": row["reasons"][:3] if row else [],
            "open_tickets": _open_tickets().filter(organization=org).count(),
            "recent_tickets": [_brief(t) for t in others],
            "timeline": [{k: e[k] for k in ("at", "kind", "title", "detail")}
                         for e in success.timeline(org, limit=8)],
        }
    return out


# ===========================================================================
# Part 5: the SLA board
# ===========================================================================
CRITICAL_DUE_HOURS = 2


def _entry(t, now):
    due = t.sla_due_at
    return {"id": str(t.id), "reference": t.reference, "subject": t.subject,
            "organization": getattr(t.organization, "name", ""),
            "priority": t.priority, "priority_display": t.get_priority_display(),
            "status": t.status, "status_display": t.get_status_display(),
            "team": t.team.name if t.team_id else None,
            "assigned_to": _name(t.assigned_to) or None,
            "escalation_level": t.escalation_level,
            "sla_due_at": due,
            "minutes_left": (round((due - now).total_seconds() / 60)
                             if due and t.sla_paused_at is None else None),
            "created_at": t.created_at}


def sla_board(actor, *, team=None):
    """Critical due, overdue, waiting on the customer, waiting on the team,
    resolved today, escalated -- with lists, and the same per team/agent."""
    _require(actor)
    now = timezone.now()
    start_today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    with no_tenant():
        base = _open_tickets().select_related("organization", "assigned_to", "team")
        if team:
            base = base.filter(team_id=team)
        running = base.filter(sla_paused_at__isnull=True, sla_due_at__isnull=False)
        groups = {
            "critical_due": running.filter(priority=P.CRITICAL,
                                           sla_due_at__gte=now,
                                           sla_due_at__lte=now + timedelta(hours=CRITICAL_DUE_HOURS)),
            "overdue": running.filter(sla_due_at__lt=now),
            "waiting_customer": base.filter(status=S.WAITING_CUSTOMER),
            "waiting_team": base.filter(status__in=[S.OPEN, S.IN_PROGRESS]),
            "escalated": base.filter(escalated=True),
            "unassigned": base.filter(assigned_to__isnull=True),
        }
        resolved = (_tickets().exclude(kind=K.FEATURE).filter(resolved_at__gte=start_today)
                    .select_related("organization", "assigned_to", "team"))
        if team:
            resolved = resolved.filter(team_id=team)
        out = {}
        for key, qs in groups.items():
            items = list(qs.order_by("sla_due_at", "created_at")[:50])
            out[key] = {"count": qs.count(), "tickets": [_entry(t, now) for t in items]}
        out["resolved_today"] = {"count": resolved.count(),
                                 "tickets": [_entry(t, now) for t in resolved.order_by("-resolved_at")[:50]]}
        met = (_tickets().exclude(kind=K.FEATURE)
               .filter(resolved_at__gte=now - timedelta(days=30), sla_due_at__isnull=False))
        total = met.count()
        on_time = sum(1 for r in met.only("resolved_at", "sla_due_at") if r.resolved_at <= r.sla_due_at)
        by_team = []
        for t in SupportTeam.objects.filter(is_active=True):
            qs = _open_tickets().filter(team=t)
            by_team.append({"id": str(t.id), "name": t.name, "open": qs.count(),
                            "overdue": qs.filter(sla_due_at__lt=now, sla_paused_at__isnull=True).count(),
                            "unassigned": qs.filter(assigned_to__isnull=True).count()})
        no_team = _open_tickets().filter(team__isnull=True).count()
    out.update({
        "sla_met_30d": {"resolved": total, "on_time": on_time,
                        "percent": round(100 * on_time / total) if total else None},
        "by_team": by_team, "without_team": no_team,
        "rules": [{"priority": k, "hours": v} for k, v in escalation_rules().items()],
        "generated_at": now,
    })
    return out


# ===========================================================================
# Part 9: campaigns
# ===========================================================================
NEAR_RENEWAL_DAYS = 30
SEGMENT_TEST = {
    "inactive": lambda r: any(x["code"] == "inactive" for x in r["reasons"]) and not r["is_new"],
    "low_health": lambda r: r["band"] == "at_risk",
    "trial": lambda r: r["subscription_status"] == "trial",
    "near_renewal": lambda r: (r["subscription_status"] in ("active", "grace")
                               and r["subscription_ends"] is not None
                               and 0 <= (r["subscription_ends"] - timezone.localdate()).days
                               <= NEAR_RENEWAL_DAYS),
}
SEGMENT_TASK = {
    "inactive": "Re-engage — the team has gone quiet",
    "low_health": "Health check call",
    "trial": "Trial check-in — help them get value before it ends",
    "near_renewal": "Renewal conversation",
}


def segment_members(segment):
    from . import success

    test = SEGMENT_TEST.get(segment)
    if test is None:
        raise TenancyError("Unknown segment.")
    return [{"slug": r["slug"], "name": r["name"], "health": r["health"], "band": r["band"],
             "plan": r["plan"], "subscription_status": r["subscription_status"],
             "subscription_ends": r["subscription_ends"], "trial_end": r["trial_end"],
             "reason": next((x["text"] for x in r["reasons"]), "")}
            for r in success.health() if test(r)]


def segments_summary(actor):
    _require(actor)
    return [{"key": k, "label": label, "count": len(segment_members(k)),
             "default_title": SEGMENT_TASK[k]}
            for k, label in SuccessCampaign.Segment.choices]


def campaign_row(c):
    tasks = c.tasks.all()
    total = tasks.count()
    done = tasks.filter(status=SuccessTask.Status.DONE).count()
    return {"id": str(c.id), "name": c.name, "segment": c.segment,
            "segment_display": c.get_segment_display(), "task_kind": c.task_kind,
            "task_title": c.task_title, "notes": c.notes,
            "assigned_to": str(c.assigned_to_id) if c.assigned_to_id else None,
            "assigned_to_name": _name(c.assigned_to) or None,
            "created_by_name": _name(c.created_by) or None,
            "tasks": total, "done": done,
            "open": tasks.filter(status=SuccessTask.Status.OPEN).count(),
            "percent": round(100 * done / total) if total else 0,
            "created_at": c.created_at}


def campaigns(actor):
    _require(actor)
    with no_tenant():
        return [campaign_row(c) for c in
                SuccessCampaign.objects.select_related("assigned_to", "created_by")[:100]]


def create_campaign(actor, data):
    """One outreach task per customer in the segment, now.

    A customer who already has an OPEN task from another campaign on the
    same segment is skipped: two people calling about the same thing is
    worse than one."""
    from .models import Organization

    _require(actor)
    segment = data.get("segment")
    members = segment_members(segment)
    kind = data.get("task_kind") or SuccessTask.Kind.OUTREACH
    if kind not in SuccessTask.Kind.values:
        raise TenancyError("Unknown task type.")
    try:
        due_days = max(0, min(int(data.get("due_in_days") or 7), 90))
    except (TypeError, ValueError):
        due_days = 7
    with no_tenant(), transaction.atomic():
        agent = _staff_member(data.get("assigned_to"))
        label = dict(SuccessCampaign.Segment.choices)[segment]
        campaign = SuccessCampaign.objects.create(
            name=((data.get("name") or "").strip() or
                  f"{label} — {timezone.localdate():%d %b %Y}")[:160],
            segment=segment, task_kind=kind,
            task_title=((data.get("task_title") or "").strip() or SEGMENT_TASK[segment])[:200],
            notes=(data.get("notes") or "").strip(), assigned_to=agent, created_by=actor)
        busy = set(SuccessTask.objects.filter(status=SuccessTask.Status.OPEN,
                                              campaign__segment=segment)
                   .values_list("organization__slug", flat=True))
        orgs = {o.slug: o for o in Organization.objects.filter(slug__in=[m["slug"] for m in members])}
        due = timezone.localdate() + timedelta(days=due_days)
        created = skipped = 0
        for m in members:
            org = orgs.get(m["slug"])
            if org is None or m["slug"] in busy:
                skipped += 1
                continue
            SuccessTask.objects.create(
                organization=org, campaign=campaign, kind=kind, title=campaign.task_title,
                notes="\n".join(x for x in (m["reason"], campaign.notes) if x),
                due_date=due, assigned_to=agent, created_by=actor,
                auto_reason=f"campaign:{segment}"[:40])
            created += 1
        out = campaign_row(campaign)
    out.update({"created": created, "skipped": skipped})
    return out


# ===========================================================================
# Part 10: the founder view
# ===========================================================================
def _renewal_value(subscription, currency):
    from . import plans

    price = subscription.plan_price
    if price is None or price.currency != currency:
        try:
            price = plans.current_price(subscription.plan, currency=currency)
        except Exception:                                # noqa: BLE001 - NoActivePrice
            return None
    return price.amount_minor


def operations(actor):
    """MRR, ARR, customers, risk, tickets, satisfaction, renewal pipeline."""
    from . import console, success, support
    from .models import Organization, PlatformMetric as M, Subscription

    _require(actor)
    base = success.executive(actor)
    rows = success.health()
    today = timezone.localdate()
    currency = base["currency"]
    with no_tenant():
        subs = list(Subscription.objects.filter(status__in=[
            Subscription.Status.ACTIVE, Subscription.Status.TRIAL, Subscription.Status.GRACE])
            .select_related("organization", "plan", "plan_price"))
        pipeline = {30: {"count": 0, "value_minor": 0, "unpriced": 0},
                    60: {"count": 0, "value_minor": 0, "unpriced": 0},
                    90: {"count": 0, "value_minor": 0, "unpriced": 0}}
        upcoming = []
        for s in subs:
            ends = s.current_period_end or s.trial_end
            if not ends:
                continue
            days = (ends - today).days
            if days < 0 or days > 90:
                continue
            value = _renewal_value(s, currency)
            for window in (30, 60, 90):
                if days <= window:
                    pipeline[window]["count"] += 1
                    if value is None:
                        pipeline[window]["unpriced"] += 1
                    else:
                        pipeline[window]["value_minor"] += value
            upcoming.append({"slug": s.organization.slug, "name": s.organization.name,
                             "status": s.status, "ends_on": ends, "days_left": days,
                             "plan": getattr(s.plan, "name", ""), "value_minor": value})
        upcoming.sort(key=lambda r: r["days_left"])
        overview = support.overview()
        since = today - timedelta(days=29)
        shown = M.objects.filter(key=M.Key.ASSIST_SHOWN, day__gte=since).aggregate(n=Sum("count"))["n"] or 0
        deflected = base["tickets_deflected_30d"]
        active = Organization.objects.filter(status=Organization.Status.ACTIVE).count()
        new_tickets = _tickets().exclude(kind=K.FEATURE).filter(
            created_at__gte=timezone.now() - timedelta(days=30)).count()
        escalated_open = _open_tickets().filter(escalated=True).count()
        overdue_tasks = SuccessTask.objects.filter(status=SuccessTask.Status.OPEN,
                                                   due_date__lt=today).count()
    paying = console.revenue_forecast(actor)
    return {
        **base,
        "active_customers": active,
        "paying_customers": paying["counted"],
        "at_risk_customers": sum(1 for r in rows if r["band"] == "at_risk"),
        "open_tickets": overview["open"],
        "escalated_open": escalated_open,
        "tickets_30d": new_tickets,
        "assist_shown_30d": shown,
        "deflection_rate_30d": round(100 * deflected / shown) if shown else None,
        "renewal_pipeline": {str(k): v for k, v in pipeline.items()},
        "renewals_upcoming": upcoming[:15],
        "overdue_tasks": overdue_tasks,
        "bands": {b: sum(1 for r in rows if r["band"] == b)
                  for b in ("healthy", "watch", "at_risk", "onboarding")},
    }
