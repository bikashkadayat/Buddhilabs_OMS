"""
Generate one sample of every report into docs/sample-reports/.

Usage (from backend/, against a throwaway DB):
    DATABASE_ENGINE=sqlite3 python manage.py shell < scripts/generate_sample_reports.py
"""
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
try:
    django.setup()
except Exception:
    pass

from django.conf import settings  # noqa: E402
from leaves.models import Department, LeaveType, Leave  # noqa: E402
from leaves import services  # noqa: E402
from users.models import User  # noqa: E402
from reports import report_service  # noqa: E402
from reports.models import ReportType  # noqa: E402

OUT = Path(settings.BASE_DIR).parent / "docs" / "sample-reports"
OUT.mkdir(parents=True, exist_ok=True)
YEAR = 2026
MON = date.fromisocalendar(YEAR, 24, 1)

dept, _ = Department.objects.get_or_create(code="ENG", defaults={"name": "Engineering"})
annual = LeaveType.objects.get(code="ANNUAL")

for i in range(3):
    u, _ = User.objects.get_or_create(
        username=f"sample{i}", defaults={"email": f"sample{i}@nif.test", "role": "maker", "department": "ENG"},
    )
    u.first_name, u.last_name, u.department = ["Asha", "Bikash", "Chitra"][i], "Sample", "ENG"
    u.save()
    leave = Leave.objects.create(user=u, leave_type="annual", reason="Sample", start_date=MON, end_date=MON + timedelta(days=2 + i))
    leave.status = Leave.Status.APPROVED
    leave.save()
    services.adjust_leave_balance(u, annual, YEAR, Decimal("2.00"), actor=u, reason="Sample bonus grant")

specs = [
    (ReportType.EMPLOYEE_REGISTER, {"year": YEAR}),
    (ReportType.MONTHLY_ATTENDANCE, {"year": YEAR, "format": "excel"}),
    (ReportType.MONTHLY_ATTENDANCE, {"year": YEAR, "format": "pdf"}),
    (ReportType.LEAVE_UTILIZATION, {"year": YEAR}),
    (ReportType.COMPLIANCE, {"year": YEAR}),
    (ReportType.AUDIT_TRAIL, {}),
]
for report_type, params in specs:
    content, filename, _ct = report_service.build_report_file(report_type, params)
    (OUT / filename).write_bytes(content)
    print(f"wrote {filename} ({len(content)} bytes)")

print("Samples written to", OUT)
