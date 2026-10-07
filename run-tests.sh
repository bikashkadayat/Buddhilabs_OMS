#!/usr/bin/env bash
#
# Run the backend suite the way CI runs it: against PostgreSQL, not SQLite.
#
#   ./run-tests.sh                    # full backend suite on Postgres
#   ./run-tests.sh --migrations-only  # just apply migrations to an empty DB (fast)
#   ./run-tests.sh minutes/tests      # pass any pytest args through
#
# WHY THIS EXISTS
# ---------------
# run-local.sh runs the app on SQLite, because that is where this machine's
# imported biometric history lives. CI (.github/workflows/ci.yml) runs pytest on
# postgres:14-alpine. The two databases do not fail in the same places, and the
# gap is not theoretical — it has already cost one red build:
#
#   minutes/0003 added a column with db_index=True and later set unique=True.
#   On Postgres an indexed varchar also gets a companion `..._like` index, and
#   AddField defers that CREATE INDEX to the end of the migration, where it
#   collides with the one unique=True already created:
#     relation "minutes_minuteactionitem_reference_50fc12d3_like" already exists
#   SQLite emits no `_like` index at all, so it passed locally and broke in CI.
#
# Migrations failing means pytest-django cannot build the test database, so the
# whole backend job collapses rather than reporting a few failures. --migrations-only
# catches exactly that in ~30s and is the cheap check worth running before a push.
#
# Nothing here touches .env, .env.local or backend/db.sqlite3. The container is
# thrown away on exit; the dev database is never opened.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Matches the image CI uses. Keep in sync with ci.yml's `services.postgres.image`.
PG_IMAGE="postgres:14-alpine"
CONTAINER="nif-test-pg"
# Not 5432/5434: 5432 is often taken by a host Postgres and 5434 is the dev
# compose stack. This port belongs to the throwaway test database only.
PG_PORT="${PG_PORT:-55433}"

MIGRATIONS_ONLY=0
PYTEST_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --migrations-only) MIGRATIONS_ONLY=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) PYTEST_ARGS+=("$arg") ;;
  esac
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker is required to run the test database"
VENV="$REPO/backend/.venv"
[[ -x "$VENV/bin/python" ]] || die "no virtualenv at $VENV — see docs/LOCAL_RUNBOOK.md §3"

# Always start from an empty database. A reused one hides exactly the class of
# bug this script exists to catch: a migration that only fails on first apply.
say "starting $PG_IMAGE on $PG_PORT"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" \
  -e POSTGRES_DB=leave_system \
  -e POSTGRES_USER=leave_user \
  -e POSTGRES_PASSWORD=postgres \
  -p "$PG_PORT:5432" "$PG_IMAGE" >/dev/null

cleanup() {
  # Capture the status FIRST: the cleanup below ends in `|| true`, and on the
  # fatal-error path (e.g. `set -u`) that became the script's exit status, so a
  # run that never reached pytest still reported success. Re-exit with what
  # actually happened.
  local rc=$?
  trap - INT TERM EXIT
  say "removing test database container"
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  exit "$rc"
}
trap cleanup INT TERM EXIT

for _ in $(seq 1 60); do
  docker exec "$CONTAINER" pg_isready -U leave_user -d leave_system >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U leave_user -d leave_system >/dev/null 2>&1 \
  || die "postgres did not become ready"

# DJANGO_DEBUG=True keeps settings.py off the "must set a real secret" path; this
# database is empty, thrown away, and reachable only on loopback.
export DJANGO_SECRET_KEY=test-only-not-a-real-secret
export DJANGO_DEBUG=True
export DATABASE_ENGINE=postgresql
export DATABASE_HOST=127.0.0.1
export DATABASE_PORT="$PG_PORT"
export DATABASE_NAME=leave_system
export DATABASE_USER=leave_user
export DATABASE_PASSWORD=postgres
export EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend

cd "$REPO/backend"

if [[ "$MIGRATIONS_ONLY" == "1" ]]; then
  say "applying every migration to an empty PostgreSQL database"
  "$VENV/bin/python" manage.py migrate --no-input
  say "migrations OK on PostgreSQL"
  exit 0
fi

say "ruff"
"$VENV/bin/ruff" check .

say "migration drift"
"$VENV/bin/python" manage.py makemigrations --check --dry-run

say "migrations on an empty PostgreSQL database"
"$VENV/bin/python" manage.py migrate --no-input >/dev/null

say "pytest on PostgreSQL (${#PYTEST_ARGS[@]} extra args)"
# ${arr[@]} on an EMPTY array is an "unbound variable" fatal error under
# `set -u` in bash < 4.4 — and macOS still ships bash 3.2 as /bin/bash, which is
# what `#!/usr/bin/env bash` resolves to here. So `./run-tests.sh` with no extra
# args died before pytest ever started. The ${arr[@]+"${arr[@]}"} form expands to
# nothing when the array is empty and is safe on every bash.
"$VENV/bin/python" -m pytest ${PYTEST_ARGS[@]+"${PYTEST_ARGS[@]}"}
