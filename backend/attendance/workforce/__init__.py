"""Workforce management layer (Phase 9).

Phase 8 finished the attendance *engine*: raw punches become a policy-aware
daily record. This package is the *workflow* layer on top of it — the parts
people interact with rather than the parts that compute.

    models.py       AttendanceCorrectionRequest — the one new table
    aggregates.py   every dashboard number, in one role-agnostic place
    dashboards.py   employee / manager / HR views over those aggregates
    corrections.py  the employee -> department head -> HR approval workflow
    conflicts.py    approved leave that collides with recorded attendance
    routing.py      who approves for whom

Two rules this package exists to keep:

* **It never recomputes status.** Everything reads ``Attendance.status`` or
  calls ``resolve_day_status``, so the NIF arrival rules apply identically here,
  in the calendar, in reports and on the device path. Applying a correction goes
  through ``services.recompute_status`` — the same single seam as every other
  write.
* **It never writes from a dashboard.** Dashboards are strictly read-side, so no
  amount of viewing can damage data.
"""
