"""
Phase 70 - who gets told, and when.

The claim being tested is not "a notification was sent" but "the person who has to
ACT was told". Those differ in the way that matters: a workflow that notifies the
requester at every stage and never notifies the approver looks busy and stalls.

Two structural properties are asserted alongside the routing:

  * notifications fire on COMMIT, so a transition that rolls back sends nothing;
  * a notification failure never breaks the transition, because the asset having
    moved is the fact of record and the message is only a nudge toward it.
"""
import pytest
from django.db import transaction

from inventory import lifecycle
from inventory.models import InventoryItem
from notifications.models import Category, Notification

# `transaction=True` rather than the usual wrapped-in-a-rollback database, because
# the thing under test is `transaction.on_commit`. In the default test database
# every test runs inside an atomic block that never commits, so on_commit callbacks
# would never fire and every assertion below would pass or fail for the wrong
# reason - and the rollback test would prove nothing at all.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(scope="module", autouse=True)
def restore_flushed_seed_data(django_db_setup, django_db_blocker):
    """Put back everything a transactional test flushes.

    Every test in this module is transactional, and Django FLUSHES all tables
    when such a test tears down. That takes the migration-seeded rows with it —
    LeaveType, Holiday, EntitlementRule and friends from leaves/migrations 0005,
    0010 and 0013 — and nothing ever re-creates them. Without this, the flush
    silently breaks a later module that resolves e.g. leave_type='annual'
    (leaves/tests/test_cluster_b.py), and which tests pass depends purely on
    file collection order.

    Module-scoped restore, taken verbatim from biometric/tests/test_realtime.py,
    where the same problem and reasoning are documented at length. Only leaves.*
    rows are restored: framework tables (contenttypes, permissions) are rebuilt
    by post_migrate after the flush, and restoring them too would collide with
    the fresh rows on their natural-key unique constraints.
    """
    import json

    from django.db import connection

    with django_db_blocker.unblock():
        # tenancy.* AS WELL AS leaves.*, AND TENANCY FIRST (Phase S2).
        #
        # Every leaves row now carries a non-null FK to tenancy.Organization.
        # Restoring leaves.* alone re-inserts children whose parent the flush
        # has just deleted, and the next constraint check fails with:
        #
        #     IntegrityError: The row in table 'leaves_holiday' ... has an
        #     invalid foreign key
        #
        # The sort puts the parents in first; it is stable, so the relative
        # order within each group is unchanged.
        rows = [
            obj for obj in json.loads(connection.creation.serialize_db_to_string())
            if obj["model"].startswith(("leaves.", "tenancy."))
        ]
        snapshot = json.dumps(
            sorted(rows, key=lambda obj: not obj["model"].startswith("tenancy.")))

    yield

    with django_db_blocker.unblock():
        connection.creation.deserialize_db_from_string(snapshot)


@pytest.fixture
def held_item(employee, officer, stock_item):
    """An asset in the employee's hands, by the direct route."""
    from inventory.services import assign_item

    assign_item(stock_item.id, employee, officer)
    stock_item.refresh_from_db()
    return stock_item


def _sent(user, category=None):
    rows = Notification.objects.filter(recipient=user)
    return rows.filter(category=category) if category else rows


def _titles(user):
    return list(Notification.objects.filter(recipient=user)
                .values_list("category", flat=True))


class TestTheRequestChainTellsWhoeverMustActNext:
    def test_a_new_request_reaches_the_supervisor_not_the_requester(
            self, employee, head, stock_item):
        lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)

        assert _sent(head, Category.INVENTORY_ASSET_REQUESTED).exists()
        # Telling somebody what they just did themselves is noise, and noise is
        # what makes the notification that matters get ignored.
        assert not _sent(employee).exists()

    def test_supervisor_approval_hands_the_queue_to_the_store(
            self, employee, head, officer, stock_item):
        req = lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)
        lifecycle.supervisor_decision(req.id, head, approve=True, remarks="Agreed")

        assert _sent(officer, Category.INVENTORY_ASSET_APPROVAL_REQUIRED).exists()

    def test_inventory_approval_tells_the_requester_it_is_coming(
            self, employee, head, officer, stock_item):
        req = lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)
        lifecycle.supervisor_decision(req.id, head, approve=True)
        lifecycle.inventory_decision(req.id, officer, approve=True)

        assert _sent(employee, Category.INVENTORY_ASSET_READY).exists()

    def test_handover_asks_the_recipient_to_confirm(
            self, employee, head, officer, stock_item):
        """
        The most important message in the chain. Until the recipient accepts, the
        record says an officer handed something over - not that anybody has it -
        and nothing else prompts them to close that gap.
        """
        req = lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)
        lifecycle.supervisor_decision(req.id, head, approve=True)
        lifecycle.inventory_decision(req.id, officer, approve=True)
        lifecycle.hand_over(req.id, officer)

        note = _sent(employee, Category.INVENTORY_ASSET_HANDED_OVER).first()
        assert note is not None
        assert "confirm" in note.title.lower()

    def test_acceptance_closes_the_loop_back_to_the_store(
            self, employee, head, officer, stock_item):
        req = lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)
        lifecycle.supervisor_decision(req.id, head, approve=True)
        lifecycle.inventory_decision(req.id, officer, approve=True)
        lifecycle.hand_over(req.id, officer)
        lifecycle.accept_asset(req.id, employee)

        assert _sent(officer, Category.INVENTORY_ASSET_ACCEPTED).exists()

    def test_a_refusal_reaches_the_requester_with_the_reason(
            self, employee, head, stock_item):
        req = lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)
        lifecycle.supervisor_decision(
            req.id, head, approve=False,
            remarks="You already hold a laptop issued last year")

        note = _sent(employee, Category.INVENTORY_ASSET_REJECTED).first()
        assert note is not None
        assert "already hold a laptop" in note.body

    def test_a_request_naming_a_category_does_not_say_none(
            self, employee, head, laptop_category):
        """
        A request may name a category rather than an asset, and the notification is
        written before any asset is chosen. `item_label` exists so this reads as
        "your request for Laptop" rather than "your request for  ·".
        """
        req = lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            category=laptop_category)
        lifecycle.supervisor_decision(req.id, head, approve=False,
                                      remarks="No spare laptops this quarter")

        note = _sent(employee, Category.INVENTORY_ASSET_REJECTED).first()
        assert "Laptop" in note.title
        assert "None" not in note.title


class TestReturnsAndMaintenance:
    def test_a_return_reaches_the_store_that_must_inspect_it(
            self, employee, officer, held_item):
        lifecycle.create_return(
            item=held_item, actor=employee, reason="Finished with it",
            declared_condition=InventoryItem.Condition.GOOD)

        assert _sent(officer, Category.INVENTORY_RETURN_REQUESTED).exists()

    def test_a_refused_return_tells_the_holder_it_is_still_theirs(
            self, employee, officer, held_item):
        """
        Acceptance is pleasant news; refusal is the one somebody must act on. A
        person who believes they have handed an asset back and has not would
        otherwise find out when an overdue report names them.
        """
        record = lifecycle.create_return(
            item=held_item, actor=employee, reason="Finished with it",
            declared_condition=InventoryItem.Condition.GOOD)
        lifecycle.verify_return(record.id, officer)
        lifecycle.inspect_return(record.id, officer,
                                 condition=InventoryItem.Condition.DAMAGED,
                                 remarks="Screen is cracked, not as declared")
        lifecycle.reject_return(record.id, officer,
                                reason="Screen cracked - not accepted as declared")

        note = _sent(employee, Category.INVENTORY_RETURN_COMPLETED).first()
        assert note is not None
        assert "still recorded against you" in note.body

    def test_a_fault_reaches_the_store(self, employee, officer, held_item):
        lifecycle.report_maintenance(
            item=held_item, actor=employee, issue="Battery no longer charges",
            priority="high")

        assert _sent(officer, Category.INVENTORY_MAINTENANCE_REPORTED).exists()

    def test_a_repair_reaches_whoever_reported_it(
            self, employee, officer, held_item):
        ticket = lifecycle.report_maintenance(
            item=held_item, actor=employee, issue="Battery no longer charges")
        lifecycle.assign_maintenance(ticket.id, officer, vendor="ACME Repairs")
        lifecycle.start_maintenance(ticket.id, officer)
        lifecycle.complete_maintenance(ticket.id, officer,
                                       resolution="Battery replaced under warranty")

        assert _sent(employee, Category.INVENTORY_MAINTENANCE_DONE).exists()


class TestTheWiringItself:
    def test_nothing_is_sent_for_a_transition_that_rolls_back(
            self, employee, head, stock_item):
        """
        Notifications go out on COMMIT. Sending inline would mean an email for a
        request that a later guard rolled back - the in-app row would vanish with
        the rollback, the email would not, and the recipient would be told to act
        on something that does not exist.
        """
        with pytest.raises(RuntimeError):
            with transaction.atomic():
                lifecycle.create_request(
                    requester=employee, purpose="Need a laptop for fieldwork",
                    item=stock_item)
                raise RuntimeError("something later failed")

        assert not Notification.objects.filter(recipient=head).exists()

    def test_a_notification_failure_does_not_undo_the_transition(
            self, employee, head, stock_item, monkeypatch):
        """
        The asset having moved is the fact of record; the message is a nudge toward
        it. A mail server outage must not roll back a lifecycle step, or the module
        stops working every time an unrelated service does.
        """
        from inventory import notifications as inv_notifications

        def boom(*args, **kwargs):
            raise RuntimeError("mail server down")

        monkeypatch.setattr(inv_notifications, "notify", boom)

        req = lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)

        from inventory.models import AssetRequest
        assert AssetRequest.objects.filter(pk=req.id).exists()
        assert not Notification.objects.filter(recipient=head).exists()

    def test_the_same_stage_twice_does_not_notify_twice(
            self, employee, head, officer, stock_item):
        """
        Every send carries an idempotency key. Without one, a retried request - a
        double-clicked button, a replayed webhook - reaches the approver twice, and
        an approver who has learned that half the queue is duplicates stops reading
        it.
        """
        req = lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)
        lifecycle.supervisor_decision(req.id, head, approve=True)
        # Replay the same announcement directly - the key is what must stop it.
        from inventory import notifications as inv_notifications
        inv_notifications.asset_request_at_inventory(req)

        assert _sent(officer,
                     Category.INVENTORY_ASSET_APPROVAL_REQUIRED).count() == 1

    def test_an_inactive_account_is_not_notified(
            self, employee, head, stock_item):
        head.is_active = False
        head.save(update_fields=["is_active"])

        lifecycle.create_request(
            requester=employee, purpose="Need a laptop for fieldwork",
            item=stock_item)

        assert not Notification.objects.filter(recipient=head).exists()

    def test_every_category_used_here_is_a_declared_choice(self):
        """
        `category` is a CharField with choices and max_length=40. A category that
        is neither would be written to the row and then never match a preference
        lookup or a filter, which fails silently rather than loudly.
        """
        used = [
            Category.INVENTORY_ASSET_REQUESTED,
            Category.INVENTORY_ASSET_APPROVAL_REQUIRED,
            Category.INVENTORY_ASSET_READY,
            Category.INVENTORY_ASSET_HANDED_OVER,
            Category.INVENTORY_ASSET_ACCEPTED,
            Category.INVENTORY_ASSET_REJECTED,
            Category.INVENTORY_RETURN_REQUESTED,
            Category.INVENTORY_RETURN_COMPLETED,
            Category.INVENTORY_MAINTENANCE_REPORTED,
            Category.INVENTORY_MAINTENANCE_DONE,
        ]
        declared = set(Category.values)
        for category in used:
            assert category in declared
            assert len(category) <= 40, category
