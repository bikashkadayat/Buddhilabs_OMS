"""Phase S8 Part 8: telling a customer before their access ends.

THE FAILURE THIS PREVENTS. Everything needed to suspend a tenant has existed
since S6: `advance_expired` moves a lapsed subscription into its grace period
and then to suspended, on a schedule, correctly. What did not exist was
anybody telling the customer. The first they would know is an entire office
unable to sign in on a Monday morning -- over an invoice nobody had mentioned.

FIVE THRESHOLDS, ONE NOTICE EACH, and the "each" is load-bearing. A nightly
job that notifies whenever `days_remaining <= 30` sends thirty emails about
one renewal, which teaches people to filter them -- and the one that matters,
the last one, is filtered with the rest. So each threshold fires once per
subscription PERIOD, keyed on the period's end date: a renewal moves the
date, which arms the whole ladder again for the new period.

WHO IS TOLD, AND WHY IT IS NOT EVERYBODY. Only the organization's
administrators. An employee cannot pay the bill, cannot see the subscription
page, and has no action to take -- so a notice to them is an alarm with no
button, which is worse than silence.

IN-APP AND EMAIL BOTH, through `notifications.dispatcher.notify`, which
already honours each recipient's preferences and dedupes on an idempotency
key. This module decides WHEN and WHAT; it does not re-implement delivery.
"""
import logging

from django.utils import timezone

logger = logging.getLogger(__name__)

# Days before expiry at which to speak, descending. The brief names these
# four; the terminal states below are separate because they are not
# countdowns, they are events.
THRESHOLDS = (30, 15, 7, 3)


def _notify_admins(organization, *, key, title, body, action_url):
    """Send one notice to every administrator of one organization.

    Returns the number of people told. Runs inside the tenant's context
    because `Notification` is tenant data and the recipients are its users --
    this is the one place in the platform's own billing machinery that writes
    into a customer's workspace, and it does so as that customer.
    """
    from django.contrib.auth import get_user_model

    from notifications import dispatcher
    from notifications.models import Category

    from .context import tenant_context

    User = get_user_model()
    sent = 0
    with tenant_context(organization):
        admins = list(User.objects.filter(role="admin", is_active=True))
        for admin in admins:
            try:
                dispatcher.notify(
                    admin, Category.SYSTEM_ANNOUNCEMENT, title, body,
                    action_url=action_url,
                    # One per administrator per threshold per period. A
                    # second run of the job finds the row and sends nothing.
                    idempotency_key=f"{key}:{admin.pk}")
                sent += 1
            except Exception:                      # noqa: BLE001
                # A failed notice must not stop the sweep: the next tenant's
                # administrators have done nothing wrong.
                logger.warning("expiry notice %s failed for user %s", key,
                               admin.pk, exc_info=True)
    if not sent:
        logger.warning("organization %s has no active administrator to warn "
                       "about %s", organization.slug, key)
    return sent


def _money(subscription):
    price = getattr(subscription, "plan_price", None)
    if price is None or price.amount_minor is None:
        return ""
    return f" ({price.currency} {price.amount_minor / 100:,.2f})"


def run(*, today=None, dry_run=False):
    """Send every notice that is due today. Idempotent.

    :returns: ``{"renewal_due": n, "expired": n, "in_grace": n,
                 "suspended": n, "notices": [...]}``
    """
    from .models import Organization, Subscription

    today = today or timezone.localdate()
    S = Subscription.Status
    result = {"renewal_due": 0, "expired": 0, "in_grace": 0, "suspended": 0,
              "notices": []}

    subscriptions = (Subscription.objects
                     .select_related("organization", "plan", "plan_price")
                     .exclude(status__in=[S.CANCELLED]))

    for subscription in subscriptions:
        organization = subscription.organization
        # An archived workspace generates no activity, including this. Its
        # administrators cannot sign in to act on a notice, and preserving a
        # tenant is not the moment to email them about renewal.
        if organization.status == Organization.Status.ARCHIVED:
            continue

        period_end = subscription.current_period_end
        plan = getattr(subscription.plan, "name", "your plan")

        # --- the countdown ------------------------------------------------
        if period_end and subscription.status in (S.ACTIVE, S.TRIAL):
            days = (period_end - today).days
            # A THRESHOLD HAS TO FIT INSIDE THE PERIOD (Phase S11 Part 7).
            #
            # A monthly period is 30 or 31 days, so `days == 30` fired on the
            # first or second day of it: a monthly customer was emailed "Your
            # subscription ends in 30 days" the morning after they renewed.
            # Alarming, useless, and it trains people to ignore the notice
            # that matters at 3 days.
            #
            # `period_days` is the length of THIS period rather than the
            # plan's nominal interval, so an extension granted by an operator
            # widens the window it deserves instead of being measured against
            # the plan.
            period_start = subscription.current_period_start
            period_days = ((period_end - period_start).days
                           if period_start else None)
            for threshold in THRESHOLDS:
                if days != threshold:
                    continue
                if period_days is not None and threshold >= period_days:
                    continue
                key = f"subscription:{subscription.pk}:{period_end}:d{threshold}"
                noun = ("trial" if subscription.status == S.TRIAL
                        else "subscription")
                title = (f"Your {noun} ends in {threshold} day"
                         f"{'s' if threshold != 1 else ''}")
                body = (
                    f"{organization.name}'s {noun} on {plan} ends on "
                    f"{period_end:%d %B %Y}{_money(subscription)}. Open "
                    f"Settings → Subscription to choose a plan and send "
                    f"payment. Nothing is interrupted before that date.")
                result["notices"].append(key)
                result["renewal_due"] += 1
                if not dry_run:
                    _notify_admins(organization, key=key, title=title,
                                   body=body,
                                   action_url="/settings/subscription")

        # --- the terminal states ------------------------------------------
        if subscription.status == S.GRACE:
            key = f"subscription:{subscription.pk}:{period_end}:grace"
            result["notices"].append(key)
            result["in_grace"] += 1
            if not dry_run:
                until = subscription.grace_until
                _notify_admins(
                    organization, key=key,
                    title="Your subscription has lapsed",
                    body=(
                        f"{organization.name}'s subscription expired on "
                        f"{period_end:%d %B %Y}. Everything still works "
                        + (f"until {until:%d %B %Y}" if until else "for now")
                        + ", after which sign-in will be suspended until "
                          "payment is confirmed. Nothing has been deleted."),
                    action_url="/settings/subscription")

        elif subscription.status == S.SUSPENDED:
            key = f"subscription:{subscription.pk}:{period_end}:suspended"
            result["notices"].append(key)
            result["suspended"] += 1
            # NOT SENT IN-APP TO A SUSPENDED TENANT, because nobody there can
            # sign in to read it. `notify` writes the row anyway -- it will
            # be waiting when access is restored -- and the EMAIL is the part
            # that reaches them today.
            if not dry_run:
                _notify_admins(
                    organization, key=key,
                    title="Sign-in is suspended for your workspace",
                    body=(
                        f"{organization.name}'s subscription was not renewed "
                        f"and sign-in is now suspended. Your data is intact "
                        f"and nothing has been deleted. Send payment or "
                        f"contact support to restore access."),
                    action_url="/settings/subscription")

        elif subscription.status == S.EXPIRED:
            key = f"subscription:{subscription.pk}:{period_end}:expired"
            result["notices"].append(key)
            result["expired"] += 1
            if not dry_run:
                _notify_admins(
                    organization, key=key,
                    title="Your subscription has expired",
                    body=(
                        f"{organization.name}'s subscription expired on "
                        f"{period_end:%d %B %Y}. Send payment to restore "
                        f"access -- nothing has been deleted."),
                    action_url="/settings/subscription")

    logger.info("expiry notices: %s", {k: v for k, v in result.items()
                                       if k != "notices"})
    return result
