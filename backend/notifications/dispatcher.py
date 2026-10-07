"""
Central notification dispatcher.

    notify(user, category, title, body, action_url, idempotency_key=None)

Respects the recipient's NotificationPreference (in-app + email, per category),
creates the in-app record, and enqueues email delivery. An idempotency key
collapses duplicate notifications for the same recipient.
"""
from . import emails
from .models import Category, Notification, NotificationPreference


def get_preference(user, category):
    """The user's preference for a category, or their ORGANIZATION's default.

    Three tiers, most specific first:

      1. the user's own ``NotificationPreference`` row, if they have set one;
      2. their organization's defaults (Phase S6 Part 4), from
         ``OrganizationSettings.notify_*_default``;
      3. the platform default -- everything on except the opt-in weekly
         digest.

    Tier 2 is new. Before it, the platform default was the only answer, so
    "should new employees get email notifications" was a decision no customer
    could make and every customer inherited. A tenant whose settings are NULL
    (which is every tenant that existed before this phase, NIF included) falls
    straight through to tier 3 and behaves exactly as it did.

    Returns an UNSAVED instance in tiers 2 and 3. Deliberate: writing a
    preference row the user never chose would turn a default into a decision,
    and the user's own screen could then never show them "default" again.
    """
    pref = NotificationPreference.objects.filter(user=user, category=category).first()
    if pref:
        return pref

    in_app_default = True
    email_default = category != Category.WEEKLY_DIGEST
    defaults = _organization_defaults(user)
    if defaults:
        in_app, email, digest = defaults
        if in_app is not None:
            in_app_default = in_app
        if category == Category.WEEKLY_DIGEST:
            if digest is not None:
                email_default = digest
        elif email is not None:
            email_default = email

    return NotificationPreference(user=user, category=category,
                                  in_app_enabled=in_app_default,
                                  email_enabled=email_default)


def _organization_defaults(user):
    """``(in_app, email, digest)`` for the user's tenant, or None.

    One cheap indexed read on a one-row-per-tenant table, and it is wrapped
    because this runs on the notification path: a user with no organization
    (a platform operator) or a tenant with no settings row must not turn a
    leave approval into a 500.
    """
    organization_id = getattr(user, "organization_id", None)
    if not organization_id:
        return None
    try:
        from tenancy.models import OrganizationSettings

        row = (OrganizationSettings.objects
               .filter(organization_id=organization_id)
               .values_list("notify_in_app_default", "notify_email_default",
                            "notify_digest_default")
               .first())
    except Exception:  # noqa: BLE001 - tenancy unmigrated, or no row
        return None
    return row


def notify(user, category, title, body="", action_url="", idempotency_key=None,
           email_context=None, object_id=""):
    """
    email_context: optional dict merged into the HTML email template context
    (e.g. structured leave detail: status badge, BS+AD dates, days, remarks).
    object_id: source-object reference (e.g. leave UUID) recorded in NotificationLog.
    """
    if user is None or not getattr(user, "is_active", True):
        return None

    pref = get_preference(user, category)
    notif = None

    if pref.in_app_enabled:
        if idempotency_key:
            notif, created = Notification.objects.get_or_create(
                recipient=user, idempotency_key=idempotency_key,
                defaults={"category": category, "title": title, "body": body, "action_url": action_url or ""},
            )
            if not created:
                return notif  # already delivered; do not re-send email either (dedup)
        else:
            notif = Notification.objects.create(
                recipient=user, category=category, title=title, body=body, action_url=action_url or "",
            )

    if pref.email_enabled and getattr(user, "email", ""):
        if notif is not None:
            # Optimistically flag in the main thread so the email worker never
            # needs a DB connection (avoids sqlite lock contention under tests).
            Notification.objects.filter(pk=notif.pk).update(is_email_sent=True)
        emails.dispatch_email(user, category, title, body, action_url or "",
                              extra=email_context, object_id=object_id)

    return notif


def resolve_source_notifications(prefix):
    """Mark unread 'awaiting your review' notifications for a source object as read
    once it no longer needs action (decided / deleted). `prefix` matches the
    idempotency_key, e.g. 'leave-<id>-submitted' or 'takeout-<id>-submitted'. Keeps
    the bell reconciled with the actionable pending queue."""
    from django.utils import timezone
    from .models import Notification
    return (Notification.objects
            .filter(idempotency_key__startswith=prefix, is_read=False)
            .update(is_read=True, read_at=timezone.now()))
