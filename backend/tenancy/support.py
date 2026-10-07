"""Support Center and feedback: the customer talking to the platform team.

Inside the product, because a customer who has to find an email address, or
leave the page they are stuck on to describe it, often doesn't bother -- and
the platform never hears about the problem until they cancel.

Every request records the page it was sent from, so "it doesn't work" arrives
with "on /leave/apply" attached. Ratings ("How was your experience?", "Rate
this feature") use the same table with kind=feedback, so product feedback
and support live in one inbox rather than two nobody reads.
"""
import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .context import no_tenant
from .models import SupportRequest

logger = logging.getLogger(__name__)
K = SupportRequest.Kind


class SupportRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=K.choices)
    subject = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    message = serializers.CharField(max_length=5000, required=False, allow_blank=True, default="")
    rating = serializers.IntegerField(min_value=1, max_value=5, required=False, allow_null=True, default=None)
    feature = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    page = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")

    def validate(self, data):
        if data["kind"] == K.FEEDBACK:
            if data.get("rating") is None:
                raise ValidationError({"rating": "Choose a rating from 1 to 5."})
        elif not (data.get("message") or "").strip():
            raise ValidationError({"message": "Tell us a little about it — a sentence is enough."})
        return data


def row(r, *, for_platform=False):
    out = {
        "id": str(r.id), "kind": r.kind, "kind_display": r.get_kind_display(),
        "subject": r.subject, "message": r.message, "rating": r.rating,
        "feature": r.feature, "page": r.page, "status": r.status,
        "status_display": r.get_status_display(), "response": r.response,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }
    if for_platform:
        out.update({
            "organization_name": getattr(r.organization, "name", ""),
            "organization_slug": getattr(r.organization, "slug", ""),
            "submitted_by_email": r.submitted_by_email,
            "submitted_by_name": r.submitted_by_name,
            "submitted_by_role": r.submitted_by_role,
            "user_agent": r.user_agent,
        })
    return out


def _mail(subject, body, to):
    from django.core.mail import send_mail

    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, to, fail_silently=False)
        return True
    except Exception:                              # noqa: BLE001
        logger.warning("support email failed: %s", subject, exc_info=True)
        return False


def submit(user, data, request=None):
    """Record a request from a signed-in person. Returns the row."""
    meta = getattr(request, "META", {}) or {}
    with no_tenant():
        record = SupportRequest.objects.create(
            organization_id=getattr(user, "organization_id", None),
            kind=data["kind"], subject=(data.get("subject") or "").strip()[:200],
            message=(data.get("message") or "").strip(),
            rating=data.get("rating"), feature=(data.get("feature") or "").strip()[:80],
            page=(data.get("page") or "").strip()[:300],
            user_agent=(meta.get("HTTP_USER_AGENT") or "")[:300],
            submitted_by_email=getattr(user, "email", "") or "",
            submitted_by_name=(user.get_full_name() or user.username)[:150],
            submitted_by_role=getattr(user, "role", "") or "",
        )
    support = getattr(settings, "PLATFORM_SUPPORT_EMAIL", "")
    if support and record.kind != K.FEEDBACK:
        org = getattr(user, "organization", None)
        _mail(f"[{record.get_kind_display()}] {record.subject or record.message[:60]}"
              f" — {getattr(org, 'name', 'platform')}",
              f"From: {record.submitted_by_name} <{record.submitted_by_email}>\n"
              f"Organization: {getattr(org, 'name', '—')}\n"
              f"Page: {record.page or '—'}\n\n{record.message}\n",
              [support])
    return record


class SupportRequestsView(APIView):
    """GET your own requests; POST a new one. Any signed-in person."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "support"

    def get(self, request):
        with no_tenant():
            rows = (SupportRequest.objects
                    .filter(submitted_by_email=request.user.email,
                            organization_id=request.user.organization_id)
                    .exclude(kind=K.FEEDBACK)[:50])
            return Response([row(r) for r in rows])

    def post(self, request):
        serializer = SupportRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = submit(request.user, serializer.validated_data, request)
        detail = ("Thanks — your rating helps us decide what to improve."
                  if record.kind == K.FEEDBACK else
                  "Thanks — we’ve got it. You’ll hear back by email, and you "
                  "can see its status under Help → Your requests.")
        return Response({**row(record), "detail": detail}, status=status.HTTP_201_CREATED)


def platform_list(actor, *, kind=None, state=None):
    from .console import require_platform

    require_platform(actor)
    with no_tenant():
        rows = SupportRequest.objects.select_related("organization")
        if kind:
            rows = rows.filter(kind=kind)
        if state:
            rows = rows.filter(status=state)
        rows = list(rows[:200])
        ratings = [r.rating for r in SupportRequest.objects.filter(
            kind=K.FEEDBACK, rating__isnull=False,
            created_at__gte=timezone.now() - timezone.timedelta(days=30))]
    return {
        "requests": [row(r, for_platform=True) for r in rows],
        "feedback_30d": {
            "count": len(ratings),
            "average": round(sum(ratings) / len(ratings), 2) if ratings else None,
        },
    }


def platform_update(actor, request_id, *, state=None, response=None, request=None):
    """Move a request on, and reply. A reply is emailed to whoever sent it."""
    from .console import require_platform
    from .exceptions import TenancyError

    require_platform(actor)
    with no_tenant():
        try:
            record = SupportRequest.objects.select_related("organization").get(pk=request_id)
        except (SupportRequest.DoesNotExist, ValueError):
            raise TenancyError("That request does not exist.")
        if state:
            if state not in SupportRequest.Status.values:
                raise TenancyError("Unknown status.")
            record.status = state
        replied = response is not None and response.strip() and response.strip() != record.response
        if response is not None:
            record.response = response.strip()
        record.save()
    if replied and record.submitted_by_email:
        _mail(f"Re: {record.subject or record.get_kind_display()}",
              f"Hello {record.submitted_by_name or ''},\n\n{record.response}\n\n"
              f"— The {getattr(settings, 'PLATFORM_NAME', 'Buddhi Labs')} team\n",
              [record.submitted_by_email])
    return row(record, for_platform=True)
