"""Prove the latest backup is restorable, every night.

Phase 11 audit finding H4. The backup ran daily and nothing ever checked it, so
the most likely disaster-recovery outcome was discovering at restore time that
there was nothing to restore. A backup that has never been restored is not a
backup; it is a hypothesis.

This performs a REAL restore into a scratch database, asserts the restored data
looks like production, then drops the scratch database. Success writes a
``SYSTEM_BACKUP_VERIFIED_OK`` heartbeat; anything else writes FAILED, which the
alert rules escalate as Critical.

    python manage.py backup_verify                  # newest artifact
    python manage.py backup_verify --file <path>
    python manage.py backup_verify --keep           # leave the scratch DB up

Deliberately NOT run against the production database. The scratch database is
created and dropped by this command, so a corrupt artifact cannot touch
anything real -- the same reasoning behind ``restore.sh`` defaulting to a
scratch target.
"""
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from monitoring import heartbeat

# Tables checked to prove the restore produced real data and not a shell.
#
# These were chosen on the assumption that every live deployment has rows in all
# three. That is not true before the attendance terminal goes live:
# attendance_attendance is legitimately empty on a fresh deployment, and the
# drill failed nightly with "the artifact is a shell" against a restore that was
# in fact perfect. A critical alert that is wrong every night is worse than no
# alert, because it is the one people learn to close unread. _compare() now
# asks each table what PRODUCTION holds before deciding what the restore owes.
SENTINEL_TABLES = ("users_user", "attendance_attendance", "audit_auditlog")

# A restored table this much smaller than production means the artifact predates
# a lot of activity, or truncated. Either way it is worth a red heartbeat.
MIN_RATIO = 0.8


class Command(BaseCommand):
    help = "Restore the latest backup into a scratch database and verify it."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Artifact to verify (default: newest).")
        parser.add_argument("--backup-dir", default=os.getenv("BACKUP_DIR", "/backups"))
        parser.add_argument("--keep", action="store_true",
                            help="Do not drop the scratch database afterwards.")


    @staticmethod
    def _require_cross_tenant_visibility(connection):
        """Fail loudly if this role cannot see across tenants.

        PostgreSQL only. On SQLite there is no RLS, so nothing to check.
        """
        if connection.vendor != "postgresql":
            return
        from django.core.management.base import CommandError

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT rolbypassrls, rolsuper FROM pg_roles "
                "WHERE rolname = current_user")
            row = cursor.fetchone()
        bypass, superuser = (row or (False, False))
        if bypass or superuser:
            return
        raise CommandError(
            "backup_verify counts rows on tables protected by row-level "
            "security, and this connection's role can neither bypass nor is "
            "superuser. Every count would return 0 and the verification would "
            "pass for the wrong reason. Re-run as the admin or migration "
            "database role (see tenancy/rls.py)."
        )

    def handle(self, *args, **options):
        try:
            self._verify(options)
        except Exception as exc:  # noqa: BLE001
            heartbeat.record("BACKUP_VERIFIED", ok=False,
                             detail=f"{type(exc).__name__}: {exc}")
            raise

    # -- steps ------------------------------------------------------------
    def _verify(self, options):
        db = settings.DATABASES["default"]
        if "postgresql" not in db["ENGINE"]:
            raise CommandError(
                "backup_verify only supports PostgreSQL; this deployment is on "
                f"{db['ENGINE']}.")

        key = os.getenv("BACKUP_ENCRYPTION_KEY")
        if not key:
            raise CommandError("BACKUP_ENCRYPTION_KEY is not set — cannot decrypt.")

        artifact = self._pick_artifact(options)
        self.stdout.write(f"[verify] artifact: {artifact}")

        self._check_checksum(artifact)

        scratch = f"{db['NAME']}_verify"
        env = {**os.environ, "PGPASSWORD": db["PASSWORD"],
               "BACKUP_ENCRYPTION_KEY": key}
        conn = ["-h", db["HOST"], "-p", str(db["PORT"]), "-U", db["USER"]]

        self.stdout.write(f"[verify] restoring into scratch database {scratch} ...")
        self._run(["dropdb", *conn, "--if-exists", scratch], env)
        self._run(["createdb", *conn, scratch], env)

        try:
            self._restore(artifact, scratch, conn, env)
            counts = self._counts(scratch, conn, env)
            self._compare(counts)
        finally:
            if not options["keep"]:
                self._run(["dropdb", *conn, "--if-exists", scratch], env)
                self.stdout.write("[verify] scratch database dropped.")

        detail = {"artifact": Path(artifact).name, **counts}
        heartbeat.record("BACKUP_VERIFIED", ok=True, detail=detail)
        self.stdout.write(self.style.SUCCESS(
            f"[verify] PASS — {artifact} is restorable. {counts}"))

    def _run(self, command, env):
        """Run one helper binary (dropdb/createdb), failing loudly.

        This helper was referenced by _verify but never written, so the nightly
        drill died with AttributeError at its first dropdb - every night, on
        every deployment, since the drill was added. The FAILED heartbeat made
        that look like an unrestorable backup rather than a missing method, and
        the artifact itself was never actually tested.

        A silent failure here is the worst of the options: a dropdb that did not
        drop leaves the previous scratch database in place, and the drill then
        "verifies" yesterday's rows instead of today's artifact.
        """
        result = subprocess.run(command, capture_output=True, env=env, text=True)
        if result.returncode != 0:
            raise CommandError(
                f"{command[0]} failed: "
                f"{(result.stderr or result.stdout).strip()[:300]}")

    def _pick_artifact(self, options):
        if options["file"]:
            path = Path(options["file"])
            if not path.exists():
                raise CommandError(f"{path} does not exist.")
            return str(path)

        directory = Path(options["backup_dir"])
        if not directory.exists():
            raise CommandError(f"Backup directory {directory} does not exist.")
        artifacts = sorted(directory.glob("nif-*.dump.gz.enc"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        if not artifacts:
            raise CommandError(
                f"No database backup found in {directory}. The backup job has "
                f"produced nothing — this is the failure H4 was about.")
        return str(artifacts[0])

    def _check_checksum(self, artifact):
        """Verify the sidecar checksum if one exists. Missing is tolerated
        (older artifacts predate it); mismatched never is."""
        sidecar = Path(f"{artifact}.sha256")
        if not sidecar.exists():
            self.stdout.write(self.style.WARNING(
                "[verify] no .sha256 sidecar — skipping integrity check."))
            return
        import hashlib

        digest = hashlib.sha256()
        with open(artifact, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        expected = sidecar.read_text().split()[0]
        if digest.hexdigest() != expected:
            raise CommandError(
                "Checksum MISMATCH — the artifact is corrupt or truncated.")
        self.stdout.write("[verify] checksum OK.")

    def _restore(self, artifact, scratch, conn, env):
        decrypt = subprocess.Popen(
            ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
             "-pass", "env:BACKUP_ENCRYPTION_KEY", "-in", artifact],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        gunzip = subprocess.Popen(
            ["gunzip"], stdin=decrypt.stdout, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env)
        decrypt.stdout.close()
        restore = subprocess.Popen(
            ["pg_restore", *conn, "-d", scratch, "--no-owner", "--clean",
             "--if-exists"],
            stdin=gunzip.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env)
        gunzip.stdout.close()
        _out, err = restore.communicate()

        if decrypt.wait() != 0:
            raise CommandError(
                "Decryption failed — wrong BACKUP_ENCRYPTION_KEY, or the "
                "artifact is not what it claims to be.")
        # pg_restore warns about pre-existing objects on --clean; only a
        # non-zero exit is a real failure.
        if restore.returncode != 0:
            raise CommandError(f"pg_restore failed: {err.decode()[:500]}")

    def _counts(self, database, conn, env):
        counts = {}
        for table in SENTINEL_TABLES:
            result = subprocess.run(
                ["psql", *conn, "-d", database, "-t", "-A", "-c",
                 f"SELECT COUNT(*) FROM {table}"],
                capture_output=True, env=env, text=True)
            if result.returncode != 0:
                raise CommandError(
                    f"Restored database has no usable {table}: "
                    f"{result.stderr[:300]}")
            counts[table] = int(result.stdout.strip() or 0)
        return counts

    def _compare(self, counts):
        """A restore that produces an empty schema exits 0 and looks fine.

        Every sentinel is judged against what production actually holds. A table
        that is empty in production cannot tell us anything about the artifact,
        so it is skipped and said out loud rather than counted as a failure -
        see the note on SENTINEL_TABLES. A table that HAS rows in production and
        none in the restore is the shell case, and is still fatal.
        """
        from django.db import connection

        # REFUSE TO RUN UNDER AN RLS-CONSTRAINED ROLE (Phase S5).
        #
        # These COUNT(*)s are on business tables, and Phase S5 put a
        # row-level-security policy on every one of them. Connected as the
        # APPLICATION role with no `app.current_org` set, each count comes back
        # as 0 -- and this command would then cheerfully report that every
        # table is "empty in production too" and that the backup proves
        # nothing. A backup verifier that silently passes is worse than one
        # that fails.
        #
        # So it checks. Row counting across every tenant is a platform-level
        # integrity question, and it must run as the admin or migration role
        # (BYPASSRLS) -- never as the application role.
        self._require_cross_tenant_visibility(connection)

        checked = []
        for table, restored in counts.items():
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                live = cursor.fetchone()[0]
            if not live:
                self.stdout.write(self.style.WARNING(
                    f"[verify] {table} is empty in production too — it proves "
                    f"nothing about this artifact, so it is not checked."))
                continue
            if restored == 0:
                raise CommandError(
                    f"{table} restored with ZERO rows against {live} live — "
                    f"the artifact is a shell.")
            if restored < live * MIN_RATIO:
                raise CommandError(
                    f"{table}: restored {restored} rows against {live} live "
                    f"(< {MIN_RATIO:.0%}). The artifact is stale or truncated.")
            checked.append(table)

        # If production is empty everywhere we looked, the drill asserted
        # nothing at all, and reporting a PASS would be a lie of omission.
        if not checked:
            raise CommandError(
                "No sentinel table has any rows in production, so this drill "
                "could not verify anything. Check the database is the right "
                f"one before trusting the backup. Tables: {list(counts)}")
        self.stdout.write(
            f"[verify] row counts consistent with production ({', '.join(checked)}).")
