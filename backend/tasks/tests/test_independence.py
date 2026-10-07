"""
The module boundary, enforced as a test rather than as a promise.

Phase T1's first instruction is that Task Management stays completely
independent: no integration changes, no workflow modifications, no dependencies,
no imports across the boundary to memos, minutes, circulars, leaves, attendance,
inventory, reports or analytics.

That is easy to state and easy to violate six months later with one convenient
import. So it is pinned here, both directions, by reading the source: this app
must not import them, and they must not import this one. A boundary nothing
checks is a boundary that has already been crossed.
"""
import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[2]
TASKS = BACKEND / "tasks"

# The modules the instruction names. `leaves` appears here too — Task.department
# names "leaves.Department" as a LAZY STRING, which Django resolves through the
# app registry with no Python import, and that is the only tie.
FORBIDDEN = {
    "memos", "minutes", "circulars", "leaves", "attendance", "inventory",
    "reports", "analytics",
}

# Shared project plumbing, which is not a business module and is used by every
# app in the codebase.
#
# `monitoring` was added in Phase T4: the heartbeat registry is infrastructure
# every scheduled command in this codebase reports to (drafts, reports and
# biometric all import it), and monitoring's OWN test refuses a new cron line
# that is not registered there — "add the new job to monitoring.heartbeat.
# CRON_JOBS, or it ships unmonitored". Not importing it would mean shipping a
# critical scheduled job with no health check, which is a worse outcome than a
# dependency on a health-check registry.
#
# The FORBIDDEN set below is unchanged and is the boundary that actually
# matters: no business module, in either direction.
#
# `evidence` was added in Phase T6: it is a CONTRACT app — schema dataclasses and
# a registry, with no models, no migrations, no views and, critically, no import
# of any business module. The dependency points from `tasks` to the contract and
# never back, which is what lets the six sources that are not yet integrated stay
# untouched. A registry that lived inside `tasks` instead would have made every
# other module depend on the task module in order to publish evidence.
# `tenancy` joined this list in Phase S2, and it belongs here for the same
# reason `config` and `evidence` do: it is foundation plumbing that imports NO
# business module, so the dependency points one way only. tasks/services.py
# needs it to resolve which organization a task number is being minted for, and
# tasks/models.py needs the Organization foreign key -- neither of which makes
# this app depend on another FEATURE.
SHARED = {"audit", "config", "notifications", "users", "documents", "common",
          "monitoring", "evidence", "tenancy"}


def imported_top_level_modules(path):
    """Every top-level name this file imports, from both import forms."""
    tree = ast.parse(path.read_text(), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (level > 0) are within this app by definition.
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    return found


def source_files(root):
    return [p for p in root.rglob("*.py")
            if "__pycache__" not in p.parts and "migrations" not in p.parts]


@pytest.mark.parametrize("path", source_files(TASKS),
                         ids=lambda p: str(p.relative_to(TASKS)))
def test_no_task_module_imports_a_business_module(path):
    crossings = imported_top_level_modules(path) & FORBIDDEN
    assert not crossings, (
        f"{path.relative_to(BACKEND)} imports {sorted(crossings)}, which Phase T1 "
        f"forbids. Reference another app's model by lazy string "
        f'("app.Model") or through django.apps.apps.get_model instead.')


def test_no_other_module_imports_tasks():
    """
    The other direction. Nothing outside this app may depend on it, so the whole
    module can be removed by deleting the directory and its three registration
    lines.
    """
    offenders = []
    for app in FORBIDDEN:
        root = BACKEND / app
        if not root.exists():
            continue
        for path in source_files(root):
            if "tasks" in imported_top_level_modules(path):
                offenders.append(str(path.relative_to(BACKEND)))
    assert not offenders, f"These modules import tasks: {offenders}"


def test_the_only_cross_app_model_reference_is_a_lazy_string():
    """
    `Task.department` points at leaves.Department. It must stay a string, so the
    leave module is resolved through the app registry rather than imported.
    """
    from tasks.models import Task

    field = Task._meta.get_field("department")
    assert field.remote_field.model._meta.label == "leaves.Department"
    assert "leaves" not in imported_top_level_modules(TASKS / "models.py")


def test_every_non_shared_import_is_accounted_for():
    """
    A catch-all: anything this app imports that is neither the standard library,
    a third-party package, this app itself, nor known shared plumbing, is a new
    dependency somebody should have to justify in review.
    """
    local_apps = {p.name for p in BACKEND.iterdir()
                  if p.is_dir() and (p / "__init__.py").exists()}
    surprises = set()
    for path in source_files(TASKS):
        for name in imported_top_level_modules(path):
            if name in local_apps and name not in SHARED and name != "tasks":
                surprises.add(name)
    assert not surprises, f"Unjustified cross-app imports: {sorted(surprises)}"


def test_the_evidence_contract_imports_no_business_module():
    """
    The registry's whole value is that it knows about nobody. If `evidence` ever
    imports a business module, the dependency has inverted and the six
    un-integrated sources are no longer free to stay that way.
    """
    contract = BACKEND / "evidence"
    for path in source_files(contract):
        crossings = imported_top_level_modules(path) & (FORBIDDEN | {"tasks"})
        assert not crossings, (
            f"{path.relative_to(BACKEND)} imports {sorted(crossings)} — the "
            f"evidence registry must depend on nothing.")


def test_the_evidence_contract_has_no_models():
    """
    A contract app with a model is a data-owning app wearing a contract's name,
    and the next phase will be tempted to put a score in it.
    """
    from django.apps import apps

    assert list(apps.get_app_config("evidence").get_models()) == []
