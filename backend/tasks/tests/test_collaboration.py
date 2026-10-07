"""
Phase T2.3 — comments, replies, mentions and edited markers.

The rules with teeth here: a reply never becomes a tree, only the author may
edit their own words, an edit is always visible, and a mention never notifies
somebody about a task they cannot open.
"""
import pytest

from notifications.models import Category, Notification
from tasks.models import Task, TaskComment

from .conftest import LIST

pytestmark = pytest.mark.django_db

Status = Task.Status


def comments_url(task):
    return f"{LIST}{task.id}/comments/"


def post(auth, user, task, body, **extra):
    return auth(user).post(comments_url(task), {"body": body, **extra},
                           format="json")


# ---------------------------------------------------------------------------
# Replies
# ---------------------------------------------------------------------------
def test_a_comment_can_be_replied_to(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    parent = post(auth, cast["employee"], task, "Draft uploaded.").data
    reply = post(auth, cast["hod"], task, "Thanks — reading it now.",
                 parent=parent["id"])

    assert reply.status_code == 201, reply.data
    assert str(reply.data["parent"]) == parent["id"]

    threads = auth(cast["employee"]).get(comments_url(task)).data
    assert len(threads) == 1                      # the reply is not a second thread
    assert len(threads[0]["replies"]) == 1
    assert threads[0]["replies"][0]["body"] == "Thanks — reading it now."


def test_a_reply_to_a_reply_is_refused(cast, auth, make_task):
    """
    One level deep, by design. Arbitrary nesting reads as a tree nobody can
    follow in a work log, and makes "the last three comments" ambiguous.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    parent = post(auth, cast["employee"], task, "Draft uploaded.").data
    reply = post(auth, cast["hod"], task, "Reading it.", parent=parent["id"]).data

    second = post(auth, cast["employee"], task, "Thanks.", parent=reply["id"])
    assert second.status_code == 400
    assert "parent" in second.data


def test_a_reply_cannot_point_at_a_comment_on_another_task(cast, auth, make_task):
    one = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    two = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    elsewhere = post(auth, cast["employee"], one, "On the first task.").data

    response = post(auth, cast["employee"], two, "Confused.",
                    parent=elsewhere["id"])
    assert response.status_code == 400


def test_a_reply_notifies_the_person_being_replied_to(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["peer"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS)
    parent = post(auth, cast["hr"], task, "A question from HR.").data
    Notification.objects.all().delete()
    post(auth, cast["hod"], task, "Answering.", parent=parent["id"])

    told = set(Notification.objects.filter(
        category=Category.TASK_COMMENTED).values_list("recipient_id", flat=True))
    # HR is neither assignee, creator nor reviewer — they are reached because
    # the reply is to them.
    assert cast["hr"].id in told


# ---------------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------------
def test_a_mention_is_stored_and_notified(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS)
    response = post(auth, cast["employee"], task,
                    f"@{cast['hod'].get_full_name()} Draft uploaded.",
                    mention_ids=[str(cast["hod"].id)])

    assert response.status_code == 201
    assert [m["user_name"] for m in response.data["mentions"]] == [
        cast["hod"].get_full_name()]
    assert Notification.objects.filter(
        category=Category.TASK_MENTIONED, recipient=cast["hod"]).exists()


def test_a_mentioned_person_is_not_also_sent_the_ordinary_comment_notice(
        cast, auth, make_task):
    """One sentence must not produce two emails to the same person."""
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS)
    post(auth, cast["employee"], task, "Ping.",
         mention_ids=[str(cast["hod"].id)])

    assert Notification.objects.filter(
        recipient=cast["hod"], category=Category.TASK_MENTIONED).count() == 1
    assert not Notification.objects.filter(
        recipient=cast["hod"], category=Category.TASK_COMMENTED).exists()


def test_mentioning_somebody_who_cannot_read_the_task_is_dropped(
        cast, auth, make_task):
    """
    Notifying somebody about a task they cannot open is worse than dropping the
    mention. The body may still read "@Someone" — that is the honest outcome.
    """
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    response = post(auth, cast["employee"], task, "@Outsider look at this",
                    mention_ids=[str(cast["outsider"].id)])

    assert response.status_code == 201
    assert response.data["mentions"] == []
    assert not Notification.objects.filter(
        recipient=cast["outsider"]).exists()


def test_mentioning_yourself_notifies_nobody(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    response = post(auth, cast["employee"], task, "Note to self.",
                    mention_ids=[str(cast["employee"].id)])
    assert response.data["mentions"] == []


def test_a_stale_mention_does_not_fail_the_whole_comment(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    response = post(auth, cast["employee"], task, "Two names, one valid.",
                    mention_ids=[str(cast["hod"].id), str(cast["outsider"].id)])
    assert response.status_code == 201
    assert len(response.data["mentions"]) == 1


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------
def test_an_author_can_edit_their_own_comment_and_it_is_marked(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    comment = post(auth, cast["employee"], task, "Draft uplodaed.").data
    assert comment["is_edited"] is False

    response = auth(cast["employee"]).patch(
        f"{comments_url(task)}{comment['id']}/", {"body": "Draft uploaded."},
        format="json")
    assert response.status_code == 200
    assert response.data["body"] == "Draft uploaded."
    assert response.data["is_edited"] is True
    assert response.data["edited_at"] is not None


@pytest.mark.parametrize("who", ["hod", "hr", "admin"])
def test_nobody_else_can_edit_it_however_senior(cast, auth, make_task, who):
    """
    An edit is shown as the author's own words with an "edited" marker. Letting
    somebody else rewrite them under that marker would make the marker a lie.
    """
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS)
    comment = post(auth, cast["employee"], task, "Draft uploaded.").data
    response = auth(cast[who]).patch(
        f"{comments_url(task)}{comment['id']}/", {"body": "Rewritten."},
        format="json")
    assert response.status_code == 403
    assert TaskComment.objects.get(pk=comment["id"]).body == "Draft uploaded."


def test_an_edit_is_recorded_on_the_timeline_with_the_previous_text(
        cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    comment = post(auth, cast["employee"], task, "Original wording.").data
    auth(cast["employee"]).patch(f"{comments_url(task)}{comment['id']}/",
                                 {"body": "Corrected wording."}, format="json")

    rows = auth(cast["employee"]).get(f"{LIST}{task.id}/timeline/").data
    edit = [r for r in rows if r["action"] == "comment_edited"]
    assert len(edit) == 1
    assert edit[0]["metadata"]["previous"] == "Original wording."


def test_a_comment_on_a_closed_task_cannot_be_edited(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], reviewer=cast["hod"],
                     status=Status.IN_PROGRESS)
    comment = post(auth, cast["employee"], task, "Mid-flight note.").data

    from tasks import workflow
    workflow.submit_for_review(task, cast["employee"])
    workflow.approve_review(task, cast["hod"])
    workflow.close(task, cast["hr"])

    assert auth(cast["employee"]).patch(
        f"{comments_url(task)}{comment['id']}/", {"body": "Changed my mind."},
        format="json").status_code == 403


def test_an_edit_cannot_blank_the_comment(cast, auth, make_task):
    task = make_task(cast["hod"], [cast["employee"]], status=Status.IN_PROGRESS)
    comment = post(auth, cast["employee"], task, "Something.").data
    assert auth(cast["employee"]).patch(
        f"{comments_url(task)}{comment['id']}/", {"body": "   "},
        format="json").status_code == 400
