"""Part 9: three tenants, provisioned the same way, nine modules each.

    ABC School   ·   XYZ Hospital   ·   Demo NGO

WHY A SECOND CONFORMANCE MODULE. ``test_provisioning_conformance`` already
proves the provisioning PATH with a pair of tenants, and in more depth per
module. This one answers a different question: the brief names three specific
customers and nine specific modules, including four that the pair never
touched -- Circulars, Inventory, Notifications and Reports. A module that is
seeded but never exercised is exactly how the Phase S5 failure survived to
production: the leave types were "configured" and the first leave request
still died.

THREE TENANTS RATHER THAN TWO IS NOT CEREMONY EITHER. With two, a query that
accidentally returns "everything except the tenant I am in" is
indistinguishable from one that returns "the other tenant". With three, the
wrong answer has the wrong cardinality and the assertion catches it.

Each test USES the module rather than counting its configuration rows, and
does so as each tenant in turn, so every test is also an isolation test.
"""
import datetime

import pytest

from tenancy import bootstrap, services
from tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 6, 15)

TENANTS = [
    dict(name="ABC School", slug="abc-school", document_prefix="ABCS",
         email="admin@abc-school.edu.np", industry="education",
         country="NP"),
    dict(name="XYZ Hospital", slug="xyz-hospital", document_prefix="XYZH",
         email="admin@xyz-hospital.org.np", industry="healthcare",
         country="NP"),
    dict(name="Demo NGO", slug="demo-ngo", document_prefix="DNGO",
         email="admin@demo-ngo.org.np", industry="nonprofit",
         country="NP"),
]


@pytest.fixture
def three(db, monthly_plan):
    """All three, each through the one provisioning entry point.

    No arguments beyond what the brief's input list names, and no follow-up
    calls: if a tenant needs a second step to become usable, this fixture is
    where that would show up as a failing test rather than as a runbook.
    """
    return [
        services.provision_organization(
            plan=monthly_plan, today=TODAY,
            admin_email=f"root@{spec['slug']}.test",
            admin_name="Root Admin", **spec)
        for spec in TENANTS
    ]


def _employee(organization, username, *, role="maker", department_code="ICT"):
    from django.contrib.auth import get_user_model

    from leaves.models import Department

    User = get_user_model()
    with tenant_context(organization):
        department = Department.objects.get(code=department_code)
        user = User(username=username,
                    email=f"{username}@{organization.slug}.test",
                    role=role,
                    employment_type=User.EmploymentType.PERMANENT,
                    department_ref=department, department=department.name,
                    date_of_joining=datetime.date(2020, 1, 1),
                    organization=organization)
        user.set_password("x-Conformance-1")
        user.save()
    return user


# --- the gate -----------------------------------------------------------
def test_all_three_are_provisioned_complete_and_open(three):
    """One call each, and every one of them is ready to be handed over."""
    assert len({org.slug for org in three}) == 3
    for organization in three:
        assert bootstrap.verify_organization(organization) == {}, (
            f"{organization.slug} has configuration gaps")
        assert organization.is_admitted is True, (
            f"{organization.slug} was provisioned but nobody can use it")
        assert organization.status == organization.Status.TRIAL


def test_the_console_answers_ready_for_each_of_them(three, platform_user):
    from tenancy import console

    for organization in three:
        health = console.tenant_health(platform_user, organization)
        assert health["verdict"] == "Tenant Ready", health


# --- 1. leave workflow --------------------------------------------------
def test_leave_runs_end_to_end_for_each_tenant(three):
    """The workflow the Phase S5 failure blocked at its first line."""
    from leaves import services as leave_services
    from leaves.models import Leave, LeaveDayRecord, LeaveType

    for organization in three:
        employee = _employee(organization, f"emp-{organization.slug}")
        with tenant_context(organization):
            assert LeaveType.objects.get(code__iexact="annual")
            leave = Leave.objects.create(
                user=employee, leave_type="annual", reason="Family event",
                start_date=datetime.date(2026, 7, 6),
                end_date=datetime.date(2026, 7, 7),
                status=Leave.Status.PENDING)
            assert leave_services.generate_leave_day_records(leave), (
                f"{organization.slug}: a leave with no day records cannot be "
                f"counted against a balance")
            assert leave_services.entitlement_for("annual") > 0, (
                f"{organization.slug} grants 0 days of annual leave, so leave "
                f"can be approved and never counted")
            assert LeaveDayRecord.objects.filter(
                leave_request=leave).count() == 2


# --- 2. task workflow ---------------------------------------------------
def test_tasks_and_their_templates_work_for_each_tenant(three):
    from tasks.models import Task, TaskTemplate
    from tasks.services import generate_task_number

    for organization in three:
        creator = _employee(organization, f"task-{organization.slug}",
                            role="admin")
        with tenant_context(organization):
            template = TaskTemplate.objects.filter(
                name="Employee Onboarding").first()
            assert template is not None, (
                f"{organization.slug}: no seeded template to start from")
            assert template.items.exists(), (
                f"{organization.slug}: a template with no checklist teaches "
                f"nobody what templates are for")
            task = Task.objects.create(
                title="Onboard the first hire", created_by=creator,
                template=template, task_number=generate_task_number(),
                due_date=datetime.date(2026, 7, 20))
            assert task.task_number.startswith(organization.document_prefix), (
                f"{task.task_number} does not carry {organization.slug}'s "
                f"document prefix")


# --- 3. attendance ------------------------------------------------------
def test_attendance_resolves_a_policy_and_records_a_day_for_each_tenant(three):
    from attendance.models import Attendance
    from attendance.policy import resolver as policy_resolver
    from attendance.services import recompute_status

    for organization in three:
        employee = _employee(organization, f"att-{organization.slug}")
        with tenant_context(organization):
            policy = policy_resolver.resolve_policy(
                employee, datetime.date(2026, 7, 6))
            assert policy is not None and policy.pk is not None, (
                f"{organization.slug} resolved the settings FALLBACK rather "
                f"than its own policy row, so every tenant would share one")
            assert policy.name == bootstrap.DEFAULT_POLICY_NAME

            record = Attendance.objects.create(
                employee=employee, date=datetime.date(2026, 7, 6),
                check_in=datetime.datetime(2026, 7, 6, 10, 2,
                                           tzinfo=datetime.timezone.utc),
                check_out=datetime.datetime(2026, 7, 6, 18, 5,
                                            tzinfo=datetime.timezone.utc))
            recompute_status(record)
            record.refresh_from_db()
            assert record.status, (
                f"{organization.slug}: attendance derived no status at all")


# --- 4. minutes ---------------------------------------------------------
def test_minutes_can_be_recorded_and_are_numbered_per_tenant(three):
    from minutes.models import Minute, MinuteType
    from minutes.services import generate_minute_number

    numbers = []
    for organization in three:
        author = _employee(organization, f"min-{organization.slug}",
                           role="admin")
        with tenant_context(organization):
            minute_type = MinuteType.objects.get(code="department")
            number = generate_minute_number()
            minute = Minute.objects.create(
                minute_type=minute_type, minute_number=number,
                meeting_date=datetime.date(2026, 7, 6),
                meeting_time=datetime.time(14, 0), created_by=author,
                subject="First departmental meeting")
            assert minute.organization_id == organization.pk
            numbers.append(number)

    # EACH TENANT NUMBERS FROM ONE. A shared sequence would leak how many
    # minutes the platform's other customers have filed -- three tenants all
    # holding #1 is the assertion that the counter is per tenant.
    assert len(set(numbers)) == 3, numbers
    assert all(number.endswith("000001") for number in numbers), numbers


# --- 5. circulars -------------------------------------------------------
def test_a_circular_can_be_issued_and_is_numbered_per_tenant(three):
    from circulars.models import Circular
    from circulars.services import generate_circular_number

    seen = []
    for organization in three:
        author = _employee(organization, f"cir-{organization.slug}",
                           role="admin")
        with tenant_context(organization):
            circular = Circular.objects.create(
                subject="Office hours from the first of next month",
                content="<p>09:00 to 17:00, Sunday to Thursday.</p>",
                category=Circular.Category.ADMINISTRATIVE,
                classification=Circular.Classification.INTERNAL,
                created_by=author, status=Circular.Status.DRAFT,
                circular_number=generate_circular_number())
            seen.append(circular.circular_number)
            # Each tenant sees exactly its own, which is the assertion three
            # tenants make stronger than two.
            assert Circular.objects.count() == 1, (
                f"{organization.slug} can see another tenant's circulars")

    assert len(set(seen)) == 3, f"circular numbers collided: {seen}"


# --- 6. inventory -------------------------------------------------------
def test_inventory_opens_with_categories_and_can_register_an_asset(three):
    """The section this brief added to the bootstrap.

    `inventory/0006` seeded these for NIF before tenancy existed, so until now
    a new customer opened Inventory with an empty category list: every asset
    they registered was uncategorised, with nothing to report by and no
    useful-life default for depreciation to inherit.
    """
    from inventory import services as inventory_services
    from inventory.models import InventoryCategory, InventoryItem

    for organization in three:
        with tenant_context(organization):
            assert InventoryCategory.objects.count() == len(
                bootstrap.INVENTORY_CATEGORIES), (
                f"{organization.slug} opened Inventory with no taxonomy")
            item = InventoryItem.objects.create(
                name="Dell Latitude 5440",
                asset_code=inventory_services.generate_asset_code(),
                category=InventoryCategory.objects.get(name="Laptop"))
            assert item.asset_code, (
                f"{organization.slug}: an asset with no asset code")
            assert item.category.name == "Laptop"


# --- 7. notifications ---------------------------------------------------
def test_notification_defaults_are_the_tenants_own_and_delivery_works(three):
    """Preferences fall back to the ORGANIZATION's defaults, not the code's.

    Which is why the bootstrap writes them explicitly: an operator opening the
    settings screen on a tenant whose preferences are NULL cannot tell whether
    email is on or simply unconfigured.
    """
    from notifications import dispatcher
    from notifications.models import Category, Notification

    for organization in three:
        recipient = _employee(organization, f"ntf-{organization.slug}")
        with tenant_context(organization):
            preference = dispatcher.get_preference(recipient, Category.LEAVE_APPROVED)
            assert preference.in_app_enabled is True
            assert preference.email_enabled is True

            dispatcher.notify(recipient, Category.LEAVE_APPROVED,
                              "Your leave was approved")
            assert Notification.objects.filter(recipient=recipient).count() == 1
            # And nobody else's inbox, which is the isolation half.
            assert Notification.objects.count() == 1, (
                f"{organization.slug} can see another tenant's notifications")


# --- 8. documents -------------------------------------------------------
def test_a_document_number_identifies_which_tenant_issued_it(three):
    from documents.models import IssuedDocument
    from documents.services import issue_document
    from memos.models import Memo
    from memos.services import generate_memo_number

    numbers = {}
    for organization in three:
        author = _employee(organization, f"doc-{organization.slug}",
                           role="admin")
        with tenant_context(organization):
            number = generate_memo_number("administrative")
            memo = Memo.objects.create(subject="Notice", created_by=author,
                                       memo_number=number)
            issued = issue_document(
                IssuedDocument.DocType.MEMO, "memo", memo, "Notice", author,
                [])
            assert issued.document_number == number
            numbers[organization.slug] = number

    for spec in TENANTS:
        number = numbers[spec["slug"]]
        assert spec["document_prefix"] in number, (
            f"{spec['slug']} issued {number}, which does not carry its own "
            f"prefix -- two customers' letters would be indistinguishable")


# --- 9. reports ---------------------------------------------------------
def test_a_report_generates_for_each_tenant_and_contains_only_its_own(three):
    """Generated SYNCHRONOUSLY here on purpose.

    Production spawns a thread, and that thread has to bind its own tenant
    because `app.current_org` is connection state. Calling the service
    directly inside `tenant_context` is the same contract without the race,
    so a failure here is about the report and not about the thread.
    """
    from reports import report_service
    from reports.models import ReportRun, ReportType

    for organization in three:
        requester = _employee(organization, f"rep-{organization.slug}",
                              role="admin")
        with tenant_context(organization):
            run = ReportRun.objects.create(
                report_type=ReportType.EMPLOYEE_REGISTER,
                requested_by=requester, params={"year": 2026})
            report_service.generate_report(run)
            run.refresh_from_db()
            assert run.status == ReportRun.Status.READY, (
                f"{organization.slug}: {run.status} / {run.error}")
            assert run.file, f"{organization.slug}: a ready report with no file"
            assert ReportRun.objects.count() == 1, (
                f"{organization.slug} can see another tenant's report runs")
