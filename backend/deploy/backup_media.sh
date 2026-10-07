#!/usr/bin/env sh
# Encrypted, off-host MEDIA backup for the NIF Office Management System.
#
# Phase 11 audit finding H3. Until now only PostgreSQL was backed up. The
# media volume holds memo attachments and vouchers, attendance-correction
# evidence, profile photos and generated reports — and the database stores only
# the PATHS to those files. So a media loss left the system looking perfectly
# intact while every single download 404'd, which is a worse failure mode than
# an obvious outage.
#
# Pipeline:  tar (full or incremental) -> gzip -> AES-256 -> off-host -> rotate
#
# Required env:
#   BACKUP_ENCRYPTION_KEY      same passphrase as the database backup
# Optional env:
#   MEDIA_ROOT                 default /app/media
#   BACKUP_DIR                 default /backups
#   BACKUP_RETENTION_DAYS      default 14
#   BACKUP_MEDIA_INCLUDE_REPORTS  default 0 — generated reports are regenerable
#                              and already purged after 30 days, so they are
#                              excluded to keep the artifact small
#   BACKUP_S3_BUCKET / BACKUP_RSYNC_TARGET   off-host destination
#
# Usage:  backup_media.sh [full|incremental]
set -eu

# Failure heartbeat, mirroring backup.sh. Without it this script exited 1 and
# told nobody: the last heartbeat stayed OK from the previous night, so the
# health tile only turned amber once the run went 26 hours stale and red at 48.
# The database backup reports its own failures the same night, and there is no
# reason media should be found out a day and a half later - media loss is the
# quieter failure of the two, because the database still holds every path and
# the system looks intact until somebody clicks a download.
fail() {
  echo "[media-backup] FAILED: $1"
  # Remove the half-written artifact. openssl creates the output file before
  # the pipeline feeding it has finished, so an abort leaves a truncated file
  # with no .sha256 beside it - and backup_verify picks the NEWEST artifact, so
  # that stub would be the one tomorrow's restore drill tried to verify, while
  # the good backup from the night before sat there untouched and unchecked.
  [ -n "${OUT:-}" ] && [ -f "${OUT:-}" ] && rm -f "$OUT" "${OUT}.sha256"
  cd /app && python manage.py record_heartbeat BACKUP_MEDIA --failed --detail "$1" || true
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
MODE="${1:-incremental}"
MEDIA_ROOT="${MEDIA_ROOT:-/app/media}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION="${BACKUP_RETENTION_DAYS:-14}"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
SNAPSHOT="${BACKUP_DIR}/media.snar"
OUT="${BACKUP_DIR}/nif-media-${MODE}-${STAMP}.tar.gz.enc"

mkdir -p "$BACKUP_DIR"

if [ ! -d "$MEDIA_ROOT" ]; then
  fail "${MEDIA_ROOT} does not exist"
fi

# A full run resets the incremental snapshot, so each week starts a fresh chain.
# Without this the chain grows forever and a restore needs every artifact ever
# written — which is how incremental backups quietly become unrestorable.
if [ "$MODE" = "full" ]; then
  rm -f "$SNAPSHOT"
fi

EXCLUDES=""
if [ "${BACKUP_MEDIA_INCLUDE_REPORTS:-0}" != "1" ]; then
  EXCLUDES="--exclude=./reports"
fi

echo "[media-backup] ${MODE} backup of ${MEDIA_ROOT} ..."
# --listed-incremental writes/reads the snapshot file; with a fresh snapshot
# (full mode) tar archives everything, otherwise only what changed.
tar --listed-incremental="$SNAPSHOT" \
    $EXCLUDES \
    -C "$MEDIA_ROOT" -cf - . \
  | gzip -9 \
  | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_ENCRYPTION_KEY -out "$OUT"

SIZE="$(wc -c < "$OUT")"
echo "[media-backup] wrote $OUT (${SIZE} bytes)"

# An archive this small is not 300MB of attachments, it is an error that exited
# 0 somewhere in the tar|gzip|openssl pipeline. Same guard as backup.sh.
[ "$SIZE" -gt 1024 ] || fail "archive is only ${SIZE} bytes - almost certainly empty"

# Checksum beside the artifact, so a truncated transfer is detectable without
# decrypting (which needs the passphrase and therefore cannot be automated on
# the off-host side).
sha256sum "$OUT" > "${OUT}.sha256" 2>/dev/null || shasum -a 256 "$OUT" > "${OUT}.sha256"

# --- Off-host copy ---
OFFHOST="none"
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  aws s3 cp "$OUT" "${BACKUP_S3_BUCKET}/"
  aws s3 cp "${OUT}.sha256" "${BACKUP_S3_BUCKET}/"
  OFFHOST="s3"
elif [ -n "${BACKUP_RSYNC_TARGET:-}" ]; then
  scp -o StrictHostKeyChecking=accept-new "$OUT" "${OUT}.sha256" \
      "${BACKUP_RSYNC_TARGET}/"
  OFFHOST="rsync"
else
  echo "[media-backup] WARNING: no off-host target — LOCAL ONLY, which is not a backup."
fi

# Retention. Full artifacts are kept longer: an incremental is worthless without
# the full it chains from, so expiring them on the same clock would leave a
# window where the newest recoverable point is older than the oldest artifact.
find "$BACKUP_DIR" -name 'nif-media-incremental-*' -type f -mtime "+${RETENTION}" -delete 2>/dev/null || true
find "$BACKUP_DIR" -name 'nif-media-full-*' -type f -mtime "+$((RETENTION * 3))" -delete 2>/dev/null || true

echo "[media-backup] done (${MODE}, off-host=${OFFHOST})."

trap - EXIT INT TERM

# Heartbeat, so a silent failure becomes a red tile and an alert rather than a
# log line nobody reads.
cd /app && python manage.py record_heartbeat BACKUP_MEDIA --ok \
  --detail "${MODE} ${SIZE} bytes off-host=${OFFHOST}" || true
