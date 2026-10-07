#!/usr/bin/env sh
# Restore a NIF encrypted backup produced by deploy/backup.sh.
# Usage:  BACKUP_ENCRYPTION_KEY=... deploy/restore.sh <file.dump.gz.enc> [target_db]
# Restores into `target_db` (default: a scratch DB "<name>_restore" so you can
# verify BEFORE overwriting production). See docs/BACKUP.md for the full runbook.
set -eu

FILE="${1:?usage: restore.sh <file.dump.gz.enc> [target_db]}"
TARGET_DB="${2:-${DATABASE_NAME:-leave_system}_restore}"
: "${BACKUP_ENCRYPTION_KEY:?set BACKUP_ENCRYPTION_KEY (the backup passphrase)}"
export PGPASSWORD="${DATABASE_PASSWORD}"
HOST="${DATABASE_HOST:-db}"; PORT="${DATABASE_PORT:-5432}"; USER="${DATABASE_USER}"

echo "[restore] (re)creating scratch DB ${TARGET_DB} ..."
dropdb   -h "$HOST" -p "$PORT" -U "$USER" --if-exists "$TARGET_DB"
createdb -h "$HOST" -p "$PORT" -U "$USER" "$TARGET_DB"

echo "[restore] decrypting + restoring into ${TARGET_DB} ..."
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY -in "$FILE" \
  | gunzip \
  | pg_restore -h "$HOST" -p "$PORT" -U "$USER" -d "$TARGET_DB" --no-owner --clean --if-exists

echo "[restore] done. Verify ${TARGET_DB}, then promote if correct."
