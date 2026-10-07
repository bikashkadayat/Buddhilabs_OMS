"""Configurable attendance policy engine (Phase 8).

Replaces the five hardcoded ``ATTENDANCE_*`` settings with admin-editable,
date-effective rules resolved per employee:

    User assignment -> Department assignment -> Global assignment -> settings

The settings tier is a real fallback, not a migration artefact: ``resolve_policy``
never returns ``None``, so a fresh database, a flushed test transaction or a
deleted policy row all degrade to today's behaviour instead of raising.

Layout:
    models.py    the five new tables
    resolver.py  which policy/shift applies to whom, and when
    engine.py    what a day's punches mean under that policy
    comp_off.py  the only writer of CompensatoryLedger.Source.ATTENDANCE
"""
