"""Part 8: two tenants, provisioned the same way, both immediately usable.

    NIF  and  ABC School, both through provision_organization()

"No setup gaps allowed" is the requirement, and the only honest way to test it
is to USE each module rather than count its configuration rows. Five rows in a
table proves the rows exist; resolving an attendance policy for an employee
proves the module works.

So each test below exercises the thing the Phase S5 failure would have broken
-- the configuration lookup at the front of a workflow -- and does it for BOTH
tenants in the same test, which also re-proves isolation at every step.

NIF IS PROVISIONED HERE RATHER THAN READ FROM THE MIGRATION. The seeded NIF is
Tenant #1 and carries migration-written configuration; a second NIF-shaped
tenant built by ``provision_organization`` is what Part 8 actually asks about,
because it is the path every future customer takes. Both are checked: the
seeded NIF for "nothing regressed", the provisioned pair for "the path works".
"""
import datetime

import pytest

from tenancy import bootstrap, services
from tenancy.context import tenant_context

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 6, 15)

TENANTS = [
    dict(name="Nepal Internet Foundation", slug="nif2",
         document_prefix="NIF2", email="admin@nif2.test"),
    dict(name="ABC School", slug="abc2", document_prefix="ABC2",
         email="admin@abc2.test"),
]


@pytest.fixture
def pair(db, monthly_plan):
    """Both tenants, each through the one provisioning entry point."""
    return [
        services.provision_organization(plan=monthly_plan, today=TODAY,
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
        user = User(username=username, email=f"{username}@{organization.slug}.test",
                    role=role, employment_type=User.EmploymentType.PERMANENT,
                    department_ref=department, department=department.name,
                    date_of_joining=datetime.date(2020, 1, 1),
                    organization=organization)
        user.set_password("x-Conformance-1")
        user.save()
    return user


# --- the gate ----------------------------------------------------------
def test_both_tenants_are_provisioned_with_no_configuration_gaps(pair):
    for organization in pair:
        assert bootstrap.verify_organization(organization) == {}, (
            f"{organization.slug} has configuration gaps")


def test_both_tenants_are_open_for_business_immediately(pair):
    for organization in pair:
        assert organization.status == organization.Status.TRIAL
        assert organization.is_admitted is True, (
            f"{organization.slug} was provisioned but nobody can use it")


def test_both_tenants_have_an_administrator_who_can_create_everybody_else(pair):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    for organization in pair:
        # As the tenant: its own administrator is its own row.
        with tenant_context(organization):
            admin = User.objects.get(role="admin")
        assert admin.must_change_password is True
        assert admin.is_platform_staff is False
        assert admin.employee_id, "an employee with no employee id"


def test_the_platform_console_reports_both_tenants_healthy(pair, platform_user):
    from tenancy import console

    for organization in pair:
        health = console.tenant_health(platform_user, organization)
        assert health["usable"] is True, health
        assert health["configuration_gaps"] == {}


# --- "Leave approval works" -------------------------------------------
def test_leave_approval_works_for_both_tenants(pair):
    """The exact workflow the Phase S5 failure blocked.

    ``No LeaveType matches 'annual'`` happened at the first line of this
    flow. Everything after it -- day records, the balance, the approval --
    was unreachable.
    """
    from leaves import services as leave_services
    from leaves.models import Leave, LeaveDayRecord, LeaveType

    for organization in pair:
        employee = _employee(organization, f"emp-{organization.slug}")
        with tenant_context(organization):
            leave_type = LeaveType.objects.get(code__iexact="annual")

            leave = Leave.objects.create(
                user=employee, leave_type="annual", reason="Family event",
                start_date=datetime.date(2026, 7, 6),
                end_date=datetime.date(2026, 7, 8))
            records = leave_services.generate_leave_day_records(leave)
            assert records, "no day records generated"

            # The entitlement must be real, or leave can be applied for and
            # approved but never counted.
            entitlement = leave_services.entitlement_for("annual")
            assert entitlement > 0, (
                f"{organization.slug} grants 0 days of annual leave")

            leave.status = Leave.Status.APPROVED
            leave.save()
            LeaveDayRecord.objects.filter(leave_request=leave).update(
                status=LeaveDayRecord.Status.APPROVED)

            balance = leave_services.sync_simple_balance(employee, "annual",
                                                         2026)
            assert balance is not None
            assert balance.total_allocated > 0
            assert float(balance.used_so_far) > 0, (
                "approved leave did not consume the balance")
            assert leave.organization_id == organization.pk
            assert leave_type.organization_id == organization.pk


def test_each_tenants_leave_records_are_invisible_to_the_other(pair):
    from leaves.models import Leave

    first, second = pair
    for organization in pair:
        employee = _employee(organization, f"iso-{organization.slug}")
        with tenant_context(organization):
            Leave.objects.create(
                user=employee, leave_type="annual", reason="x",
                start_date=datetime.date(2026, 8, 3),
                end_date=datetime.date(2026, 8, 3))

    with tenant_context(first):
        from leaves.models import Leave as L

        assert L.objects.count() == 1
        assert L.objects.first().organization_id == first.pk
    with tenant_context(second):
        assert Leave.objects.count() == 1
        assert Leave.objects.first().organization_id == second.pk


# --- "Attendance works" -----------------------------------------------
def test_attendance_policy_resolves_for_both_tenants(pair):
    """A policy that does not resolve means every status falls to a fallback.

    ``resolve_policy`` returning the settings fallback instead of a real row
    is the quiet failure here: attendance appears to work, and every tenant
    on the platform silently shares one policy.
    """
    from attendance.policy import resolver

    for organization in pair:
        employee = _employee(organization, f"att-{organization.slug}")
        with tenant_context(organization):
            policy = resolver.resolve_policy(employee,
                                             datetime.date(2026, 7, 6))
            assert policy is not None
            assert policy.pk is not None, (
                f"{organization.slug} resolved the settings FALLBACK, not its "
                f"own policy row")
            assert policy.organization_id == organization.pk
            assert policy.name == bootstrap.DEFAULT_POLICY_NAME

            shift = resolver.resolve_shift(employee,
                                           datetime.date(2026, 7, 6), policy)
            assert shift is not None
            assert shift.organization_id == organization.pk


def test_attendance_can_be_recorded_and_derived_for_both_tenants(pair):
    from attendance.models import Attendance
    from attendance.services import recompute_status

    for organization in pair:
        employee = _employee(organization, f"punch-{organization.slug}")
        with tenant_context(organization):
            record = Attendance.objects.create(
                employee=employee, date=datetime.date(2026, 7, 6),
                check_in=datetime.datetime(2026, 7, 6, 10, 2,
                                            tzinfo=datetime.timezone.utc),
                check_out=datetime.datetime(2026, 7, 6, 18, 5,
                                             tzinfo=datetime.timezone.utc))
            recompute_status(record)
            record.refresh_from_db()
            assert record.status, "attendance derived no status at all"
            assert record.organization_id == organization.pk


# --- "Task management works" ------------------------------------------
def test_task_management_works_for_both_tenants(pair):
    """Includes the number sequence, which is per (tenant, year)."""
    from tasks.models import Task, TaskTemplate
    from tasks.services import generate_task_number

    for organization in pair:
        creator = _employee(organization, f"task-{organization.slug}",
                            role="admin")
        with tenant_context(organization):
            template = TaskTemplate.objects.get(name="Employee Onboarding")
            assert template.items.exists()

            task = Task.objects.create(
                title="Onboard a new joiner", created_by=creator,
                template=template, task_number=generate_task_number(),
                due_date=datetime.date(2026, 7, 31))
            assert task.task_number, "a task with no number"
            assert task.task_number.startswith(organization.document_prefix), (
                f"{task.task_number} does not carry {organization.slug}'s "
                f"document prefix")
            assert task.organization_id == organization.pk


def test_a_template_applied_copies_its_checklist_onto_the_task(pair):
    from tasks import workflow
    from tasks.models import Task, TaskChecklistItem, TaskTemplate

    for organization in pair:
        creator = _employee(organization, f"tmpl-{organization.slug}",
                            role="admin")
        with tenant_context(organization):
            template = TaskTemplate.objects.get(name="Monthly Report")
            task = Task.objects.create(title="June report",
                                        created_by=creator)
            workflow.apply_template(task, template, creator)
            items = TaskChecklistItem.objects.filter(group__task=task)
            assert items.exists(), "the template's checklist was not copied"
            assert all(item.organization_id == organization.pk
                       for item in items)


def test_the_two_tenants_number_their_tasks_independently(pair):
    """Tenant #2's first task must be its own #1, not the platform's #n."""
    from tasks.models import Task
    from tasks.services import generate_task_number

    numbers = []
    for organization in pair:
        creator = _employee(organization, f"seq-{organization.slug}",
                            role="admin")
        with tenant_context(organization):
            task = Task.objects.create(title="First", created_by=creator,
                                        task_number=generate_task_number())
            numbers.append(task.task_number)

    assert numbers[0] != numbers[1]
    assert all(number.endswith("0001") for number in numbers), numbers


# --- "Minutes work" ---------------------------------------------------
def test_minutes_work_for_both_tenants(pair):
    from minutes.models import Minute, MinuteType
    from minutes.services import generate_minute_number

    for organization in pair:
        author = _employee(organization, f"min-{organization.slug}",
                           role="admin")
        with tenant_context(organization):
            minute_type = MinuteType.objects.get(code="department")
            number = generate_minute_number()
            assert number, "no minute number issued"

            minute = Minute.objects.create(
                minute_type=minute_type, minute_number=number,
                meeting_date=datetime.date(2026, 7, 6),
                meeting_time=datetime.time(14, 0), created_by=author,
                subject="Weekly review")
            assert minute.organization_id == organization.pk
            assert minute_type.organization_id == organization.pk


def test_the_two_tenants_number_their_minutes_independently(pair):
    from minutes.services import generate_minute_number

    numbers = []
    for organization in pair:
        with tenant_context(organization):
            numbers.append(generate_minute_number())
    assert numbers[0] != numbers[1]
    assert all(number.endswith("000001") for number in numbers), numbers


# --- "Documents work" -------------------------------------------------
def test_documents_work_for_both_tenants(pair):
    """A memo, its number, and the issued-document record a PDF is verified by."""
    from documents.models import IssuedDocument
    from documents.services import issue_document
    from memos.models import Memo
    from memos.services import generate_memo_number

    for organization in pair:
        author = _employee(organization, f"doc-{organization.slug}",
                           role="admin")
        with tenant_context(organization):
            number = generate_memo_number("administrative")
            assert number.startswith(organization.document_prefix), (
                f"{number} does not carry {organization.slug}'s prefix")

            memo = Memo.objects.create(subject="Notice", created_by=author,
                                        memo_number=number)
            issued = issue_document(
                IssuedDocument.DocType.MEMO, "memo", memo, "Notice", author,
                [])
            assert issued.document_number == number
            assert memo.organization_id == organization.pk


def test_a_document_number_identifies_which_tenant_issued_it(pair):
    """The prefix is what makes a verification URL unambiguous."""
    from memos.services import generate_memo_number

    numbers = []
    for organization in pair:
        with tenant_context(organization):
            numbers.append(generate_memo_number("administrative"))
    first, second = numbers
    assert first.startswith("NIF2-") and second.startswith("ABC2-")
    assert first != second


# --- the seeded NIF has not regressed ---------------------------------
def test_the_seeded_nif_tenant_still_has_its_own_configuration(nif):
    """Phase S6 must change nothing for the customer already running.

    NIF's configuration came from data migrations, not from the bootstrap, so
    this asserts the two did not collide -- and that NIF kept its own five
    leave types rather than being reduced to the bootstrap's three.
    """
    from leaves.models import LeaveType

    with tenant_context(nif):
        codes = set(LeaveType.objects.values_list("code", flat=True))
        assert {"ANNUAL", "SICK", "CASUAL", "MATERNITY", "UNPAID"} <= codes, (
            f"NIF lost leave types; it has {sorted(codes)}")


def test_the_seeded_nif_tenant_keeps_its_legacy_document_numbers(nif):
    """A document number already printed, signed and filed cannot be restated."""
    assert nif.legacy_number_formats is True
    assert nif.document_prefix == "NIFN"


def test_provisioning_a_tenant_does_not_touch_another_tenants_configuration(
        nif, pair):
    from leaves.models import Department, LeaveType
    from minutes.models import MinuteType

    for model in (LeaveType, Department, MinuteType):
        # Gathered per tenant, inside its own context: a cross-tenant query
        # returns nothing under row-level security, and "nothing" would make
        # this test pass without proving anything.
        with tenant_context(nif):
            nif_rows = set(model.objects.values_list("pk", flat=True))
        assert nif_rows, f"NIF has no {model.__name__} rows at all"

        for organization in pair:
            with tenant_context(organization):
                rows = set(model.objects.values_list("pk", flat=True))
            assert rows, f"{organization.slug} has no {model.__name__} rows"
            assert not rows & nif_rows, (
                f"{model.__name__} rows shared between nif and "
                f"{organization.slug}")
