#!/usr/bin/env sh
# Encrypted, off-host PostgreSQL backup for the NIF Office Management System.
# Runs daily from the `cron` container (see backend/deploy/crontab).
#
# Pipeline:  pg_dump (custom format) -> gzip -> AES-256 (openssl) -> off-host copy
#            -> retention rotation.
#
# Required env (from .env):
#   DATABASE_HOST DATABASE_PORT DATABASE_NAME DATABASE_USER DATABASE_PASSWORD
#   BACKUP_ENCRYPTION_KEY      strong passphrase for AES-256 (KEEP OFFLINE/SECRET)
# Optional env:
#   BACKUP_DIR                 local staging dir (default /backups)
#   BACKUP_RETENTION_DAYS      keep N days locally (default 14)
#   BACKUP_S3_BUCKET           s3://bucket/prefix  -> uploaded with `aws s3 cp`
#   BACKUP_RSYNC_TARGET        user@host:/path     -> uploaded with `scp`
# Restore: see deploy/restore.sh / docs/BACKUP.md.
set -eu

# Phase 11 (audit finding H4): a failure anywhere below records a FAILED
# heartbeat before exiting, so a broken backup becomes a red tile and an alert
# instead of a log line nobody reads. Previously an unset encryption key made
# this script abort every single night in silence, and the first anyone would
# have learned of it was a restore with nothing to restore.
fail() {
  echo "[backup] FAILED: $1"
  # Remove the half-written artifact. openssl creates the output file before
  # the pipeline feeding it has finished, so an abort leaves a truncated file
  # with no .sha256 beside it - and backup_verify picks the NEWEST artifact, so
  # that stub would be the one tomorrow's restore drill tried to verify, while
  # the good backup from the night before sat there untouched and unchecked.
  [ -n "${OUT:-}" ] && [ -f "${OUT:-}" ] && rm -f "$OUT" "${OUT}.sha256"
  cd /app && python manage.py record_heartbeat BACKUP --failed --detail "$1" || true
  exit 1
}
# ${LINENO-unknown}, not $LINENO. These scripts run under /bin/sh, which on
# Debian is dash, and dash has no LINENO. With `set -u` the bare form made the
# trap itself die with "LINENO: parameter not set" BEFORE fail() was reached, so
# an unexpected abort - pg_dump erroring, openssl erroring, the disk filling -
# recorded no heartbeat at all and the health tile stayed green on yesterday's
# run. That is precisely the silent failure this trap was added to end. bash
# still reports the line; dash now says "unknown" and, crucially, still alerts.
trap 'fail "aborted unexpectedly at line ${LINENO-unknown}"' EXIT INT TERM

[ -n "${BACKUP_ENCRYPTION_KEY:-}" ] || fail "BACKUP_ENCRYPTION_KEY is not set"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION="${BACKUP_RETENTION_DAYS:-14}"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
OUT="${BACKUP_DIR}/nif-${DATABASE_NAME:-leave_system}-${STAMP}.dump.gz.enc"
mkdir -p "$BACKUP_DIR"

export PGPASSWORD="${DATABASE_PASSWORD}"
echo "[backup] dumping ${DATABASE_NAME} ..."
pg_dump -h "${DATABASE_HOST:-db}" -p "${DATABASE_PORT:-5432}" -U "${DATABASE_USER}" \
        -F c "${DATABASE_NAME}" \
  | gzip -9 \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY -out "$OUT"
SIZE="$(wc -c < "$OUT")"
echo "[backup] wrote $OUT (${SIZE} bytes)"

# A dump this small is not a database, it is an error that exited 0. Catching it
# here means the heartbeat reflects reality rather than "the script finished".
[ "$SIZE" -gt 1024 ] || fail "dump is only ${SIZE} bytes — almost certainly empty"

# Checksum beside the artifact so a truncated transfer is detectable off-host
# without needing the decryption passphrase there.
sha256sum "$OUT" > "${OUT}.sha256" 2>/dev/null || shasum -a 256 "$OUT" > "${OUT}.sha256"

# --- Off-host copy (choose whichever target env is set) ---
OFFHOST="none"
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  echo "[backup] uploading to ${BACKUP_S3_BUCKET} ..."
  aws s3 cp "$OUT" "${BACKUP_S3_BUCKET}/"
  aws s3 cp "${OUT}.sha256" "${BACKUP_S3_BUCKET}/"
  OFFHOST="s3"
elif [ -n "${BACKUP_RSYNC_TARGET:-}" ]; then
  echo "[backup] copying to ${BACKUP_RSYNC_TARGET} ..."
  scp -o StrictHostKeyChecking=accept-new "$OUT" "${OUT}.sha256" "${BACKUP_RSYNC_TARGET}/"
  OFFHOST="rsync"
else
  echo "[backup] WARNING: no BACKUP_S3_BUCKET / BACKUP_RSYNC_TARGET set — backup is LOCAL ONLY (not off-host)."
  if [ "${BACKUP_REQUIRE_OFFHOST:-1}" = "1" ]; then
    fail "no off-host target configured; a local-only copy is not a backup"
  fi
fi

# --- Retention: delete local encrypted backups older than N days ---
find "$BACKUP_DIR" -name 'nif-*.dump.gz.enc*' -type f -mtime "+${RETENTION}" -delete 2>/dev/null || true
echo "[backup] done. Local retention: ${RETENTION} days."

trap - EXIT INT TERM
cd /app && python manage.py record_heartbeat BACKUP --ok \
  --detail "${SIZE} bytes off-host=${OFFHOST}" || true
