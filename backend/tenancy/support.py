"""Customer Success Center: tickets, feature requests, What's New, System Status.

Inside the product, because a customer who has to find an email address, a
WhatsApp number or a developer's phone often doesn't bother -- and the
platform never hears about the problem until they cancel.

TICKETS. A customer picks a category and writes a title, a description and,
optionally, a screenshot or file. Everything else is captured: organization,
person, page, full URL, browser, device, viewport, plan, tenant and time. The
conversation is a thread of ``SupportMessage`` rows; ``is_internal`` notes are
staff-only and never leave the console.

SLA. Measured to resolution from the moment the ticket is opened, by
priority (critical 4h, high 8h, medium 24h, low 72h; ``SUPPORT_SLA_HOURS``
overrides). The clock PAUSES while the ticket is "Waiting for you" -- time
the customer holds the ball is not time the platform is late -- and resumes,
due date pushed back by the pause, when they reply.

PLATFORM-GLOBAL. Every table here is read from the console with no tenant
bound (``no_tenant``), exactly like Payment. A customer only ever reaches
their own organization's rows: each query is filtered by the caller's
organization, and ``_customer_ticket`` refuses anything else as not found.

Ratings ("How was your experience?", "Rate this feature") still use the same
table with kind=feedback, so product feedback and support live in one inbox.
"""
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, F, Max, Q
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .context import no_tenant
from .models import ProductUpdate, StatusNotice, SupportMessage, SupportRequest

logger = logging.getLogger(__name__)
K = SupportRequest.Kind
S = SupportRequest.Status
C = SupportRequest.Category
P = SupportRequest.Priority
R = SupportRequest.Roadmap

# A resolved ticket can be reopened by a reply for this long; after that the
# customer is asked to open a new one, so a months-old thread is not revived.
REOPEN_WINDOW = timedelta(days=14)
# Files a customer may attach: screenshots and the documents people actually
# send support. Same validator as every other upload in the product.
SUPPORT_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "docx", "xlsx", "csv"}
SUPPORT_MAX_BYTES = 10 * 1024 * 1024


# ===========================================================================
# helpers
# ===========================================================================
def sla_hours(priority):
    overrides = getattr(settings, "SUPPORT_SLA_HOURS", None) or {}
    return int(overrides.get(priority, SupportRequest.SLA_HOURS.get(priority, 24)))


def _validate_file(upload, field):
    if upload is None:
        return None
    from config.uploads import validate_attachment

    try:
        validate_attachment(upload, max_size=SUPPORT_MAX_BYTES, extensions=SUPPORT_EXTENSIONS)
    except Exception as exc:                        # noqa: BLE001 -- Django ValidationError
        messages = getattr(exc, "messages", None) or [str(exc)]
        raise ValidationError({field: messages}) from exc
    return upload


def _next_number():
    current = SupportRequest.objects.aggregate(top=Max("number"))["top"] or 0
    return current + 1


def _create_with_number(**fields):
    """Assign SUP-000001, SUP-000002... A unique column + retry, not a lock:
    two tickets in the same millisecond is rare and the retry is cheap."""
    for _ in range(5):
        try:
            with transaction.atomic():
                return SupportRequest.objects.create(number=_next_number(), **fields)
        except IntegrityError:
            continue
    return SupportRequest.objects.create(**fields)


def _plan_label(organization):
    sub = getattr(organization, "subscription", None) if organization else None
    plan = getattr(sub, "plan", None)
    if plan is None:
        return ""
    return getattr(plan, "name", "") or getattr(plan, "code", "")


def _ticket_url(record):
    """Where the customer reads this ticket -- their own workspace address."""
    try:
        from .handover import login_url

        base = login_url(record.organization) if record.organization else ""
    except Exception:                               # noqa: BLE001
        base = ""
    base = (base or getattr(settings, "FRONTEND_URL", "")).rstrip("/")
    return f"{base}/help/tickets/{record.id}"


def _mail(subject, body, to):
    from django.core.mail import send_mail

    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
        return True
    except Exception:                               # noqa: BLE001
        logger.warning("support email failed: %s", subject, exc_info=True)
        return False


def _platform_name():
    return getattr(settings, "PLATFORM_NAME", "Buddhi Labs")


def _system(record, text, *, internal=False, actor=None):
    SupportMessage.objects.create(
        ticket=record, author=actor if getattr(actor, "pk", None) else None,
        author_name=_platform_name(), author_kind=SupportMessage.AuthorKind.SYSTEM,
        body=text, is_internal=internal)


def _file_meta(field):
    if not field:
        return None
    return {"name": field.name.rsplit("/", 1)[-1]}


# ===========================================================================
# serialisation
# ===========================================================================
def message_row(m):
    return {"id": str(m.id), "author_name": m.author_name, "author_kind": m.author_kind,
            "body": m.body, "is_internal": m.is_internal,
            "attachment": _file_meta(m.attachment), "created_at": m.created_at}


def row(r, *, for_platform=False, messages=None):
    out = {
        "id": str(r.id), "reference": r.reference, "kind": r.kind,
        "kind_display": r.get_kind_display(),
        "category": r.category, "category_display": r.get_category_display() if r.category else "",
        "subject": r.subject, "message": r.message, "rating": r.rating,
        "feature": r.feature, "page": r.page, "url": r.url,
        "status": r.status, "status_display": r.get_status_display(),
        "priority": r.priority, "priority_display": r.get_priority_display(),
        "roadmap_status": r.roadmap_status,
        "roadmap_status_display": r.get_roadmap_status_display() if r.roadmap_status else "",
        "response": r.response,
        "screenshot": _file_meta(r.screenshot), "attachment": _file_meta(r.attachment),
        "unread": r.unread_by_customer,
        "satisfaction_rating": r.satisfaction_rating,
        "can_rate": (r.is_ticket and r.kind != K.FEATURE and r.satisfaction_rating is None
                     and r.status in (S.RESOLVED, S.CLOSED)),
        "can_reply": r.status != S.CLOSED,
        "submitted_by_name": r.submitted_by_name,
        "created_at": r.created_at, "updated_at": r.updated_at,
        "resolved_at": r.resolved_at,
    }
    if for_platform:
        out.update({
            "organization_name": getattr(r.organization, "name", ""),
            "organization_slug": getattr(r.organization, "slug", ""),
            "submitted_by_email": r.submitted_by_email,
            "submitted_by_role": r.submitted_by_role,
            "user_agent": r.user_agent, "context": r.context,
            "assigned_to": str(r.assigned_to_id) if r.assigned_to_id else None,
            "assigned_to_name": (r.assigned_to.get_full_name() or r.assigned_to.username)
            if r.assigned_to_id else None,
            "escalated": r.escalated, "escalation_level": r.escalation_level,
            "team": str(r.team_id) if r.team_id else None,
            "team_name": r.team.name if r.team_id else None,
            "known_issue": str(r.known_issue_id) if r.known_issue_id else None,
            "sla_due_at": r.sla_due_at,
            "sla_paused": r.sla_paused_at is not None, "overdue": r.is_overdue,
            "first_response_at": r.first_response_at, "closed_at": r.closed_at,
            "unread": r.unread_by_staff,
            "satisfaction_comment": r.satisfaction_comment,
        })
    if messages is not None:
        out["messages"] = [message_row(m) for m in messages]
    return out


# ===========================================================================
# customer side
# ===========================================================================
class SupportRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=K.choices, required=False, default=K.PROBLEM)
    category = serializers.ChoiceField(choices=C.choices, required=False, allow_blank=True, default="")
    subject = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    message = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")
    rating = serializers.IntegerField(min_value=1, max_value=5, required=False, allow_null=True, default=None)
    feature = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    page = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    url = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    # A JSON object from the browser; a string when the form is multipart.
    context = serializers.JSONField(required=False, default=dict)

    def validate_context(self, value):
        if isinstance(value, str):
            try:
                value = json.loads(value or "{}")
            except ValueError as exc:
                raise ValidationError("Not valid JSON.") from exc
        if not isinstance(value, dict):
            raise ValidationError("Expected an object.")
        # Only short scalar facts; a page cannot stuff the ticket with data.
        return {str(k)[:40]: (str(v)[:200] if not isinstance(v, (int, float, bool)) else v)
                for k, v in list(value.items())[:20]}

    def validate(self, data):
        if data["kind"] == K.FEEDBACK:
            if data.get("rating") is None:
                raise ValidationError({"rating": "Choose a rating from 1 to 5."})
            return data
        if data.get("category") == C.FEATURE_REQUEST:
            data["kind"] = K.FEATURE
        elif data["kind"] == K.FEATURE and not data.get("category"):
            data["category"] = C.FEATURE_REQUEST
        if not (data.get("message") or "").strip():
            raise ValidationError({"message": "Tell us a little about it — a sentence is enough."})
        return data


def submit(user, data, request=None, *, screenshot=None, attachment=None):
    """Record a ticket (or a rating) from a signed-in person. Returns the row."""
    meta = getattr(request, "META", {}) or {}
    organization = getattr(user, "organization", None)
    ticket = data["kind"] != K.FEEDBACK
    category = data.get("category") or ""
    if ticket and not category:
        category = C.FEATURE_REQUEST if data["kind"] == K.FEATURE else C.OTHER
    priority = SupportRequest.DEFAULT_PRIORITY.get(category, P.MEDIUM)

    context = dict(data.get("context") or {})
    context.update({
        "organization": getattr(organization, "name", ""),
        "tenant": getattr(organization, "slug", ""),
        "plan": _plan_label(organization),
        "role": getattr(user, "role", "") or "",
        "received_at": timezone.now().isoformat(),
    })
    now = timezone.now()
    fields = dict(
        organization_id=getattr(user, "organization_id", None),
        kind=data["kind"], category=category if ticket else "",
        priority=priority, subject=(data.get("subject") or "").strip()[:200],
        message=(data.get("message") or "").strip(),
        rating=data.get("rating"), feature=(data.get("feature") or "").strip()[:80],
        page=(data.get("page") or "").strip()[:300],
        url=(data.get("url") or "").strip()[:500],
        context=context if ticket else {},
        user_agent=(meta.get("HTTP_USER_AGENT") or "")[:300],
        submitted_by_email=getattr(user, "email", "") or "",
        submitted_by_name=(user.get_full_name() or user.username)[:150],
        submitted_by_role=getattr(user, "role", "") or "",
        unread_by_staff=ticket,
    )
    if ticket and data["kind"] == K.FEATURE:
        fields["roadmap_status"] = R.SUBMITTED
    elif ticket:
        fields["sla_due_at"] = now + timedelta(hours=sla_hours(priority))
    with no_tenant():
        record = _create_with_number(**fields) if ticket else SupportRequest.objects.create(**fields)
        if ticket:
            from . import desk
            desk.assign_new(record)
        if screenshot is not None or attachment is not None:
            record.screenshot = screenshot or ""
            record.attachment = attachment or ""
            record.save(update_fields=["screenshot", "attachment", "updated_at"])
    support = getattr(settings, "PLATFORM_SUPPORT_EMAIL", "")
    if support and ticket:
        _mail(f"[{record.reference}] [{record.get_category_display()}] "
              f"{record.subject or record.message[:60]} — {getattr(organization, 'name', 'platform')}",
              f"From: {record.submitted_by_name} <{record.submitted_by_email}>\n"
              f"Organization: {getattr(organization, 'name', '—')} ({context.get('plan') or 'no plan'})\n"
              f"Priority: {record.get_priority_display()}\n"
              f"Page: {record.url or record.page or '—'}\n"
              f"Browser: {context.get('browser', '—')} · {context.get('device', '—')}\n\n"
              f"{record.message}\n", [support])
    return record


def _can_see_organization(user):
    from users import roles
    return roles.is_admin(user)


def _customer_ticket(user, ticket_id):
    """The caller's own ticket -- or, for an administrator, any of their
    organization's. Anything else is 'not found', never 'forbidden'."""
    try:
        record = SupportRequest.objects.select_related("organization").get(pk=ticket_id)
    except (SupportRequest.DoesNotExist, ValueError, TypeError):
        raise Http404
    if record.organization_id != getattr(user, "organization_id", None) or record.kind == K.FEEDBACK:
        raise Http404
    if record.submitted_by_email != user.email and not _can_see_organization(user):
        raise Http404
    return record


class SupportRequestsView(APIView):
    """GET your tickets; POST a new one (multipart for a screenshot)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support"
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        params = request.query_params
        with no_tenant():
            rows = (SupportRequest.objects
                    .filter(organization_id=request.user.organization_id)
                    .exclude(kind=K.FEEDBACK))
            if not (params.get("scope") == "organization" and _can_see_organization(request.user)):
                rows = rows.filter(submitted_by_email=request.user.email)
            if params.get("kind"):
                rows = rows.filter(kind=params["kind"])
            if params.get("open") == "1":
                rows = rows.filter(status__in=SupportRequest.OPEN_STATES)
            return Response([row(r) for r in rows[:100]])

    def post(self, request):
        serializer = SupportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        screenshot = _validate_file(request.FILES.get("screenshot"), "screenshot")
        attachment = _validate_file(request.FILES.get("attachment"), "attachment")
        record = submit(request.user, serializer.validated_data, request,
                        screenshot=screenshot, attachment=attachment)
        if record.kind == K.FEEDBACK:
            detail = "Thanks — your rating helps us decide what to improve."
        elif record.kind == K.FEATURE:
            detail = (f"Thanks — {record.reference} is in. Follow it under "
                      f"Help & Support → Feature requests.")
        else:
            detail = (f"Thanks — {record.reference} is open. We’ll reply here and by "
                      f"email; follow it under Help & Support → My tickets.")
        return Response({**row(record), "detail": detail}, status=status.HTTP_201_CREATED)


class SupportTicketView(APIView):
    """GET one ticket with its public conversation; POST {action: close}."""

    permission_classes = [IsAuthenticated]

    def get(self, request, ticket_id):
        with no_tenant():
            record = _customer_ticket(request.user, ticket_id)
            if record.unread_by_customer and record.submitted_by_email == request.user.email:
                SupportRequest.objects.filter(pk=record.pk).update(unread_by_customer=False)
                record.unread_by_customer = False
            messages = list(record.messages.filter(is_internal=False))
            return Response(row(record, messages=messages))

    def post(self, request, ticket_id):
        action = request.data.get("action")
        with no_tenant():
            record = _customer_ticket(request.user, ticket_id)
            if action == "close":
                if record.status == S.CLOSED:
                    return Response(row(record))
                record.status, record.closed_at = S.CLOSED, timezone.now()
                record.resolved_at = record.resolved_at or record.closed_at
                record.sla_paused_at = None
                record.unread_by_staff = True
                record.save()
                _system(record, f"Closed by {record.submitted_by_name if record.submitted_by_email == request.user.email else request.user.get_full_name()}.")
                return Response(row(record, messages=list(record.messages.filter(is_internal=False))))
            if action == "rate":
                return self._rate(request, record)
        raise ValidationError({"action": "Use close or rate."})

    def _rate(self, request, record):
        try:
            rating = int(request.data.get("rating"))
        except (TypeError, ValueError):
            rating = 0
        if not 1 <= rating <= 5:
            raise ValidationError({"rating": "Choose a rating from 1 to 5."})
        if record.status not in (S.RESOLVED, S.CLOSED) or record.kind == K.FEATURE:
            raise ValidationError({"detail": "You can rate support once the ticket is resolved."})
        if record.satisfaction_rating is not None:
            raise ValidationError({"detail": "Thanks — you’ve already rated this one."})
        record.satisfaction_rating = rating
        record.satisfaction_comment = (request.data.get("comment") or "").strip()[:2000]
        record.satisfaction_at = timezone.now()
        record.save(update_fields=["satisfaction_rating", "satisfaction_comment",
                                   "satisfaction_at", "updated_at"])
        return Response(row(record))


class SupportReplyView(APIView):
    """POST a customer reply (multipart, optional attachment)."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support"
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def post(self, request, ticket_id):
        body = (request.data.get("body") or "").strip()
        upload = _validate_file(request.FILES.get("attachment"), "attachment")
        if not body and upload is None:
            raise ValidationError({"body": "Write a reply or attach a file."})
        with no_tenant():
            record = _customer_ticket(request.user, ticket_id)
            if record.status == S.CLOSED:
                raise ValidationError({"detail": "This ticket is closed. Please open a new one — "
                                                 "we’ll link them up."})
            now = timezone.now()
            if record.status == S.RESOLVED:
                if record.resolved_at and now - record.resolved_at > REOPEN_WINDOW:
                    raise ValidationError({"detail": "This ticket was resolved a while ago. Please "
                                                     "open a new one so it gets fresh attention."})
                record.status, record.resolved_at = S.OPEN, None
                if record.kind != K.FEATURE:
                    record.sla_due_at = now + timedelta(hours=sla_hours(record.priority))
                _system(record, "Reopened by the customer’s reply.")
            elif record.status == S.WAITING_CUSTOMER:
                _resume_sla(record, now)
                record.status = S.IN_PROGRESS
            message = SupportMessage.objects.create(
                ticket=record, author=request.user,
                author_name=(request.user.get_full_name() or request.user.username)[:150],
                author_kind=SupportMessage.AuthorKind.CUSTOMER, body=body,
                attachment=upload or "")
            record.unread_by_staff, record.unread_by_customer = True, False
            record.save()
        return Response(message_row(message), status=status.HTTP_201_CREATED)


def _file_for(record, which, message_id=None):
    if which == "message":
        message = record.messages.filter(pk=message_id).first()
        field = message.attachment if message else None
        if message is not None and message.is_internal:
            field = None
        return field
    return {"screenshot": record.screenshot, "attachment": record.attachment}.get(which)


def _serve(field):
    from config.uploads import harden_file_response

    if not field:
        raise Http404
    response = FileResponse(field.open("rb"), as_attachment=False,
                            filename=field.name.rsplit("/", 1)[-1])
    return harden_file_response(response)


class SupportFileView(APIView):
    """GET a ticket's screenshot/attachment, or a message attachment -- the
    customer who can see the ticket only. Never a public link."""

    permission_classes = [IsAuthenticated]

    def get(self, request, ticket_id, which, message_id=None):
        with no_tenant():
            record = _customer_ticket(request.user, ticket_id)
            return _serve(_file_for(record, which, message_id))


class FeatureRequestsView(APIView):
    """Your organization's feature requests and where each stands."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        with no_tenant():
            rows = (SupportRequest.objects
                    .filter(organization_id=request.user.organization_id, kind=K.FEATURE)
                    .order_by("-created_at")[:100])
            return Response({"roadmap": [{"value": v, "label": label} for v, label in R.choices],
                             "requests": [row(r) for r in rows]})


# --- What's New ------------------------------------------------------------
def update_row(u):
    return {"id": str(u.id), "title": u.title, "summary": u.summary, "body": u.body,
            "category": u.category, "category_display": u.get_category_display(),
            "link": u.link, "published_at": u.published_at}


def _seen_at(user):
    raw = (getattr(user, "ui_state", None) or {}).get("updates_seen_at")
    try:
        from django.utils.dateparse import parse_datetime
        return parse_datetime(raw) if raw else None
    except (TypeError, ValueError):
        return None


class ProductUpdatesView(APIView):
    """GET published updates + how many are new to you; POST marks them seen."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        with no_tenant():
            rows = list(ProductUpdate.objects.filter(
                published_at__isnull=False, published_at__lte=timezone.now())[:50])
        seen = _seen_at(request.user)
        unseen = [u for u in rows if seen is None or u.published_at > seen]
        return Response({"updates": [{**update_row(u), "is_new": u in unseen} for u in rows],
                         "unseen": len(unseen)})

    def post(self, request):
        user = request.user
        state = dict(getattr(user, "ui_state", None) or {})
        state["updates_seen_at"] = timezone.now().isoformat()
        type(user).objects.filter(pk=user.pk).update(ui_state=state)
        return Response({"unseen": 0})


# --- System Status ---------------------------------------------------------
STATUS_CACHE_SECONDS = 60
_LEVELS = {"operational": 0, "maintenance": 1, "degraded": 2, "outage": 3}


def _component(key, label, state, note=""):
    return {"key": key, "label": label, "state": state, "note": note}


def _check_platform():
    try:
        # Any real query proves the database answers; an ORM one keeps this
        # module out of the raw-SQL inventory (test_phase_c_isolation).
        ProductUpdate.objects.exists()
        return "operational", ""
    except Exception:                               # noqa: BLE001
        return "outage", "The database is not answering."


def _check_email():
    from django.db.models import Sum

    from .models import PlatformMetric

    backend = getattr(settings, "EMAIL_BACKEND", "")
    if any(x in backend for x in ("console", "locmem", "dummy")):
        return "degraded", "Email is not being delivered by this deployment."
    keys = [PlatformMetric.Key.EMAIL_SENT, PlatformMetric.Key.EMAIL_FAILED]
    agg = {r["key"]: r["total"] or 0 for r in PlatformMetric.objects.filter(
        day__gte=timezone.localdate() - timedelta(days=1), key__in=keys)
        .values("key").annotate(total=Sum("count"))}
    sent, failed = agg.get(keys[0], 0), agg.get(keys[1], 0)
    if failed and failed >= max(3, sent):
        return "degraded", "Some emails are failing to send."
    return "operational", ""


def _check_storage():
    from .launch import check_storage

    ok = check_storage().get("ready", True)
    return ("operational", "") if ok else ("outage", "Files can't be uploaded right now.")


def _check_payments():
    from .models import PaymentInstruction

    if PaymentInstruction.objects.filter(is_active=True).exists():
        return "operational", ""
    return "degraded", "No payment method is open right now."


def _check_domains():
    try:
        import dns.resolver  # noqa: F401
        return "operational", ""
    except ImportError:
        return "outage", "Custom domains can't be verified right now."


def _check_attendance():
    from monitoring import heartbeat

    states = [heartbeat.status(job)["state"] for job in ("DEVICE_SYNC", "PROCESS_PUNCHES")]
    if all(s == "green" for s in states):
        return "operational", ""
    if any(s == "red" for s in states):
        return "degraded", "Biometric device collection is delayed. App check-in is not affected."
    return "degraded", "Biometric device collection is running late."


CHECKS = (
    ("platform", "Platform", _check_platform),
    ("email", "Email delivery", _check_email),
    ("storage", "File storage", _check_storage),
    ("payments", "Payments", _check_payments),
    ("domains", "Domain verification", _check_domains),
    ("attendance", "Attendance services", _check_attendance),
)


def system_status():
    """Every component's state, plus active notices. Cached briefly: it is
    read by every customer who opens the page during an incident."""
    from django.core.cache import cache

    cached = cache.get("support:system-status")
    if cached is not None:
        return cached
    components = []
    with no_tenant():
        notices = list(StatusNotice.objects.filter(resolved_at__isnull=True,
                                                   starts_at__lte=timezone.now()))
        for key, label, check in CHECKS:
            try:
                state, note = check()
            except Exception:                       # noqa: BLE001
                logger.warning("status check %s failed", key, exc_info=True)
                state, note = "degraded", "We couldn't check this just now."
            for notice in notices:
                if notice.component == key and _LEVELS[notice.level] >= _LEVELS[state]:
                    state, note = notice.level, notice.message
            components.append(_component(key, label, state, note))
    worst = max((c["state"] for c in components), key=lambda s: _LEVELS[s])
    payload = {
        "overall": worst,
        "summary": {"operational": "All systems operational.",
                    "maintenance": "Scheduled maintenance in progress.",
                    "degraded": "Some services are degraded.",
                    "outage": "There is an outage affecting part of the platform."}[worst],
        "components": components,
        "notices": [{"id": str(n.id), "component": n.component,
                     "component_display": n.get_component_display(), "level": n.level,
                     "message": n.message, "starts_at": n.starts_at} for n in notices],
        "checked_at": timezone.now(),
    }
    cache.set("support:system-status", payload, STATUS_CACHE_SECONDS)
    return payload


class SystemStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(system_status())


# ===========================================================================
# platform side
# ===========================================================================
def _require(actor):
    from .console import require_platform
    require_platform(actor)


def _base_qs():
    return (SupportRequest.objects.select_related("organization", "assigned_to", "team")
            .exclude(kind=K.FEEDBACK))


def overview():
    """The numbers for the console's Support Overview card and desk header."""
    now = timezone.now()
    tickets = SupportRequest.objects.exclude(kind=K.FEEDBACK)
    open_qs = tickets.filter(status__in=SupportRequest.OPEN_STATES)
    window = now - timedelta(days=30)
    resolved = tickets.filter(resolved_at__gte=window, resolved_at__isnull=False)
    durations = [(r.resolved_at - r.created_at).total_seconds() / 3600
                 for r in resolved.only("created_at", "resolved_at")]
    first = [(r.first_response_at - r.created_at).total_seconds() / 3600
             for r in tickets.filter(created_at__gte=window, first_response_at__isnull=False)
             .only("created_at", "first_response_at")]
    csat = tickets.filter(satisfaction_at__gte=window).aggregate(avg=Avg("satisfaction_rating"),
                                                                 n=Count("id"))
    return {
        "open": open_qs.exclude(kind=K.FEATURE).count(),
        "assigned": open_qs.filter(assigned_to__isnull=False).count(),
        "unassigned": open_qs.filter(assigned_to__isnull=True).exclude(kind=K.FEATURE).count(),
        "critical": open_qs.filter(priority=P.CRITICAL).count(),
        "overdue": open_qs.filter(sla_due_at__lt=now, sla_paused_at__isnull=True).count(),
        "unread": tickets.filter(unread_by_staff=True).exclude(status=S.CLOSED).count(),
        "waiting_customer": tickets.filter(status=S.WAITING_CUSTOMER).count(),
        "waiting_team": open_qs.filter(status__in=[S.OPEN, S.IN_PROGRESS]).exclude(kind=K.FEATURE).count(),
        "escalated": open_qs.filter(escalated=True).exclude(kind=K.FEATURE).count(),
        "resolved": tickets.filter(status=S.RESOLVED).count(),
        "closed": tickets.filter(status=S.CLOSED).count(),
        "feature_requests_open": tickets.filter(kind=K.FEATURE).exclude(
            roadmap_status=R.COMPLETED).count(),
        "avg_resolution_hours_30d": round(sum(durations) / len(durations), 1) if durations else None,
        "avg_first_response_hours_30d": round(sum(first) / len(first), 1) if first else None,
        "csat_30d": {"average": round(csat["avg"], 2) if csat["avg"] else None,
                     "count": csat["n"]},
    }


def platform_list(actor, *, kind=None, state=None, filters=None):
    _require(actor)
    filters = filters or {}
    now = timezone.now()
    with no_tenant():
        rows = SupportRequest.objects.select_related("organization", "assigned_to", "team")
        if kind:
            rows = rows.filter(kind=kind)
        else:
            rows = rows.exclude(kind=K.FEEDBACK) if filters.get("view") != "all" else rows
        view = filters.get("view")
        if state:
            rows = rows.filter(status=state)
        elif view == "open":
            # Feature requests have their own view and no SLA; listing them
            # here would make the Open view disagree with the Open count.
            rows = rows.filter(status__in=SupportRequest.OPEN_STATES).exclude(kind=K.FEATURE)
        elif view == "critical":
            rows = rows.filter(status__in=SupportRequest.OPEN_STATES, priority=P.CRITICAL)
        elif view == "unread":
            rows = rows.filter(unread_by_staff=True).exclude(status=S.CLOSED)
        elif view == "overdue":
            rows = rows.filter(status__in=SupportRequest.OPEN_STATES, sla_due_at__lt=now,
                               sla_paused_at__isnull=True)
        elif view == "mine":
            rows = rows.filter(assigned_to=actor, status__in=SupportRequest.OPEN_STATES)
        elif view == "escalated":
            rows = rows.filter(escalated=True, status__in=SupportRequest.OPEN_STATES)
        elif view == "unassigned":
            rows = rows.filter(assigned_to__isnull=True, status__in=SupportRequest.OPEN_STATES)
        elif view == "my_teams":
            rows = rows.filter(team__members__user=actor, status__in=SupportRequest.OPEN_STATES)
        if view in ("critical", "overdue", "escalated", "unassigned", "my_teams"):
            rows = rows.exclude(kind=K.FEATURE)
        for param, field in (("organization", "organization__slug"), ("priority", "priority"),
                             ("category", "category")):
            if filters.get(param):
                rows = rows.filter(**{field: filters[param]})
        if filters.get("team") == "none":
            rows = rows.filter(team__isnull=True)
        elif filters.get("team"):
            rows = rows.filter(team_id=filters["team"])
        agent = filters.get("assigned")
        if agent == "none":
            rows = rows.filter(assigned_to__isnull=True)
        elif agent:
            rows = rows.filter(assigned_to_id=agent)
        if filters.get("q"):
            q = filters["q"].strip()
            query = (Q(subject__icontains=q) | Q(message__icontains=q)
                     | Q(submitted_by_email__icontains=q) | Q(organization__name__icontains=q))
            if q.upper().startswith("SUP-") and q[4:].isdigit():
                query |= Q(number=int(q[4:]))
            rows = rows.filter(query)
        rows = list(rows.order_by(F("unread_by_staff").desc(), "-created_at")[:200])
        ratings = [r.rating for r in SupportRequest.objects.filter(
            kind=K.FEEDBACK, rating__isnull=False,
            created_at__gte=now - timedelta(days=30))]
        summary = overview()
    return {
        "requests": [row(r, for_platform=True) for r in rows],
        "summary": summary,
        "feedback_30d": {
            "count": len(ratings),
            "average": round(sum(ratings) / len(ratings), 2) if ratings else None,
        },
    }


def platform_detail(actor, ticket_id):
    _require(actor)
    with no_tenant():
        record = _platform_ticket(ticket_id)
        if record.unread_by_staff:
            SupportRequest.objects.filter(pk=record.pk).update(unread_by_staff=False)
            record.unread_by_staff = False
        from . import desk
        desk.mark_mentions_read(actor, record.pk)
        out = row(record, for_platform=True, messages=list(record.messages.all()))
        out.update(desk.ticket_extras(record))
        return out


def _platform_ticket(ticket_id):
    from .exceptions import TenancyError

    try:
        return (SupportRequest.objects.select_related("organization", "assigned_to", "team",
                                                      "known_issue").get(pk=ticket_id))
    except (SupportRequest.DoesNotExist, ValueError, TypeError):
        raise TenancyError("That ticket does not exist.")


def _pause_sla(record, now):
    if record.sla_paused_at is None and record.sla_due_at:
        record.sla_paused_at = now


def _resume_sla(record, now):
    if record.sla_paused_at is not None:
        if record.sla_due_at:
            record.sla_due_at += now - record.sla_paused_at
        record.sla_paused_at = None


ESCALATION = {P.LOW: P.MEDIUM, P.MEDIUM: P.HIGH, P.HIGH: P.CRITICAL, P.CRITICAL: P.CRITICAL}


def platform_update(actor, request_id, *, state=None, response=None, request=None,
                    priority=None, category=None, assigned_to=None, assign=False,
                    escalate=False, roadmap_status=None, escalate_reason=""):
    """Move a ticket on. Every change leaves a line in the thread."""
    from . import desk
    from .exceptions import TenancyError

    _require(actor)
    now = timezone.now()
    with no_tenant():
        record = _platform_ticket(request_id)
        if priority and priority != record.priority:
            if priority not in P.values:
                raise TenancyError("Unknown priority.")
            if record.sla_due_at:
                record.sla_due_at += timedelta(hours=sla_hours(priority) - sla_hours(record.priority))
            _system(record, f"Priority {record.get_priority_display()} → "
                            f"{dict(P.choices)[priority]}.", internal=True, actor=actor)
            record.priority = priority
        if category and category != record.category:
            if category not in C.values:
                raise TenancyError("Unknown category.")
            record.category = category
        if assign:
            # Assign / reassign / unassign. Staff are read through
            # all_tenants inside desk: they belong to no organization, so the
            # tenant-scoped manager refuses them once tenancy is enforced.
            desk.assign(actor, record, assigned_to)
        if escalate:
            # After any assignment, so an unassigned ticket goes to a lead.
            desk.escalate(record, actor=actor, reason=(escalate_reason or "").strip()[:300])
        if roadmap_status and roadmap_status != record.roadmap_status:
            if roadmap_status not in R.values:
                raise TenancyError("Unknown roadmap status.")
            record.roadmap_status = roadmap_status
            record.unread_by_customer = True
            _system(record, f"Status: {dict(R.choices)[roadmap_status]}.")
            if roadmap_status == R.COMPLETED and record.status in SupportRequest.OPEN_STATES:
                state = state or S.RESOLVED
        if state and state != record.status:
            if state not in S.values:
                raise TenancyError("Unknown status.")
            if state == S.WAITING_CUSTOMER:
                _pause_sla(record, now)
            elif record.status == S.WAITING_CUSTOMER:
                _resume_sla(record, now)
            if state == S.RESOLVED:
                record.resolved_at = now
            if state == S.CLOSED:
                record.closed_at = now
                record.resolved_at = record.resolved_at or now
            if state in (S.OPEN, S.IN_PROGRESS) and record.status in (S.RESOLVED, S.CLOSED):
                record.resolved_at = record.closed_at = None
            record.status = state
            record.unread_by_customer = True
            _system(record, f"Status: {record.get_status_display()}.")
        record.save()
    if response is not None and response.strip() and response.strip() != record.response:
        platform_message(actor, record.pk, body=response, internal=False, notify=True)
        record.refresh_from_db()
    elif state in (S.RESOLVED,) and record.submitted_by_email and record.kind != K.FEATURE:
        _mail(f"[{record.reference}] Resolved: {record.subject or record.get_category_display()}",
              f"Hello {record.submitted_by_name or ''},\n\nWe’ve marked your ticket "
              f"{record.reference} as resolved. If it isn’t, just reply on the ticket and it "
              f"will reopen.\n\nHow was your support experience? You can rate it here:\n"
              f"{_ticket_url(record)}\n\n— The {_platform_name()} team\n",
              [record.submitted_by_email])
    with no_tenant():
        return row(_platform_ticket(record.pk), for_platform=True)


def platform_message(actor, ticket_id, *, body="", internal=False, attachment=None,
                     notify=True):
    """A staff reply (emailed to the customer) or an internal note (never)."""
    from .exceptions import TenancyError

    _require(actor)
    body = (body or "").strip()
    if not body and attachment is None:
        raise TenancyError("Write a reply or attach a file.")
    now = timezone.now()
    with no_tenant():
        record = _platform_ticket(ticket_id)
        message = SupportMessage.objects.create(
            ticket=record, author=actor,
            author_name=(actor.get_full_name() or actor.username)[:150],
            author_kind=SupportMessage.AuthorKind.STAFF, body=body,
            is_internal=internal, attachment=attachment or "")
        if internal and body:
            from . import desk
            desk.record_mentions(actor, record, message)
        if not internal:
            record.response = body or record.response
            record.unread_by_customer = True
            record.first_response_at = record.first_response_at or now
            if record.status == S.OPEN:
                record.status = S.IN_PROGRESS
            record.save()
    if not internal and notify and record.submitted_by_email:
        _mail(f"Re: [{record.reference}] {record.subject or record.get_category_display()}",
              f"Hello {record.submitted_by_name or ''},\n\n{body}\n\n"
              f"Reply or follow the ticket here: {_ticket_url(record)}\n\n"
              f"— The {_platform_name()} team\n",
              [record.submitted_by_email])
    return message_row(message)


def platform_file(actor, ticket_id, which, message_id=None):
    _require(actor)
    with no_tenant():
        record = _platform_ticket(ticket_id)
        if which == "message":
            message = record.messages.filter(pk=message_id).first()
            return message.attachment if message else None
        return _file_for(record, which)


def agents(actor):
    """Every platform agent, with their teams and how many open tickets."""
    from . import desk
    return desk.agents(actor)


# --- product updates & status notices (console) ----------------------------
class ProductUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=160)
    summary = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    body = serializers.CharField(required=False, allow_blank=True, default="")
    category = serializers.ChoiceField(choices=ProductUpdate.Category.choices,
                                       required=False, default=ProductUpdate.Category.NEW)
    link = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    publish = serializers.BooleanField(required=False, default=False)

    def validate_link(self, value):
        value = value.strip()
        if value and not value.startswith("/"):
            raise ValidationError("Use a path inside the product, starting with /.")
        return value


def save_update(actor, data, update_id=None):
    _require(actor)
    with no_tenant():
        update = (ProductUpdate.objects.filter(pk=update_id).first() if update_id
                  else ProductUpdate(created_by=actor))
        if update is None:
            raise Http404
        for field in ("title", "summary", "body", "category", "link"):
            if field in data:
                setattr(update, field, data[field])
        if data.get("publish") and update.published_at is None:
            update.published_at = timezone.now()
        elif "publish" in data and not data["publish"]:
            update.published_at = None
        update.save()
        return update_row(update)


class StatusNoticeSerializer(serializers.Serializer):
    component = serializers.ChoiceField(choices=StatusNotice.Component.choices)
    level = serializers.ChoiceField(choices=StatusNotice.Level.choices)
    message = serializers.CharField(max_length=500)


def notice_row(n):
    return {"id": str(n.id), "component": n.component,
            "component_display": n.get_component_display(), "level": n.level,
            "message": n.message, "starts_at": n.starts_at, "resolved_at": n.resolved_at}


def _forget_status():
    from django.core.cache import cache
    cache.delete("support:system-status")


# ===========================================================================
# Customer Success 2.0: the in-app assistant, deflection, badges
# ===========================================================================
_STOP = frozenset("""a an and are but can cant could did does doesnt dont for from get got have
how i im in is it its my no not of on or our please so that the then there this to
was we what when where which why will with work working you your able any some""".split())

# Which status component a ticket category is most likely waiting on.
CATEGORY_COMPONENT = {
    C.ATTENDANCE: ("attendance",), C.DOMAIN: ("domains",), C.BILLING: ("payments",),
    C.LOGIN: ("platform", "email"), C.TECHNICAL: ("platform", "storage"), C.BUG: ("platform",),
}


def _tokens(text):
    import re
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) >= 3 and w not in _STOP}


def assist(user, query, category=""):
    """Before a ticket is sent: similar tickets (with their answers) and any
    known issue the platform has already posted. KB articles are matched in
    the browser, where the Knowledge Base lives.

    Deterministic retrieval, not a language model: it never invents an
    answer, only points at ones that already exist. See the report for the
    LLM layer this is designed to sit underneath.
    """
    from . import desk

    words = _tokens(query)
    with no_tenant():
        solutions = desk.match_issues(words, category)
        notices = list(StatusNotice.objects.filter(resolved_at__isnull=True,
                                                   starts_at__lte=timezone.now()))
        components = CATEGORY_COMPONENT.get(category, ())
        known = [{"component": n.get_component_display(), "level": n.level,
                  "message": n.message}
                 for n in notices if n.component in components or n.level == "outage"]
        similar = []
        if words:
            qs = (SupportRequest.objects
                  .filter(organization_id=user.organization_id,
                          created_at__gte=timezone.now() - timedelta(days=365))
                  .exclude(kind=K.FEEDBACK))
            if not _can_see_organization(user):
                qs = qs.filter(submitted_by_email=user.email)
            for t in qs.order_by("-created_at")[:300]:
                theirs = _tokens(f"{t.subject} {t.message}")
                if not theirs:
                    continue
                overlap = len(words & theirs) / len(words)
                if category and t.category == category:
                    overlap += 0.15
                if overlap >= 0.4:
                    similar.append((overlap, t))
            similar.sort(key=lambda x: -x[0])
        results = []
        for score, t in similar[:3]:
            answer = (t.messages.filter(is_internal=False, author_kind=SupportMessage.AuthorKind.STAFF)
                      .order_by("-created_at").values_list("body", flat=True).first())
            results.append({"id": str(t.id), "reference": t.reference, "subject": t.subject,
                            "status": t.status, "status_display": t.get_status_display(),
                            "is_open": t.status in SupportRequest.OPEN_STATES,
                            "answer": (answer or "")[:400] if t.status in (S.RESOLVED, S.CLOSED) else "",
                            "score": round(min(score, 1.0), 2)})
    # The single best thing to try first: a fix the team has written up for
    # everyone, else what fixed this customer's own similar ticket. The KB
    # article candidate is ranked in the browser and wins only if neither.
    best = None
    if solutions:
        s0 = solutions[0]
        best = {"source": "known_issue", "id": s0["id"], "title": s0["title"],
                "text": s0["workaround"], "score": s0["score"]}
    answered = [r for r in results if r["answer"]]
    if answered and (best is None or answered[0]["score"] > best["score"] + 0.2):
        a0 = answered[0]
        best = {"source": "ticket", "id": a0["id"], "title": f"{a0['reference']}: {a0['subject']}",
                "text": a0["answer"], "score": a0["score"]}
    return {"similar_tickets": results, "known_issues": known,
            "known_solutions": solutions, "possible_solution": best}


class SupportAssistView(APIView):
    """POST {query, category} -> suggestions before a ticket is created.
    POST {shown: true} records that suggestions were shown (once per draft);
    POST {deflected: true, source} records that a suggestion answered it.
    Deflection rate = deflected / shown."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support"

    def post(self, request):
        if request.data.get("deflected") or request.data.get("shown"):
            from . import desk
            from .adoption import _count
            from .models import PlatformMetric

            if request.data.get("deflected"):
                _count(PlatformMetric.Key.TICKET_DEFLECTED, request.user.organization_id)
                desk.record_deflection(request.data.get("source"))
            else:
                _count(PlatformMetric.Key.ASSIST_SHOWN, request.user.organization_id)
            return Response({"recorded": True})
        query = str(request.data.get("query") or "")[:600]
        category = str(request.data.get("category") or "")
        return Response(assist(request.user, query, category if category in C.values else ""))


class SupportBadgesView(APIView):
    """GET the two numbers the Help & Support rail shows as badges."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        with no_tenant():
            unread = (SupportRequest.objects
                      .filter(organization_id=user.organization_id, submitted_by_email=user.email,
                              unread_by_customer=True)
                      .exclude(kind=K.FEEDBACK).count())
            published = list(ProductUpdate.objects.filter(
                published_at__isnull=False, published_at__lte=timezone.now())
                .values_list("published_at", flat=True)[:50])
        seen = _seen_at(user)
        return Response({"tickets_unread": unread,
                         "updates_unseen": sum(1 for p in published if seen is None or p > seen)})
