"""
Phase RELEASE — appraisal notifications.

The module could run a complete ten-stage cycle and tell nobody. These pin both
halves of the fix: that the right person is told at the right moment, and that
nobody else is — including, deliberately, HR.

The second half is the one that decays. A notification nobody wants is removed
by a filter rule, and the filter takes the wanted ones with it.
"""
import pytest

from appraisal import workflow
from appraisal.models import Appraisal, TrainingPlan
from notifications.models import Category, Notification
from .conftest import APPRAISALS

pytestmark = pytest.mark.django_db

Status = Appraisal.Status


def sent_to(user, category=None):
    rows = Notification.objects.filter(recipient=user)
    if category:
        rows = rows.filter(category=category)
    return list(rows)


def only_recipients(category):
    return {n.recipient_id
            for n in Notification.objects.filter(category=category)}


# ---------------------------------------------------------------------------
# The seam itself
# ---------------------------------------------------------------------------
def test_the_receivers_are_connected_once_and_only_once():
    """
    Connected twice, every appraisal notification is delivered twice — a bug
    invisible in development and obvious to everybody else. `dispatch_uid`
    makes a second connect a no-op rather than a second delivery.
    """
    from appraisal import events, receivers

    receivers.connect()
    receivers.connect()
    assert len(events.GOALS_APPROVED.receivers) == 1


def test_a_failing_receiver_cannot_roll_back_a_transition(cast, cycle,
                                                          make_appraisal):
    """
    The work happened and the timeline is the record of it; the bell is a
    convenience. This is the opposite of the rule for the audit trail, which IS
    allowed to fail a transition.
    """
    from appraisal import events

    def explode(**kwargs):
        raise RuntimeError("the notification backend is down")

    events.GOALS_APPROVED.connect(explode, dispatch_uid="test.explode")
    try:
        appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                                   status=Status.GOAL_APPROVAL)
        workflow.agree_goals(appraisal, cast["supervisor"])
        appraisal.refresh_from_db()
        assert appraisal.status == Status.MID_YEAR
    finally:
        events.GOALS_APPROVED.disconnect(dispatch_uid="test.explode")


# ---------------------------------------------------------------------------
# Who is told, at each moment
# ---------------------------------------------------------------------------
def test_the_employee_learns_their_appraisal_has_opened(cast, cycle,
                                                        make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"], actor=cast["hr"])
    assert sent_to(cast["employee"], Category.APPRAISAL_OPENED)


def test_opening_the_same_appraisal_twice_tells_them_once(cast, cycle,
                                                          make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               actor=cast["hr"])
    workflow.record_creation(appraisal, cast["hr"])
    assert len(sent_to(cast["employee"], Category.APPRAISAL_OPENED)) == 1


def test_submitting_goals_tells_the_supervisor_and_not_the_employee(
        cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"])
    Notification.objects.all().delete()
    workflow.submit_goals(appraisal, cast["employee"])

    assert only_recipients(Category.APPRAISAL_GOALS_SUBMITTED) == {
        cast["supervisor"].id}


def test_approving_goals_tells_the_employee_and_not_the_supervisor(
        cast, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.GOAL_APPROVAL)
    Notification.objects.all().delete()
    workflow.agree_goals(appraisal, cast["supervisor"])

    assert only_recipients(Category.APPRAISAL_GOALS_APPROVED) == {
        cast["employee"].id}


def test_the_self_assessment_notice_names_the_deadline(cast, cycle,
                                                       make_appraisal):
    """
    The one notification this module most needed. A date is what turns a
    reminder into something somebody schedules rather than something they mean
    to get to.
    """
    import datetime
    from django.utils import timezone

    cycle.self_assessment_deadline = timezone.localdate() + datetime.timedelta(
        days=21)
    cycle.save(update_fields=["self_assessment_deadline"])

    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    Notification.objects.all().delete()
    workflow.record_mid_year(appraisal, cast["supervisor"])

    rows = sent_to(cast["employee"], Category.APPRAISAL_SELF_ASSESSMENT_DUE)
    assert len(rows) == 1
    assert str(cycle.self_assessment_deadline) in rows[0].body


def test_the_notice_still_reads_properly_with_no_deadline_set(cast, cycle,
                                                              make_appraisal):
    """
    Deadlines are optional on a cycle. The sentence must not end up with a
    dangling "It is due by ." — which is how an optional field becomes a
    visible defect.
    """
    assert cycle.self_assessment_deadline is None
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    Notification.objects.all().delete()
    workflow.record_mid_year(appraisal, cast["supervisor"])

    body = sent_to(cast["employee"],
                   Category.APPRAISAL_SELF_ASSESSMENT_DUE)[0].body
    assert "due by" not in body
    assert body.endswith(".")


def test_submitting_a_self_assessment_tells_the_supervisor(cast, cycle,
                                                           make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.SELF_ASSESSMENT)
    Notification.objects.all().delete()
    appraisal.self_assessment = "My account of the year, in detail."
    appraisal.save(update_fields=["self_assessment"])
    workflow.submit_self_assessment(appraisal, cast["employee"])

    assert only_recipients(Category.APPRAISAL_REVIEW_PENDING) == {
        cast["supervisor"].id}


def test_the_committee_is_told_only_when_it_is_their_turn(cast, cycle,
                                                          make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               [cast["committee"]],
                               status=Status.SUPERVISOR_REVIEW)
    Notification.objects.all().delete()
    appraisal.supervisor_comments = "A solid year; specifics recorded."
    appraisal.save(update_fields=["supervisor_comments"])
    workflow.record_supervisor_review(appraisal, cast["supervisor"])

    assert only_recipients(Category.APPRAISAL_COMMITTEE_REVIEW) == {
        cast["committee"].id}


def test_the_employee_is_told_when_their_feedback_is_readable(cast, cycle,
                                                              make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.FINAL_REVIEW)
    Notification.objects.all().delete()
    appraisal.final_summary = "Objectives met in full."
    appraisal.save(update_fields=["final_summary"])
    workflow.record_final_review(appraisal, cast["supervisor"])

    assert only_recipients(Category.APPRAISAL_FEEDBACK_READY) == {
        cast["employee"].id}


def test_closing_tells_the_employee_the_cycle_is_done(cast, cycle,
                                                      make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    Notification.objects.all().delete()
    workflow.agree_training_plan(appraisal, cast["supervisor"])

    assert only_recipients(Category.APPRAISAL_CLOSED) == {cast["employee"].id}


# ---------------------------------------------------------------------------
# Returns carry the reason
# ---------------------------------------------------------------------------
def test_a_return_carries_the_reviewers_reason_verbatim(cast, cycle,
                                                        make_appraisal):
    """
    That reason is the only part the person receiving it back will read.
    Paraphrasing it would lose the specifics they need to act on.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    Notification.objects.all().delete()
    reason = "The second objective needs a measurable target before I agree it."
    workflow.return_to(appraisal, cast["supervisor"], Status.GOAL_SETTING,
                       reason)

    rows = sent_to(cast["employee"], Category.APPRAISAL_RETURNED)
    assert len(rows) == 1
    assert rows[0].body == reason


def test_a_return_reaches_whoever_now_owns_the_stage(cast, cycle,
                                                     make_appraisal):
    """Returned to a SUPERVISOR stage, it is the supervisor who must act — not
    the employee, who would have nothing to do with it."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               [cast["committee"]], status=Status.COMMITTEE)
    Notification.objects.all().delete()
    workflow.return_to(appraisal, cast["committee"],
                       Status.SUPERVISOR_REVIEW,
                       "Please expand on the delivery competency.")

    assert only_recipients(Category.APPRAISAL_RETURNED) == {
        cast["supervisor"].id}


def test_two_returns_are_two_messages(cast, cycle, make_appraisal):
    """A second return is a genuinely new thing to read, so the idempotency key
    must not collapse it into the first."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.MID_YEAR)
    Notification.objects.all().delete()
    workflow.return_to(appraisal, cast["supervisor"], Status.GOAL_SETTING,
                       "First reason, with enough detail.")
    workflow.submit_goals(appraisal, cast["employee"])
    workflow.agree_goals(appraisal, cast["supervisor"])
    workflow.return_to(appraisal, cast["supervisor"], Status.GOAL_SETTING,
                       "Second reason, also detailed.")

    assert len(sent_to(cast["employee"], Category.APPRAISAL_RETURNED)) == 2


# ---------------------------------------------------------------------------
# Training decisions
# ---------------------------------------------------------------------------
def test_the_employee_hears_what_happened_to_their_training_request(
        cast, auth, cycle, make_appraisal):
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.TRAINING_PLAN)
    row = TrainingPlan.objects.create(
        appraisal=appraisal, title="Advanced Excel",
        kind=TrainingPlan.Kind.TRAINING)
    Notification.objects.all().delete()

    auth(cast["hr"]).post(
        f"{APPRAISALS}{appraisal.id}/training-plan/{row.id}/decide/",
        {"status": "declined", "decision_note": "No budget this quarter."},
        format="json")

    rows = sent_to(cast["employee"], Category.APPRAISAL_TRAINING_DECIDED)
    assert len(rows) == 1
    # A declined request nobody hears about is how people stop asking.
    assert "Declined" in rows[0].body
    assert "No budget this quarter." in rows[0].body


# ---------------------------------------------------------------------------
# Who is NOT told
# ---------------------------------------------------------------------------
def test_hr_is_never_told_about_an_individual_appraisal_moving(
        cast, cycle, make_appraisal):
    """
    HR runs a hundred of these. A notification per stage per person is a hundred
    a week, and the predictable result is a filter rule that hides the category
    — including the ones HR does need. HR's view is the cycle dashboard, which
    is a pull, not a push.
    """
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               [cast["committee"]], status=Status.CLOSED)
    assert sent_to(cast["hr"]) == []
    assert sent_to(cast["admin"]) == []


def test_an_outsider_is_never_told_anything(cast, cycle, make_appraisal):
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   [cast["committee"]], status=Status.CLOSED)
    assert sent_to(cast["outsider"]) == []


def test_nobody_is_told_about_their_own_action(cast, cycle, make_appraisal):
    """The actor already knows: they just did it."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.GOAL_APPROVAL)
    Notification.objects.all().delete()
    # HR approving on the supervisor's behalf still tells the employee, and
    # still does not tell HR.
    workflow.agree_goals(appraisal, cast["hr"])
    assert only_recipients(Category.APPRAISAL_GOALS_APPROVED) == {
        cast["employee"].id}


def test_every_category_used_here_is_a_declared_choice():
    """A category that is not a choice is a row the preferences screen cannot
    show and the user can therefore never turn off."""
    from appraisal import receivers
    import inspect

    source = inspect.getsource(receivers)
    declared = {c.value for c in Category}
    used = {line.split("Category.")[1].split(",")[0].split(")")[0].strip()
            for line in source.splitlines() if "Category." in line}
    for name in used:
        assert getattr(Category, name).value in declared, name


def test_a_deactivated_account_is_not_notified(cast, cycle, make_appraisal):
    """Somebody who has left should not accumulate a queue nobody reads."""
    appraisal = make_appraisal(cycle, cast["employee"], cast["supervisor"],
                               status=Status.GOAL_APPROVAL)
    cast["employee"].is_active = False
    cast["employee"].save(update_fields=["is_active"])
    Notification.objects.all().delete()

    workflow.agree_goals(appraisal, cast["supervisor"])
    assert sent_to(cast["employee"], Category.APPRAISAL_GOALS_APPROVED) == []


# ---------------------------------------------------------------------------
# Deadline reminders
# ---------------------------------------------------------------------------
def _shift(cycle, field, days):
    import datetime
    from django.utils import timezone
    setattr(cycle, field, timezone.localdate() + datetime.timedelta(days=days))
    cycle.save(update_fields=[field])


@pytest.mark.parametrize("days", [7, 3, 0])
def test_a_deadline_is_nudged_three_times_and_no_more(cast, cycle,
                                                      make_appraisal, days):
    """
    Seven days out, three days out, and on the day. A reminder that arrives
    every morning is one people filter, and the filter catches the last one too.
    """
    from appraisal import reminders

    _shift(cycle, "self_assessment_deadline", days)
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.SELF_ASSESSMENT)
    Notification.objects.all().delete()

    assert reminders.run_deadline_reminders() == 1
    rows = sent_to(cast["employee"], Category.APPRAISAL_DEADLINE_REMINDER)
    assert len(rows) == 1
    assert str(cycle.self_assessment_deadline) in rows[0].body


@pytest.mark.parametrize("days", [10, 5, 1, -1, -30])
def test_no_nudge_on_any_other_day(cast, cycle, make_appraisal, days):
    """Including AFTER the deadline: lateness is visible on HR's dashboard,
    where a human can decide whether it matters. It is not a daily chase."""
    from appraisal import reminders

    _shift(cycle, "self_assessment_deadline", days)
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.SELF_ASSESSMENT)
    Notification.objects.all().delete()
    assert reminders.run_deadline_reminders() == 0


def test_running_twice_in_one_day_sends_nothing_twice(cast, cycle,
                                                      make_appraisal):
    from appraisal import reminders

    _shift(cycle, "self_assessment_deadline", 3)
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.SELF_ASSESSMENT)
    Notification.objects.all().delete()

    reminders.run_deadline_reminders()
    reminders.run_deadline_reminders()
    assert len(sent_to(cast["employee"],
                       Category.APPRAISAL_DEADLINE_REMINDER)) == 1


def test_a_cycle_with_no_deadline_invents_one_for_nobody(cast, cycle,
                                                         make_appraisal):
    """Deadlines are optional and stay optional. No date, no reminder."""
    from appraisal import reminders

    assert cycle.self_assessment_deadline is None
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.SELF_ASSESSMENT)
    assert reminders.run_deadline_reminders() == 0


def test_the_nudge_goes_to_whoever_owns_the_stage(cast, cycle,
                                                  make_appraisal):
    """At supervisor review it is the supervisor who is late, not the
    employee — and chasing the wrong person is worse than chasing nobody."""
    from appraisal import reminders

    _shift(cycle, "review_deadline", 3)
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.SUPERVISOR_REVIEW)
    Notification.objects.all().delete()

    reminders.run_deadline_reminders()
    assert only_recipients(Category.APPRAISAL_DEADLINE_REMINDER) == {
        cast["supervisor"].id}
    body = sent_to(cast["supervisor"])[0].body
    assert cast["employee"].get_full_name() in body


def test_a_closed_cycle_chases_nobody(cast, cycle, make_appraisal):
    from appraisal.models import AppraisalCycle
    from appraisal import reminders

    _shift(cycle, "self_assessment_deadline", 3)
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.SELF_ASSESSMENT)
    cycle.status = AppraisalCycle.Status.CLOSED
    cycle.save(update_fields=["status"])
    Notification.objects.all().delete()

    assert reminders.run_deadline_reminders() == 0


def test_the_reminder_command_records_a_heartbeat(cast, cycle,
                                                  make_appraisal):
    """
    A job that silently stops running must become a red tile and an alert. This
    job's failure mode is silence, which is exactly the mode nobody reports.
    """
    from django.core.management import call_command
    from monitoring import heartbeat

    _shift(cycle, "self_assessment_deadline", 3)
    make_appraisal(cycle, cast["employee"], cast["supervisor"],
                   status=Status.SELF_ASSESSMENT)
    call_command("send_appraisal_reminders", verbosity=0)

    timestamp, ok = heartbeat.last_run("APPRAISAL_REMINDERS")
    assert timestamp is not None
    assert ok
