"""Who a given user's analytics cover.

Every endpoint resolves its population through ``resolve_scope`` and nothing
else. That matters twice over: it is the security boundary, and it delegates to
``attendance.workforce.aggregates.scoped_employees`` -- the same helper the
Phase 9 dashboards and the report scoping already use -- so there is one scoping
rule with four consumers rather than four rules that drift.

A ``?department=`` parameter can only NARROW. A department head who passes
another department's id gets their own scope back: silently narrowing cannot
leak, whereas a 403 confirms the other department exists.
"""
from dataclasses import dataclass, field

from users.models import User

ORG = "organization"
DEPARTMENT = "department"

# Below this headcount a department-level percentage is one person's attendance
# record wearing a department's name. Suppressed rather than published -- the
# phase brief forbids scoring employees, and a 2-person department is a
# transparent proxy for one.
MIN_DEPARTMENT_SAMPLE = 3


@dataclass(frozen=True)
class Scope:
    """A resolved analytics population.

    ``employees`` is the authoritative id list; every metric filters on it.
    ``departments`` maps department id (str) -> name, with ``None`` keyed as
    "unassigned" so employees with no department are never silently dropped from
    a total.
    """
    level: str
    employee_ids: tuple
    departments: dict
    department_of: dict           # employee id -> department key
    floors: dict                  # employee id -> eligibility floor date
    own_department_id: str | None = None
    named_departments: bool = True
    warnings: tuple = field(default_factory=tuple)
    # Phase S3. Which tenant this population belongs to. Part of the cache
    # identity below, and the reason two tenants can no longer share an entry.
    organization_token: str = ""

    @property
    def headcount(self):
        return len(self.employee_ids)

    def department_headcount(self, key):
        return sum(1 for value in self.department_of.values() if value == key)

    def is_small(self, key):
        return self.department_headcount(key) < MIN_DEPARTMENT_SAMPLE

    def label(self, key):
        return self.departments.get(key, "Unassigned")

    def as_dict(self):
        own = self.departments.get(self.own_department_id) if self.own_department_id else None
        return {
            "level": self.level,
            "department": own,
            "department_id": self.own_department_id,
            "headcount": self.headcount,
            "departments": len(self.departments),
            "named_departments": self.named_departments,
            "min_sample": MIN_DEPARTMENT_SAMPLE,
            "warnings": list(self.warnings),
        }

    def cache_token(self):
        """Cache identity. Deliberately NOT the user id: every manager in a
        department shares one entry, and HR and Admin share the org entry.

        THE TENANT TOKEN LEADS, AND THAT IS A SECURITY FIX (Phase S3).
        Without it this returned ``organization:-:<headcount>`` -- so two
        tenants whose HR users had the same headcount produced the SAME key and
        one was served the other's dashboard. There is no bad query in that
        path: the leak is downstream of the ORM entirely, which is why adding
        organization_id to 28 tables did not touch it.
        """
        token = self.organization_token or _tenant_token()
        return (f"org:{token}:{self.level}:{self.own_department_id or '-'}:"
                f"{len(self.employee_ids)}")


UNASSIGNED = "unassigned"


def _tenant_token():
    """The current tenant's cache token. See tenancy.keys for the shape."""
    from tenancy.keys import current_token

    return current_token()


def resolve_scope(user, department_param=None):
    """The population `user` may analyse, optionally narrowed to one department.

    ONE query. ``scoped_employees`` already applies the role rules; the only
    thing added here is the optional narrowing and the per-employee metadata
    (department, eligibility floor) that the metrics need in bulk.
    """
    from attendance.workforce.aggregates import scoped_employees

    is_org = user.role in (User.Roles.APPROVER, User.Roles.BOD, User.Roles.ADMIN)
    rows = list(scoped_employees(user).values(
        "id", "department_ref_id", "department_ref__name",
        "date_joined", "date_of_joining"))

    own_department_id = (str(user.department_ref_id)
                         if getattr(user, "department_ref_id", None) else None)

    warnings = []
    if department_param:
        wanted = _match_department(rows, department_param)
        if wanted is None:
            warnings.append("department_not_in_scope")
        else:
            rows = [r for r in rows if _dept_key(r) == wanted]
            own_department_id = None if wanted == UNASSIGNED else wanted

    employee_ids, departments, department_of, floors = [], {}, {}, {}
    for row in rows:
        pk = row["id"]
        employee_ids.append(pk)
        key = _dept_key(row)
        departments[key] = row["department_ref__name"] or "Unassigned"
        department_of[pk] = key
        floors[pk] = _floor(row)

    level = ORG if is_org and not department_param else DEPARTMENT
    if level == DEPARTMENT and not own_department_id and len(departments) == 1:
        own_department_id = next(iter(departments))

    return Scope(
        level=level,
        employee_ids=tuple(employee_ids),
        departments=departments,
        department_of=department_of,
        floors=floors,
        own_department_id=own_department_id,
        # A department head may see the ranking, but other departments stay
        # unnamed (approved decision #4). HR and Admin see everyone by name.
        named_departments=is_org,
        warnings=tuple(warnings),
        # Taken from the USER, not from request context: a scope is the
        # population one person may analyse, and that person belongs to exactly
        # one tenant. Deriving it here means an export running in a background
        # thread gets the same token as the request that queued it.
        organization_token=_token_for_user(user),
    )


def _token_for_user(user):
    from tenancy.keys import current_token, token_for

    org_id = getattr(user, "organization_id", None)
    return token_for(org_id) if org_id is not None else current_token()


def scope_from_ids(employee_ids=None, department=None):
    """Build a Scope from report parameters rather than from a request.

    Export builders run in a background thread with no request in hand: they get
    a ``ReportRun.params`` dict whose ``employee_ids`` was injected server-side
    by ``ReportsViewSet._scoped_params``. Rebuilding the scope from that list
    means an exported PDF covers exactly the people the requester could see on
    screen -- the narrowing is not re-derived here and cannot be widened here.
    """
    qs = User.objects.filter(is_active=True)
    if employee_ids:
        qs = qs.filter(pk__in=employee_ids)
    rows = list(qs.values("id", "department_ref_id", "department_ref__name",
                          "date_joined", "date_of_joining"))

    if department:
        wanted = _match_department(rows, department)
        if wanted is not None:
            rows = [row for row in rows if _dept_key(row) == wanted]

    employee_pks, departments, department_of, floors = [], {}, {}, {}
    for row in rows:
        pk = row["id"]
        employee_pks.append(pk)
        key = _dept_key(row)
        departments[key] = row["department_ref__name"] or "Unassigned"
        department_of[pk] = key
        floors[pk] = _floor(row)

    level = DEPARTMENT if (employee_ids or department) else ORG
    return Scope(level=level, employee_ids=tuple(employee_pks), departments=departments,
                 department_of=department_of, floors=floors,
                 own_department_id=next(iter(departments)) if len(departments) == 1 else None,
                 named_departments=True,
                 # Phase S3: a scope rebuilt from report params runs in a
                 # background thread, so the tenant comes from context -- which
                 # the report runner enters explicitly.
                 organization_token=_tenant_token())


def resolve_org_scope():
    """Every active employee, regardless of who is asking.

    Used for ONE purpose: giving a department head the organisation average and
    their own percentile rank without showing them another department's figures.
    A single org-wide aggregate is not a disclosure of anyone's department; the
    per-department rows built from this scope are redacted before they leave
    ``metrics.departments`` (see ``redact``).
    """
    rows = list(User.objects.filter(is_active=True).values(
        "id", "department_ref_id", "department_ref__name",
        "date_joined", "date_of_joining"))

    employee_ids, departments, department_of, floors = [], {}, {}, {}
    for row in rows:
        pk = row["id"]
        employee_ids.append(pk)
        key = _dept_key(row)
        departments[key] = row["department_ref__name"] or "Unassigned"
        department_of[pk] = key
        floors[pk] = _floor(row)

    return Scope(level=ORG, employee_ids=tuple(employee_ids), departments=departments,
                 department_of=department_of, floors=floors, named_departments=True,
                 organization_token=_tenant_token())


def _dept_key(row):
    return str(row["department_ref_id"]) if row["department_ref_id"] else UNASSIGNED


def _match_department(rows, value):
    """Resolve a `?department=` value (uuid or name) against the rows in scope.

    Returns the department key, or None when it is outside this user's scope --
    which the caller turns into "your own scope, unchanged", never a 403.
    """
    needle = str(value).strip().lower()
    if needle in ("unassigned", "none", "null"):
        return UNASSIGNED
    for row in rows:
        key = _dept_key(row)
        name = (row["department_ref__name"] or "").lower()
        if needle in (key.lower(), name):
            return key
    # Codes are not on the User row; one extra query only when a code was passed.
    from leaves.models import Department

    match = Department.objects.filter(code__iexact=value).values_list("id", flat=True).first()
    if match is None:
        return None
    key = str(match)
    return key if any(_dept_key(row) == key for row in rows) else None


def _floor(row):
    """Earliest date attendance can exist for this employee.

    Mirrors ``attendance.services.absent_floor`` exactly -- later of account
    registration and date_of_joining -- but reads the two raw values off a
    ``values()`` row so resolving a whole organisation costs no extra queries.
    """
    from django.utils import timezone

    from attendance.services import attendance_tracking_start

    joined = row.get("date_joined")
    registration = timezone.localtime(joined).date() if joined else None
    date_of_joining = row.get("date_of_joining")

    if registration and date_of_joining:
        floor = max(registration, date_of_joining)
    else:
        floor = registration or date_of_joining

    start = attendance_tracking_start()
    if floor and start:
        return max(floor, start)
    return floor or start
