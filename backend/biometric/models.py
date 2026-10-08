"""Biometric attendance — raw device layer.

This app owns everything that comes off a fingerprint terminal: the device
inventory, the device-user-ID -> employee mapping, the immutable raw punch log
and the per-batch sync audit trail.

It deliberately does NOT own the daily attendance record. ``attendance.Attendance``
stays the single source of truth for "was employee X present on day D" — Phase 5
derives those rows from the punches stored here. That keeps the existing
calendar, PDF reports, leave integration and dashboard working unchanged.
"""
import hashlib
import hmac
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from tenancy.scoping import (AllTenantsManager, TenantManager,
                              TenantQuerySet)

# Mirrors morx/models.py PUNCH_LABELS exactly. The collector sends its own
# punch_label, but we re-derive server-side so a collector of any version can
# never write an unexpected label into our data.
PUNCH_LABELS = {
    0: "check_in",
    1: "check_out",
    2: "break_out",
    3: "break_in",
    4: "overtime_in",
    5: "overtime_out",
}

# Punch codes that start a working period vs. end one. Used by the Phase 5
# derivation engine; defined here so the raw layer stays the one place that
# knows what a punch code means.
ENTRY_PUNCHES = frozenset({0, 3, 4})   # check_in, break_in, overtime_in
EXIT_PUNCHES = frozenset({1, 2, 5})    # check_out, break_out, overtime_out

API_KEY_PREFIX = "nifbio_"
API_KEY_PREFIX_LENGTH = 14  # "nifbio_" + 7 chars — indexed lookup selector


def punch_label(code):
    """Human label for a device punch code, preserving unknown codes."""
    return PUNCH_LABELS.get(code, f"unknown_{code}")


def generate_api_key():
    """A fresh device key. Returned once at creation and never recoverable."""
    return f"{API_KEY_PREFIX}{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key):
    """SHA-256 of a device key.

    A plain digest (not PBKDF2) is the right call here: the key is 32 bytes of
    CSPRNG output, so there is no low-entropy secret to slow an attacker down
    on, and this hash is verified on *every* punch ingest — a deliberately slow
    KDF would make the hot path expensive for no security gain.
    """
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class BiometricDevice(models.Model):
    """A physical fingerprint terminal that reports to this system."""

    class Status(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        UNKNOWN = "unknown", "Unknown"

    class DeviceType(models.TextChoices):
        """Which wire protocol the terminal speaks.

        Only protocols this codebase can actually read are offered. Every
        option here goes through ``zk_client`` -- the ZK binary protocol on
        TCP 4370 -- which ZKTeco and its OEM rebadges (eSSL, Realtime,
        Identix) all share. A vendor with its own protocol (Hikvision,
        Suprema) is not listed because choosing it would save a device that
        can never sync, which is worse than not offering it.
        """
        ZKTECO = "zkteco", "ZKTeco"
        ZK_COMPATIBLE = "zk_compatible", "ZK-compatible (eSSL, Realtime, Identix)"

    class SyncInterval(models.IntegerChoices):
        """How often the scheduler pulls this terminal. 0 = only on demand."""
        MANUAL = 0, "Manual sync only"
        EVERY_5 = 5, "Every 5 minutes"
        EVERY_15 = 15, "Every 15 minutes"
        EVERY_30 = 30, "Every 30 minutes"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="Human-friendly name, e.g. 'Main Gate'.")
    device_type = models.CharField(
        max_length=20, choices=DeviceType.choices, default=DeviceType.ZKTECO,
        db_default=DeviceType.ZKTECO)
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()

    # Short handle for this terminal. `device_sync --device <label>` selects by
    # it, and it is also the join key on a pushed ingest payload — so if
    # anything POSTs to the ingest API, its device label must match this exactly
    # or the punches are rejected.
    label = models.CharField(
        max_length=100,
        help_text="Short handle, e.g. 'main-gate'. Used by `device_sync --device`, "
                  "and must match the device label on any pushed ingest payload.",
    )
    # Not informational since Phase 12: `device_sync` connects to this address.
    host = models.CharField(
        max_length=255, blank=True, default="",
        help_text="The terminal's LAN address. `device_sync` connects to it, so "
                  "it must be reachable from the server.",
    )
    # The identity a PUSH/ADMS terminal presents. It is the ONLY thing in the
    # iClock protocol that says which device is calling, so it doubles as the
    # allow-list key: a serial we do not know is refused rather than trusted.
    serial_number = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="Device serial number (device menu: Info > Device). Required "
                  "for PUSH/ADMS terminals — it is how they identify themselves.",
    )
    # The serial the terminal REPORTS when pulled, recorded on first contact.
    # Separate from `serial_number` on purpose: that field is the PUSH/ADMS
    # credential, and filling it in from a pull would quietly start accepting
    # unauthenticated iClock posts for a device that never asked to push.
    # Unique platform-wide (below), which is what stops a second organization
    # registering the same physical terminal and pulling its attendance.
    hardware_serial = models.CharField(
        max_length=64, blank=True, default="", db_default="",
        help_text="Serial reported by the terminal on first contact. A pull "
                  "from a terminal reporting a different serial is refused.")
    port = models.PositiveIntegerField(default=4370)
    location = models.CharField(max_length=255, blank=True, default="")
    # Device clocks are set to wall-clock local time and pyzk reports them naive.
    # We record which zone that is so ingest can attach the right offset.
    device_timezone = models.CharField(max_length=64, default=settings.TIME_ZONE)
    is_active = models.BooleanField(default=True)

    # --- credentials: the raw key is shown once at creation and never stored ---
    api_key_prefix = models.CharField(max_length=32, blank=True, default="", db_index=True)
    api_key_hash = models.CharField(max_length=64, blank=True, default="")
    api_key_set_at = models.DateTimeField(null=True, blank=True)

    # --- health / sync state ---
    connection_status = models.CharField(max_length=10, choices=Status.choices, default=Status.UNKNOWN)
    last_seen_at = models.DateTimeField(null=True, blank=True, help_text="Last authenticated request from this device.")
    last_sync_at = models.DateTimeField(null=True, blank=True, help_text="Last successful punch ingest.")
    # High-water mark so the collector can stop re-pushing the whole backlog on
    # every reconnect (audit gap G7).
    last_punch_at = models.DateTimeField(null=True, blank=True, help_text="Newest punch timestamp ingested from this device.")
    clock_drift_seconds = models.IntegerField(
        null=True, blank=True, help_text="Device clock minus server clock, in seconds, at last sync.",
    )

    # --- collector health rollups (Phase 6) ---------------------------------
    # Reported by the collector on each sync and surfaced on the Phase 11
    # dashboard. pending_punches is the collector's local spool depth, which is
    # the only way the server can see attendance that exists but has not
    # arrived yet — a silent backlog is the failure mode worth alerting on.
    pending_punches = models.PositiveIntegerField(
        default=0, help_text="Punches sitting unsent in the collector's spool at last contact.")
    successful_batches = models.PositiveIntegerField(default=0)
    failed_batches = models.PositiveIntegerField(default=0)

    # --- scheduled pull (Organization Settings -> Biometric Devices) --------
    # The scheduler (`device_sync_due`) reads these. db_default on the NOT
    # NULL columns for the same reason attendance.Attendance uses it: code
    # that predates them must still be able to INSERT after a rollback.
    sync_interval_minutes = models.PositiveSmallIntegerField(
        choices=SyncInterval.choices, default=SyncInterval.EVERY_15,
        db_default=SyncInterval.EVERY_15,
        help_text="How often the server pulls this terminal. 0 = manual only.")
    # The terminal's COMM key (device menu: Comm > Security). Per device, not
    # per deployment, because two tenants' terminals will not share one. It
    # is a 0-999999 PIN that the ZK handshake sends in a scrambled form, so
    # it is never returned by the API -- write-only, like a password field.
    comm_key = models.PositiveIntegerField(default=0, db_default=0)
    last_sync_attempt_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last time a pull was attempted, successful or not. Drives the schedule.")
    last_sync_status = models.CharField(max_length=10, blank=True, default="", db_default="")
    last_sync_error = models.TextField(blank=True, default="", db_default="")
    last_sync_imported = models.PositiveIntegerField(
        default=0, db_default=0, help_text="New punches stored by the last pull.")
    # What the terminal said about itself at the last successful connection
    # test or sync: serial, firmware, platform, user and record counts.
    device_info = models.JSONField(default=dict, blank=True)
    device_info_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="biometric_devices_created",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Biometric Device"
        constraints = [
            # Both were unique=True standalone: two tenants could not both have
            # a terminal called "Main Gate".
            models.UniqueConstraint(fields=["organization", "name"],
                                    name="uniq_biometric_device_org_name"),
            models.UniqueConstraint(fields=["organization", "label"],
                                    name="uniq_biometric_device_org_label"),

            # SERIAL NUMBER IS GLOBALLY UNIQUE, AND THAT IS DELIBERATE
            # (Phase S3). It is the one constraint in this project that must
            # NOT be scoped to an organization, and the reason is the protocol:
            #
            # iClock/ADMS has no authentication whatsoever. A terminal
            # identifies itself with `?SN=<serial>` and nothing else, and the
            # firmware cannot be made to send a key. So the serial IS the
            # credential, and `_device_for` resolves it with
            # `filter(serial_number=serial).first()` -- before any tenant is
            # known, because the device is what tells us the tenant.
            #
            # With no constraint here, two organizations could hold the same
            # serial and `.first()` would pick one arbitrarily: one tenant
            # registering another tenant's terminal serial would silently
            # divert that terminal's punches into its own attendance records,
            # or capture them outright. A per-organization constraint would
            # permit exactly that. Platform-wide uniqueness is what makes
            # serial -> tenant a function.
            #
            # Blank is excluded because a pull-mode device (device_sync
            # connects to it over the LAN) never presents a serial and
            # legitimately has none.
            models.UniqueConstraint(
                fields=["serial_number"],
                condition=~models.Q(serial_number=""),
                name="uniq_biometric_device_serial_global"),
            # Same reasoning for the pulled identity. A database constraint,
            # not an application check, because under row-level security the
            # application role cannot SEE another tenant's devices to compare
            # against -- but the unique index is enforced over every row.
            models.UniqueConstraint(
                fields=["hardware_serial"],
                condition=~models.Q(hardware_serial=""),
                name="uniq_biometric_device_hw_serial_global"),
        ]

    def __str__(self):
        return f"{self.name} ({self.label})"

    def set_api_key(self, raw_key=None):
        """Assign a key and return the raw value — the only time it is readable."""
        raw_key = raw_key or generate_api_key()
        self.api_key_prefix = raw_key[:API_KEY_PREFIX_LENGTH]
        self.api_key_hash = hash_api_key(raw_key)
        self.api_key_set_at = timezone.now()
        return raw_key

    def check_api_key(self, raw_key):
        """Constant-time verification of a presented key."""
        if not self.api_key_hash or not raw_key:
            return False
        return hmac.compare_digest(self.api_key_hash, hash_api_key(raw_key))

    @property
    def has_api_key(self):
        return bool(self.api_key_hash)


class BiometricEmployeeQuerySet(TenantQuerySet):
    """Mapping lookups, now tenant-scoped (Phase S5).

    Subclasses TenantQuerySet rather than models.QuerySet so that
    `active()`, `mapped()`, `unmapped()` and `covering()` keep their chaining
    AND inherit `for_organization()` / `for_current_tenant()`. The scoping
    itself comes from the manager below.
    """
    def active(self):
        return self.filter(is_active=True)

    def mapped(self):
        return self.filter(user__isnull=False)

    def unmapped(self):
        return self.filter(user__isnull=True)

    def covering(self, d):
        """Mappings whose validity window contains date ``d``."""
        return self.filter(
            models.Q(effective_from__isnull=True) | models.Q(effective_from__lte=d),
            models.Q(effective_until__isnull=True) | models.Q(effective_until__gte=d),
        )


class BiometricEmployee(models.Model):
    """Maps a device's internal user ID to an OMS employee.

    The device knows people as short numeric strings ("1", "17"); the OMS knows
    them as ``users.User`` rows whose ``employee_id`` looks like
    ``NIFN-EMP-2026-0007``. Nothing links the two, so this table is the link.

    ``user`` is nullable on purpose: a punch from an unrecognised device ID is
    still recorded (never dropped), leaving an unmapped row for HR to resolve.

    **Device IDs get recycled.** When someone leaves, HR routinely re-enrols the
    next hire into the freed slot, so "device 17" is not a stable identity — it
    is only an identity *for a period of time*. That is what the
    effective_from/effective_until window is for, and why a retired mapping is
    kept rather than overwritten: without it, backfilling a new mapping would
    silently hand the leaver's attendance history to their replacement.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(BiometricDevice, on_delete=models.CASCADE, related_name="employees")
    # Phase S2 (tenant isolation, Phase A).
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )
    # CharField, never Integer: pyzk returns user_id as a decoded string or an
    # int depending on packet size, and the collector normalises to str().
    device_user_id = models.CharField(max_length=64)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="biometric_identities",
    )

    # Roster snapshot as enrolled on the device — used to suggest a mapping and
    # to keep unmapped punches human-readable.
    device_name = models.CharField(max_length=150, blank=True, default="")
    privilege = models.PositiveSmallIntegerField(default=0)
    card = models.BigIntegerField(default=0)
    group_id = models.CharField(max_length=32, blank=True, default="")

    is_active = models.BooleanField(default=True)

    # --- validity window: when this device ID meant THIS person -------------
    # Both null means "unbounded", which is only safe on a device ID that has
    # never belonged to anyone else. Backfill treats an unbounded window as a
    # hazard and refuses to run without an explicit override.
    effective_from = models.DateField(
        null=True, blank=True,
        help_text="Punches on or after this date belong to this mapping. Bounds backfill.",
    )
    effective_until = models.DateField(
        null=True, blank=True,
        help_text="Set when the mapping is retired, so a later occupant of the same device ID cannot claim earlier punches.",
    )
    superseded_by = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="supersedes",
        help_text="The mapping that replaced this one when the device ID was reassigned.",
    )

    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    mapped_at = models.DateTimeField(null=True, blank=True)
    mapped_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="biometric_mappings_made",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Phase S5 Part 3. The custom queryset is preserved -- `active()`,
    # `mapped()`, `unmapped()`, `covering()` are used across the biometric
    # module -- while the MANAGER under it now scopes to the tenant. Declared
    # here rather than at the top of the class because a second `objects = ...`
    # later in a class body silently wins, which is how this model was missed
    # on the first pass.
    objects = TenantManager.from_queryset(BiometricEmployeeQuerySet)()
    all_tenants = AllTenantsManager.from_queryset(BiometricEmployeeQuerySet)()

    def save(self, *args, **kwargs):
        """Refuse to rewrite the device's own identifier.

        **The device is the source of truth for identity.** ``device_user_id``
        and ``device`` are what the terminal sends with every punch; the OMS
        stores them and must never renumber them.

        Changing either on an existing row is silently catastrophic rather than
        loudly broken. Historical punches keep the ``employee_device_id`` they
        were ingested with, so they stay where they are — but every FUTURE punch
        from the original device ID stops resolving to this employee, and
        punches from the NEW id start resolving to them. The result is one
        person's attendance quietly landing on another person's record, with no
        error anywhere and nothing to notice until payroll.

        A device ID that has genuinely been reassigned to a new hire is handled
        by ``services.remap_device_user`` — retire this row with an
        ``effective_until`` and create a new one. That preserves history instead
        of rewriting it, which is the whole reason the validity window exists.

        Enforced on the model rather than the serializer so the API, the Django
        admin, a management command and a shell session are all covered by one
        rule. This closes a real hole: ``device_user_id`` was writable through
        ``BiometricEmployeeViewSet``'s PATCH.
        """
        if self.pk:
            previous = (BiometricEmployee.objects
                        .filter(pk=self.pk)
                        .values("device_id", "device_user_id")
                        .first())
            if previous:
                if str(previous["device_user_id"]) != str(self.device_user_id):
                    raise ValueError(
                        f"device_user_id is immutable: the device is the source "
                        f"of truth for identity. Tried to change "
                        f"{previous['device_user_id']!r} -> {self.device_user_id!r}. "
                        f"If this device ID was reassigned to a different person, "
                        f"use services.remap_device_user() so the original "
                        f"mapping is retired with a validity window and its "
                        f"attendance history is preserved.")
                if previous["device_id"] != self.device_id:
                    raise ValueError(
                        "device is immutable on a mapping: a device ID only has "
                        "meaning on the terminal that issued it. Create a "
                        "separate mapping on the other device instead.")
        return super().save(*args, **kwargs)

    class Meta:
        ordering = ["device", "device_user_id"]
        verbose_name = "Biometric Employee Mapping"
        constraints = [
            # Scoped to active rows so a device ID's history survives
            # reassignment: at most one LIVE mapping per device ID, plus any
            # number of retired ones carrying their own validity windows.
            models.UniqueConstraint(
                fields=["device", "device_user_id"],
                condition=models.Q(is_active=True),
                name="uniq_biometric_active_device_user",
            ),
            # One employee cannot hold two active identities on the same device.
            # Scoped per-device so a person enrolled on both the main gate and
            # the warehouse terminal is still allowed.
            models.UniqueConstraint(
                fields=["device", "user"],
                condition=models.Q(user__isnull=False, is_active=True),
                name="uniq_biometric_active_user",
            ),
        ]
        indexes = [
            # `user` alone is already covered by Django's automatic FK index.
            models.Index(fields=["device", "is_active"], name="idx_bioemp_dev_active"),
        ]

    def __str__(self):
        who = self.user.get_full_name() if self.user else f"UNMAPPED ({self.device_name or '?'})"
        return f"{self.device.label}#{self.device_user_id} -> {who}"

    @property
    def is_mapped(self):
        return self.user_id is not None

    @property
    def is_unbounded(self):
        """No start date — backfill cannot tell this occupant's punches from a
        previous occupant's, so it requires an explicit override."""
        return self.effective_from is None

    def covers_date(self, d):
        """Was this device ID this person on day ``d``?"""
        if self.effective_from and d < self.effective_from:
            return False
        if self.effective_until and d > self.effective_until:
            return False
        return True


class AttendancePunch(models.Model):
    """One raw punch, exactly as the terminal reported it.

    Append-only: this is the audit trail that every derived attendance figure
    can be traced back to, so rows are never edited in place. Corrections happen
    on the derived ``attendance.Attendance`` row, not here.

    The unique constraint mirrors the collector's ``dedup_key``
    (morx/models.py) field-for-field, which is what makes re-POSTing the whole
    device backlog on every reconnect safe and idempotent.
    """

    class Source(models.TextChoices):
        HISTORY = "HISTORY", "History (backlog pull)"
        LIVE = "LIVE", "Live (streamed)"
        IMPORT = "IMPORT", "Manual import"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S4 (tenant isolation, Phase B). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S4 Part 5: tenant-aware querying.
    #
    # `objects` now filters every read to the tenant in context. In
    # single-tenant operation that is a no-op -- every row is NIF's -- and it
    # becomes the real boundary the moment a second tenant has data.
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. Django's
    # `_base_manager` stays a plain unfiltered Manager (no base_manager_name
    # is set), so FK validation and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    # PROTECT: punches are audit data and must outlive casual device cleanup.
    device = models.ForeignKey(BiometricDevice, on_delete=models.PROTECT, related_name="punches")
    biometric_employee = models.ForeignKey(
        BiometricEmployee, on_delete=models.SET_NULL, null=True, blank=True, related_name="punches",
    )
    # Kept even when the mapping is missing or later deleted, so an unmapped
    # punch can always be re-attributed.
    employee_device_id = models.CharField(max_length=64)
    # Denormalised from biometric_employee.user — attendance queries filter by
    # employee constantly and this avoids a two-hop join on every one.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="biometric_punches",
    )
    employee_name = models.CharField(
        max_length=150, blank=True, default="", help_text="Name as enrolled on the device at punch time.",
    )

    # Timezone-aware. Ingest rejects naive timestamps outright (audit gap G6) —
    # the device reports naive local time and USE_TZ=True would silently read
    # that as UTC, putting every punch 5h45m out for Asia/Kathmandu.
    timestamp = models.DateTimeField()
    # Derived from `timestamp` in the configured local zone. Stored rather than
    # computed so day-bucketed queries stay index-friendly and never depend on
    # the database session timezone.
    local_date = models.DateField()

    punch = models.PositiveSmallIntegerField(help_text="Device punch code: 0 in, 1 out, 2/3 break, 4/5 overtime.")
    punch_label = models.CharField(max_length=20)
    verify_status = models.PositiveSmallIntegerField(
        default=0, help_text="Device verify mode (the collector's `status` field): fingerprint, card, password.",
    )
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.LIVE)
    raw = models.JSONField(default=dict, blank=True, help_text="Original payload, for forensics on odd firmware.")

    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Set once this punch has been folded into a daily attendance row (Phase 5).
    is_processed = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Attendance Punch"
        verbose_name_plural = "Attendance Punches"
        constraints = [
            # Field-for-field mirror of morx/models.py dedup_key.
            models.UniqueConstraint(
                fields=["device", "employee_device_id", "timestamp", "punch"],
                name="uniq_biometric_punch",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "local_date"], name="idx_punch_user_date"),
            models.Index(fields=["device", "local_date"], name="idx_punch_dev_date"),
            models.Index(fields=["local_date"], name="idx_punch_date"),
            # Partial: the derivation worker only ever scans unprocessed rows,
            # and that set stays tiny while the table grows to ~90k rows/year.
            models.Index(
                fields=["local_date"], condition=models.Q(is_processed=False), name="idx_punch_unprocessed",
            ),
        ]

    def __str__(self):
        who = self.employee_name or self.employee_device_id
        return f"{who} · {self.timestamp:%Y-%m-%d %H:%M:%S} · {self.punch_label}"

    def save(self, *args, **kwargs):
        if not self.punch_label:
            self.punch_label = punch_label(self.punch)
        if self.timestamp and not self.local_date:
            self.local_date = timezone.localtime(self.timestamp).date()
        super().save(*args, **kwargs)

    @property
    def is_entry(self):
        return self.punch in ENTRY_PUNCHES

    @property
    def is_exit(self):
        return self.punch in EXIT_PUNCHES


class DeviceSyncLog(models.Model):
    """One ingest batch — what arrived, what stuck, and what went wrong.

    Answers "why is Ram missing from today's report?" without trawling
    application logs: a batch with records_unmapped > 0 names the problem.
    """

    class SyncType(models.TextChoices):
        HISTORY = "history", "History backlog"
        LIVE = "live", "Live punch"
        ROSTER = "roster", "Employee roster"
        IMPORT = "import", "Manual import"
        # One row per server-initiated pull, success or failure. The per-batch
        # HISTORY rows ingest writes still exist beneath it; this is the row
        # that says "the scheduler tried at 10:05 and the terminal was off".
        PULL = "pull", "Device pull"
        TEST = "test", "Connection test"

    class Trigger(models.TextChoices):
        AUTO = "auto", "Scheduled"
        MANUAL = "manual", "Manual (Sync now)"
        TEST = "test", "Connection test"

    class Status(models.TextChoices):
        STARTED = "started", "Started"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        # A pull that did not run because another was already reading the
        # terminal. Not a failure: the device keeps its log, nothing is lost.
        SKIPPED = "skipped", "Skipped"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). Ownership chain declared in
    # tenancy/ownership.py; derived and verified on save.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True,
    )

    # Phase S5 Part 3: tenant-aware querying on Phase A and Phase C.
    #
    # `objects` filters every read to the tenant in context. Phase S4 switched
    # the 55 Phase B models; this completes the set, which is what closes R3
    # (15 unscoped Phase A `objects.all()` sites) and R4 (UserListView and
    # AdminUserViewSet returning every tenant's users).
    #
    # `all_tenants` is the deliberate, GREPPABLE escape hatch. `_base_manager`
    # stays a plain unfiltered Manager (no base_manager_name), so FK validation
    # and refresh_from_db are unaffected.
    objects = TenantManager()
    all_tenants = AllTenantsManager()
    device = models.ForeignKey(BiometricDevice, on_delete=models.CASCADE, related_name="sync_logs")
    sync_type = models.CharField(max_length=10, choices=SyncType.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.STARTED)

    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    records_received = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_duplicate = models.PositiveIntegerField(default=0)
    records_unmapped = models.PositiveIntegerField(default=0)
    records_invalid = models.PositiveIntegerField(default=0)
    # Spool depth the collector reported when it sent this batch, so a growing
    # backlog is visible server-side even while syncs keep succeeding.
    queue_depth = models.PositiveIntegerField(default=0)

    error = models.TextField(blank=True, default="")
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    # Blank on rows written by the push/ingest paths, which predate it.
    trigger = models.CharField(max_length=10, choices=Trigger.choices, blank=True,
                               default="", db_default="")
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "Device Sync Log"
        indexes = [
            models.Index(fields=["device", "-started_at"], name="idx_synclog_dev_start"),
            models.Index(fields=["status", "-started_at"], name="idx_synclog_status"),
        ]

    def __str__(self):
        return f"{self.device.label} · {self.sync_type} · {self.status} · {self.started_at:%Y-%m-%d %H:%M}"
        constraints = [
            # Both were unique=True standalone: two tenants could not both have
            # a device called "Main Gate".
            models.UniqueConstraint(fields=["organization", "name"],
                                    name="uniq_biometric_device_org_name"),
            models.UniqueConstraint(fields=["organization", "label"],
                                    name="uniq_biometric_device_org_label"),
        ]

    @property
    def duration_seconds(self):
        if not self.finished_at:
            return None
        return (self.finished_at - self.started_at).total_seconds()
