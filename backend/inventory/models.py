"""Inventory Management models.

Tracks office assets, WHO currently holds each one (ItemAssignment), and an
approval workflow for taking an item home/outside (TakeOutRequest) — mirroring the
leave module's maker→checker/approver→approved pattern.

Design notes:
  * User FKs are SET_NULL + name snapshots so historical records survive account
    deletion (the DB was designed to be wiped/re-onboarded).
  * asset_code / reference are handed out by a race-safe per-key counter
    (InventorySequence) — see services.py.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from tenancy.scoping import AllTenantsManager, TenantManager

# How far ahead a warranty counts as "expiring". Defined once, so the badge on the
# asset page and the warranty report cannot disagree about what the word means.
WARRANTY_WARNING_DAYS = 60
# Phase ASSET-LIFECYCLE-DISPOSAL. An AMC renewal needs a purchase order, which is
# slower than claiming on a warranty, so it is flagged as early as a warranty is.
AMC_WARNING_DAYS = 60
# End of life is a replacement decision and a budget line, so it is flagged a
# quarter ahead rather than two months.
END_OF_LIFE_WARNING_DAYS = 90

# The ten categories Phase 70.3 names. Seeded as InventoryCategory rows rather than
# hardcoded as choices, because the module already had a category TABLE and an
# organisation must be able to add an eleventh without a deployment.
DEFAULT_CATEGORIES = [
    ("Laptop", "Portable computers issued to staff"),
    ("Desktop", "Fixed workstations"),
    ("Monitor", "Displays and screens"),
    ("Printer", "Printers, scanners and multifunction devices"),
    ("Projector", "Projectors and presentation equipment"),
    ("Network Device", "Routers, switches, access points and firewalls"),
    ("Mobile Device", "Phones, tablets and dongles"),
    ("Furniture", "Desks, chairs, cabinets and fittings"),
    ("Accessories", "Chargers, bags, keyboards, mice and cables"),
    ("Other", "Anything not covered by the categories above"),
]


class InventorySequence(models.Model):
    """Gap-free, race-safe counter for asset codes / take-out references.
    ``year`` = 0 means not year-scoped (asset codes); a BS year for references."""
    key = models.CharField(max_length=8)
    year = models.PositiveIntegerField(default=0)
    last_value = models.PositiveIntegerField(default=0)
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

    class Meta:
        # Was (key, year): one counter row shared by every tenant, so one
        # tenant registering an asset advanced another tenant's asset codes.
        unique_together = ("organization", "key", "year")

    def __str__(self):
        return f"{self.key}-{self.year}: {self.last_value}"


class InventoryCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
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
    description = models.CharField(max_length=255, blank=True, default="")
    # Phase ASSET-LIFECYCLE-DISPOSAL. The useful life a new asset in this category
    # inherits, so a store officer does not have to know that a laptop is written
    # down over four years every time they register one. An asset may override it.
    default_useful_life_months = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Months over which assets in this category are depreciated.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Inventory categories"
        constraints = [
            # Was unique=True on `name`: two tenants could not both have a
            # "Laptop" category.
            models.UniqueConstraint(fields=["organization", "name"],
                                    name="uniq_inventory_category_org_name"),
        ]

    def __str__(self):
        return self.name


def asset_photo_path(instance, filename):
    """Unguessable per-file directory, matching the document modules' convention."""
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="inventory", kind="photos")


def asset_document_path(instance, filename):
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="inventory",
                       kind="documents")


class InventoryItem(models.Model):
    class Status(models.TextChoices):
        # Phase 70.2. The four states that existed are kept with their stored
        # values unchanged - there is live data in them - and the lifecycle's
        # missing ends are added around them.
        #
        # PROCUREMENT and RECEIVED sit BEFORE available: an asset on order is a real
        # thing the finance team knows about, and an asset delivered but not yet
        # checked in is the window in which things go missing. AVAILABLE is the
        # brief's "In Stock" under the name the data already uses; renaming the
        # stored value would have rewritten every existing row for a label.
        #
        # DISPOSED and ARCHIVED sit after RETIRED, which is now what it always
        # meant: withdrawn from service but still on the books.
        PROCUREMENT = "procurement", "On Order"
        RECEIVED = "received", "Received"
        AVAILABLE = "available", "In Stock"
        ASSIGNED = "assigned", "Assigned"
        OUT = "out", "Taken Out"            # approved take-out, currently outside
        MAINTENANCE = "maintenance", "Under Maintenance"
        RETIRED = "retired", "Retired"
        DISPOSED = "disposed", "Disposed"
        ARCHIVED = "archived", "Archived"

    # An asset in one of these is out of circulation for good. Nothing may be
    # assigned, taken out or sent for maintenance from here.
    TERMINAL_STATUSES = frozenset({Status.DISPOSED, Status.ARCHIVED})
    # Not yet available to anybody: on order, or delivered but not checked in.
    PRE_STOCK_STATUSES = frozenset({Status.PROCUREMENT, Status.RECEIVED})
    # Physically in the organisation's hands and usable.
    IN_SERVICE_STATUSES = frozenset({Status.AVAILABLE, Status.ASSIGNED, Status.OUT})

    class Condition(models.TextChoices):
        NEW = "new", "New"
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"
        DAMAGED = "damaged", "Damaged"

    class AssetType(models.TextChoices):
        """
        The broad family an asset belongs to. Retained unchanged - there is live
        data in it, and it answers a different question from the ten categories the
        brief names (which are now seeded InventoryCategory rows, see
        services.seed_default_categories).

        Type is about how the asset behaves: an IT asset has a MAC address and a
        warranty, furniture has neither. Category is about what it IS. A laptop and
        a monitor are both `it` and are different categories, which is exactly why
        one field could not do both jobs.
        """
        IT = "it", "IT / Computing"
        PERIPHERAL = "peripheral", "Peripheral"
        FURNITURE = "furniture", "Furniture"
        VEHICLE = "vehicle", "Vehicle"
        OTHER = "other", "Other"

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
    asset_code = models.CharField(max_length=32, editable=False)
    name = models.CharField(max_length=150)
    asset_type = models.CharField(max_length=20, choices=AssetType.choices, default=AssetType.OTHER)
    category = models.ForeignKey(
        InventoryCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="items")
    serial_number = models.CharField(max_length=120, blank=True, default="")
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="inventory_items")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    condition = models.CharField(max_length=20, choices=Condition.choices, default=Condition.GOOD)
    purchase_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    # --- Device specifications (mainly IT/electronic assets; all optional) ---
    brand = models.CharField(max_length=80, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    cpu = models.CharField(max_length=120, blank=True, default="")
    ram = models.CharField(max_length=60, blank=True, default="")
    storage_type = models.CharField(max_length=40, blank=True, default="")   # SSD/HDD/NVMe
    storage_size = models.CharField(max_length=40, blank=True, default="")   # e.g. 512GB
    gpu = models.CharField(max_length=120, blank=True, default="")
    screen_size = models.CharField(max_length=40, blank=True, default="")
    os = models.CharField(max_length=80, blank=True, default="")
    mac_address = models.CharField(max_length=64, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    warranty_expiry = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vendor = models.CharField(max_length=150, blank=True, default="")
    accessories = models.CharField(max_length=255, blank=True, default="")   # charger/bag/mouse
    # Flexible, category-appropriate specs for non-IT items (furniture, camera…).
    specifications = models.JSONField(default=dict, blank=True)

    # --- Phase 70.4: the asset master fields the brief names that were absent ---
    location = models.CharField(
        max_length=150, blank=True, default="", db_index=True,
        help_text="Where the asset physically is - room, floor, site. Distinct "
                  "from department, which is who it belongs to: a laptop owned by "
                  "Finance can sit in the server room.")
    warranty_start = models.DateField(
        null=True, blank=True,
        help_text="Warranty START. The module already had an expiry; without a "
                  "start date a warranty cannot be shown as a period, only as a "
                  "deadline.")
    # --- Phase ASSET-LIFECYCLE-DISPOSAL: depreciation ----------------------
    # Straight-line, by whole months, from the purchase date. Only the INPUTS
    # are stored; accumulated depreciation and book value are derived on read
    # (see depreciation.py), so they are right on the day they are asked for
    # and can never drift from the cost they were computed from.
    useful_life_months = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Overrides the category's useful life for this asset.")
    salvage_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Residual value at the end of its useful life. Blank means zero.")

    # --- Phase ASSET-LIFECYCLE-DISPOSAL: annual maintenance contract --------
    amc_provider = models.CharField(max_length=150, blank=True, default="")
    amc_contract_number = models.CharField(max_length=80, blank=True, default="")
    amc_start = models.DateField(null=True, blank=True)
    amc_end = models.DateField(null=True, blank=True)
    amc_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    photo = models.ImageField(upload_to=asset_photo_path, max_length=255,
                              null=True, blank=True)
    document = models.FileField(
        upload_to=asset_document_path, max_length=255, null=True, blank=True,
        help_text="Invoice, warranty card or delivery note.")
    document_name = models.CharField(max_length=255, blank=True, default="")

    # Disposal, recorded on the asset because it is a property of the asset rather
    # than an event that might be one of several.
    disposed_at = models.DateTimeField(null=True, blank=True)
    disposal_method = models.CharField(max_length=120, blank=True, default="")
    disposal_reason = models.TextField(blank=True, default="")
    disposal_value = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Proceeds of sale, if any. Null means disposed at no value.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["asset_code"]
        constraints = [
            # Phase S4: was unique=True on `asset_code` alone -- a GLOBAL namespace.
            # Phase S3 already gives each tenant its own document prefix, so a
            # collision was impossible in practice; the composite makes it
            # impossible by construction and removes the last way one tenant's
            # numbering could refuse another tenant's insert.
            models.UniqueConstraint(fields=["organization", "asset_code"],
                                    name="uniq_asset_code_org"),
            # A serial number identifies one physical device, so two assets must not
            # claim the same one. Enforced on Lower(...) because the scan lookup in
            # lifecycle_views resolves with __iexact — without folding case, "AB12"
            # and "ab12" would satisfy the constraint yet both match one scan.
            #
            # Conditional on a non-blank value: serial_number defaults to "" and is
            # legitimately unset on assets that carry no serial, so a plain
            # unique=True would let only ONE such asset exist.
            # PER TENANT (Phase S4), and this is the deliberate OPPOSITE of
            # the decision taken for biometric.BiometricDevice.serial_number
            # in Phase S3. The two look identical and are not:
            #
            #   BiometricDevice  GLOBAL. The iClock protocol has no
            #                    authentication -- a terminal sends ?SN=<serial>
            #                    and nothing else -- so the serial IS the
            #                    credential and is resolved BEFORE any tenant is
            #                    known. serial -> tenant must be a function, or
            #                    one tenant registering another's serial
            #                    diverts its punches.
            #
            #   InventoryItem    PER TENANT. Nothing ever resolves an asset by
            #                    serial without a tenant already in hand: every
            #                    lookup is an authenticated in-app scan or
            #                    search. So global uniqueness buys no safety
            #                    here, and costs two real harms:
            #                      * one tenant could not register a laptop
            #                        because another tenant had typed that
            #                        serial, and
            #                      * the integrity error REVEALED that another
            #                        tenant holds that asset -- an existence
            #                        oracle across the tenant boundary.
            #
            # Within one tenant the guarantee is unchanged: a serial still
            # identifies one physical device, folded on case because the scan
            # lookup resolves with __iexact.
            models.UniqueConstraint(
                "organization",
                Lower("serial_number"),
                condition=~Q(serial_number=""),
                name="uniq_inventory_serial_org",
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="asset_status_idx"),
            models.Index(fields=["status", "department"], name="asset_status_dept_idx"),
            models.Index(fields=["warranty_expiry"], name="asset_warranty_idx"),
        ]

    def __str__(self):
        return f"{self.asset_code} · {self.name}"

    @property
    def is_it_asset(self):
        return self.asset_type in (self.AssetType.IT, self.AssetType.PERIPHERAL)

    @property
    def active_assignment(self):
        return self.assignments.filter(is_active=True).first()

    # --- Phase 70 derived state ------------------------------------------
    @property
    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES

    @property
    def is_in_service(self):
        return self.status in self.IN_SERVICE_STATUSES

    @property
    def warranty_state(self):
        """
        'active', 'expiring', 'expired' or None.

        Derived from the date rather than stored, so it is correct the instant a
        warranty lapses - the same rule the minute module's overdue actions follow.
        Sixty days is the window the warranty report uses, defined once here so the
        report and the badge on the asset page cannot disagree.
        """
        if not self.warranty_expiry:
            return None
        from django.utils import timezone
        today = timezone.localdate()
        if self.warranty_expiry < today:
            return "expired"
        if (self.warranty_expiry - today).days <= WARRANTY_WARNING_DAYS:
            return "expiring"
        return "active"

    @staticmethod
    def _window_state(end, warning_days):
        """'expired' / 'expiring' / 'active' / None for a dated contract."""
        if not end:
            return None
        from django.utils import timezone
        today = timezone.localdate()
        if end < today:
            return "expired"
        if (end - today).days <= warning_days:
            return "expiring"
        return "active"

    @property
    def amc_state(self):
        return self._window_state(self.amc_end, AMC_WARNING_DAYS)

    @property
    def effective_useful_life_months(self):
        """The asset's own useful life, else its category's, else None."""
        if self.useful_life_months:
            return self.useful_life_months
        return getattr(self.category, "default_useful_life_months", None) or None

    @property
    def end_of_life_date(self):
        """Purchase date plus useful life. None if either is unknown."""
        months = self.effective_useful_life_months
        if not (self.purchase_date and months):
            return None
        from .depreciation import add_months
        return add_months(self.purchase_date, months)

    @property
    def end_of_life_state(self):
        """'past' / 'approaching' / 'in_life' / None."""
        state = self._window_state(self.end_of_life_date, END_OF_LIFE_WARNING_DAYS)
        return {"expired": "past", "expiring": "approaching",
                "active": "in_life"}.get(state)

    @property
    def open_maintenance_ticket(self):
        return self.maintenance_tickets.filter(
            status__in=MaintenanceTicket.OPEN_STATUSES).first()


class ItemAssignment(models.Model):
    """WHO is currently using an item. At most one active row per item.

    The item FK is SET_NULL + snapshots (matching TakeOutRequest) so custody history
    — who held what — survives deletion of the item itself.
    """
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
    item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="assignments")
    item_code = models.CharField(max_length=32, blank=True, default="")
    item_name = models.CharField(max_length=150, blank=True, default="")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="inventory_assignments")
    assigned_to_name = models.CharField(max_length=150, blank=True, default="")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="inventory_assigned_by")
    assigned_by_name = models.CharField(max_length=150, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    # Business handover date (BS+AD), distinct from the created timestamp.
    assigned_date = models.DateField(null=True, blank=True)
    handover_condition = models.CharField(max_length=20, choices=InventoryItem.Condition.choices, blank=True, default="")
    accessories = models.CharField(max_length=255, blank=True, default="")
    # A handover is a transfer from a previous active holder to a new one.
    is_handover = models.BooleanField(default=False)
    # Captured when the item is returned / the assignment is closed.
    return_condition = models.CharField(max_length=20, choices=InventoryItem.Condition.choices, blank=True, default="")
    return_remarks = models.CharField(max_length=255, blank=True, default="")
    assigned_at = models.DateTimeField(auto_now_add=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-assigned_at"]
        constraints = [
            # Integrity: only one active holder per item at any time (race-safe).
            models.UniqueConstraint(
                fields=["item"], condition=Q(is_active=True),
                name="uniq_active_assignment_per_item"),
        ]

    def __str__(self):
        # item may be NULL (deleted asset) — fall back to the snapshot.
        code = self.item.asset_code if self.item_id else (self.item_code or "—")
        return f"{code} → {self.assigned_to_name} ({'active' if self.is_active else 'closed'})"


class TakeOutRequest(models.Model):
    class Purpose(models.TextChoices):
        HOME = "home", "Take Home"
        OUTSIDE = "outside", "Use Outside Office"
        REPAIR = "repair", "Repair / Servicing"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        RETURNED = "returned", "Returned"

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
    reference = models.CharField(max_length=32, editable=False)
    # Item FK is SET_NULL + snapshot so a retired/deleted item keeps its history.
    item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="takeout_requests")
    item_code = models.CharField(max_length=32, blank=True, default="")
    item_name = models.CharField(max_length=150, blank=True, default="")

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="takeout_requests")
    requested_by_name = models.CharField(max_length=150, blank=True, default="")
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="inventory_takeouts")

    purpose = models.CharField(max_length=12, choices=Purpose.choices, default=Purpose.HOME)
    reason = models.TextField()
    expected_out_date = models.DateField()
    expected_return_date = models.DateField()

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="takeout_reviewed")
    approver_name = models.CharField(max_length=150, blank=True, default="")
    approver_remarks = models.TextField(blank=True, default="")
    action_date = models.DateTimeField(null=True, blank=True)
    actual_return_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["requested_by", "status"]),
        ]
        # Phase S4: was unique=True on `reference` alone -- a GLOBAL namespace.
        # Phase S3 already gives each tenant its own document prefix, so a
        # collision was impossible in practice; the composite makes it
        # impossible by construction and removes the last way one tenant's
        # numbering could refuse another tenant's insert.
        constraints = [
            models.UniqueConstraint(fields=["organization", "reference"],
                                    name="uniq_takeout_reference_org"),
        ]

    def __str__(self):
        return f"{self.reference} · {self.item_code} ({self.status})"


# ===========================================================================
# Phase 70 - enterprise asset lifecycle
#
# The module before this phase tracked three things: what an asset IS, who holds
# it, and whether it has been taken outside. It did not track how it GOT there.
# Everything below adds that: a lifecycle with an append-only event log, the two
# approval workflows a bank puts in front of custody changing hands, and
# maintenance as a first-class thing rather than a status nobody can explain.
#
# The organising rule is the one the memo and circular modules settled on:
# STATE lives on the asset, HISTORY lives in its own append-only table, and a row
# that can be rewritten cannot answer "what happened".
# ===========================================================================
class AssetLifecycleEvent(models.Model):
    """
    One thing that happened to an asset. Append-only, never updated.

    The brief's lifecycle - Procurement, Received, In Stock, Assigned, Returned,
    Maintenance, Reassigned, Disposed, Archived - is nine words that are not all
    the same kind of thing. Four are STATES an asset rests in (in stock, assigned,
    maintenance, disposed) and the rest are TRANSITIONS between them (received,
    returned, reassigned, archived). Modelling all nine as statuses would have
    forced questions with no good answer, like what status an asset has after it is
    returned but before it is back on the shelf.

    So states live on InventoryItem.status and every transition writes a row here.
    "Track every transition" is then true by construction rather than by
    discipline, and the asset history screen (70.11) is a query rather than a
    reconstruction.
    """

    class Event(models.TextChoices):
        CREATED = "created", "Created"
        PROCUREMENT = "procurement", "Procurement raised"
        RECEIVED = "received", "Received"
        STOCKED = "stocked", "Placed in stock"
        REQUESTED = "requested", "Requested"
        REQUEST_APPROVED = "request_approved", "Request approved"
        REQUEST_REJECTED = "request_rejected", "Request rejected"
        ASSIGNED = "assigned", "Assigned"
        HANDED_OVER = "handed_over", "Handed over"
        ACCEPTED = "accepted", "Accepted by holder"
        REASSIGNED = "reassigned", "Reassigned"
        # Phase ASSET-CUSTODY-TRANSFER. Custody moved by an APPROVED transfer, as
        # distinct from REASSIGNED/HANDED_OVER, which an officer can do directly.
        # The difference is the whole point of the feature: an auditor asking
        # "which moves went through approval?" gets an answer from one column.
        TRANSFERRED = "transferred", "Transferred"
        DEPARTMENT_CHANGED = "department_changed", "Department changed"
        RETURN_REQUESTED = "return_requested", "Return requested"
        RETURNED = "returned", "Returned"
        TAKEN_OUT = "taken_out", "Taken out"
        BROUGHT_BACK = "brought_back", "Brought back"
        MAINTENANCE_REPORTED = "maint_reported", "Maintenance reported"
        MAINTENANCE_STARTED = "maint_started", "Maintenance started"
        MAINTENANCE_DONE = "maint_done", "Maintenance completed"
        CONDITION_CHANGED = "condition_changed", "Condition changed"
        LOCATION_CHANGED = "location_changed", "Location changed"
        DISPOSED = "disposed", "Disposed"
        # Phase ASSET-LIFECYCLE-DISPOSAL. Custody ended because the asset is gone,
        # not because it came back. Recorded before DISPOSED so the chain of
        # custody says why the last holder stopped holding it.
        LOST = "lost", "Reported lost"
        ARCHIVED = "archived", "Archived"
        UPDATED = "updated", "Details updated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Phase S5 (tenant isolation, Phase C). DELIBERATELY NULLABLE --
    # its `item` is nullable.
    # A NULL-organization row is invisible under the RLS policy, which is the
    # correct fail-closed answer: only the platform console reads it.
    organization = models.ForeignKey(
        "tenancy.Organization", on_delete=models.PROTECT, related_name="+",
        db_index=True, null=True, blank=True,
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
    item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lifecycle_events",
        help_text="SET_NULL with a snapshot, matching every other history table "
                  "here: deleting an asset must not erase the record that it "
                  "existed and who held it.")
    item_code = models.CharField(max_length=32, blank=True, default="")
    item_name = models.CharField(max_length=150, blank=True, default="")

    sequence = models.PositiveIntegerField(default=0)
    event = models.CharField(max_length=24, choices=Event.choices, db_index=True)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_events")
    actor_name = models.CharField(max_length=150, blank=True, default="")
    # Who the event was ABOUT, when that differs from who did it - the person an
    # asset was assigned to, rather than the officer who assigned it.
    subject_name = models.CharField(max_length=150, blank=True, default="")

    remarks = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "sequence"]
        indexes = [
            models.Index(fields=["item", "at"], name="asset_event_item_idx"),
        ]

    def __str__(self):
        return f"{self.item_code} {self.event} @ {self.at:%Y-%m-%d}"


class AssetRequest(models.Model):
    """
    An employee asking to be issued an asset (Phase 70.5).

    Two approvals before anything moves, then two more steps before the record is
    complete: the officer hands over, and the employee ACCEPTS. That last step is
    the one that makes the record worth keeping - without it "assigned" means "an
    officer says they gave it to you", and the first time an asset goes missing
    that distinction is the whole argument.

    The asset is only held against a request from `inventory_approved` onwards, so
    two employees can both request the same laptop and the first approval decides
    it, rather than the first request silently reserving it.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUPERVISOR_APPROVED = "supervisor_approved", "Supervisor Approved"
        INVENTORY_APPROVED = "inventory_approved", "Inventory Approved"
        HANDED_OVER = "handed_over", "Handed Over"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    # Statuses where the request still has a claim on the asset. Used to stop two
    # live requests reserving one item.
    HOLDING_STATUSES = frozenset({Status.INVENTORY_APPROVED, Status.HANDED_OVER})
    OPEN_STATUSES = frozenset({Status.PENDING, Status.SUPERVISOR_APPROVED,
                               Status.INVENTORY_APPROVED, Status.HANDED_OVER})

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
    reference = models.CharField(max_length=32, editable=False)

    item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_requests")
    item_code = models.CharField(max_length=32, blank=True, default="")
    item_name = models.CharField(max_length=150, blank=True, default="")
    # A request may name a CATEGORY rather than a specific asset - "I need a
    # laptop" is the common case, and forcing the employee to pick an asset code
    # they cannot see the availability of is how paper forms get filled in wrong.
    requested_category = models.ForeignKey(
        InventoryCategory, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_requests")

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_requests")
    requested_by_name = models.CharField(max_length=150, blank=True, default="")
    department = models.ForeignKey(
        "leaves.Department", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_requests")
    purpose = models.TextField()
    needed_by = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.PENDING, db_index=True)

    supervisor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_requests_supervised")
    supervisor_name = models.CharField(max_length=150, blank=True, default="")
    supervisor_remarks = models.TextField(blank=True, default="")
    supervisor_at = models.DateTimeField(null=True, blank=True)

    inventory_officer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_requests_processed")
    inventory_officer_name = models.CharField(max_length=150, blank=True, default="")
    inventory_remarks = models.TextField(blank=True, default="")
    inventory_at = models.DateTimeField(null=True, blank=True)

    handed_over_at = models.DateTimeField(null=True, blank=True)
    handover_condition = models.CharField(
        max_length=20, choices=InventoryItem.Condition.choices, blank=True, default="")
    accessories = models.CharField(max_length=255, blank=True, default="")

    accepted_at = models.DateTimeField(null=True, blank=True)
    acceptance_remarks = models.TextField(blank=True, default="")

    rejected_by_name = models.CharField(max_length=150, blank=True, default="")
    rejection_reason = models.TextField(blank=True, default="")

    assignment = models.OneToOneField(
        ItemAssignment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="request",
        help_text="Created at handover. The request is the paperwork; the "
                  "assignment is the custody record, and they are separate because "
                  "an asset can be assigned without a request (a direct issue by "
                  "an officer) but never the reverse.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="asset_req_status_idx"),
            models.Index(fields=["requested_by", "status"], name="asset_req_user_idx"),
        ]
        # Phase S4: was unique=True on `reference` alone -- a GLOBAL namespace.
        # Phase S3 already gives each tenant its own document prefix, so a
        # collision was impossible in practice; the composite makes it
        # impossible by construction and removes the last way one tenant's
        # numbering could refuse another tenant's insert.
        constraints = [
            models.UniqueConstraint(fields=["organization", "reference"],
                                    name="uniq_asset_request_reference_org"),
        ]

    def __str__(self):
        return f"{self.reference} · {self.item_code or 'category'} ({self.status})"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def item_label(self):
        """
        What to call the thing being asked for, in a sentence.

        A request names either a specific asset or a category, and it changes from
        one to the other the moment an officer picks one. Notifications and PDFs
        both need to say what was requested without caring which of the two it is
        at that instant, and doing that at each call site is how one of them ends
        up reading "your request for None was approved".
        """
        if self.item_code:
            return f"{self.item_code} · {self.item_name}".strip(" ·")
        if self.requested_category_id:
            return self.requested_category.name
        return "an asset"


class AssetReturn(models.Model):
    """
    An employee giving an asset back (Phase 70.6).

    Separate from the officer-initiated `return_item` service, which stays: an
    officer taking an asset back at a desk is a real thing and should not require
    the employee to raise a form first. This models the other direction - the
    employee starts it, and an officer verifies and inspects before the asset is
    back in stock.

    Condition at inspection is the officer's finding, and it is deliberately a
    separate field from the employee's own declaration. When they differ, that
    disagreement is the record.
    """

    class Status(models.TextChoices):
        REQUESTED = "requested", "Return Requested"
        VERIFYING = "verifying", "Under Verification"
        INSPECTED = "inspected", "Condition Inspected"
        ACCEPTED = "accepted", "Return Accepted"
        REJECTED = "rejected", "Return Rejected"

    OPEN_STATUSES = frozenset({Status.REQUESTED, Status.VERIFYING, Status.INSPECTED})

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
    reference = models.CharField(max_length=32, editable=False)

    item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_returns")
    item_code = models.CharField(max_length=32, blank=True, default="")
    item_name = models.CharField(max_length=150, blank=True, default="")
    assignment = models.ForeignKey(
        ItemAssignment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="returns")

    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_returns")
    returned_by_name = models.CharField(max_length=150, blank=True, default="")
    reason = models.TextField(blank=True, default="")

    # What the EMPLOYEE says the condition is.
    declared_condition = models.CharField(
        max_length=20, choices=InventoryItem.Condition.choices, blank=True, default="")
    declared_remarks = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.REQUESTED, db_index=True)

    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_returns_verified")
    verified_by_name = models.CharField(max_length=150, blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)

    # What the OFFICER finds. Separate from the declaration on purpose.
    inspected_condition = models.CharField(
        max_length=20, choices=InventoryItem.Condition.choices, blank=True, default="")
    inspection_remarks = models.TextField(blank=True, default="")
    inspected_at = models.DateTimeField(null=True, blank=True)

    accepted_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True, default="")
    returned_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="asset_ret_status_idx"),
        ]
        # Phase S4: was unique=True on `reference` alone -- a GLOBAL namespace.
        # Phase S3 already gives each tenant its own document prefix, so a
        # collision was impossible in practice; the composite makes it
        # impossible by construction and removes the last way one tenant's
        # numbering could refuse another tenant's insert.
        constraints = [
            models.UniqueConstraint(fields=["organization", "reference"],
                                    name="uniq_asset_return_reference_org"),
        ]

    def __str__(self):
        return f"{self.reference} · {self.item_code} ({self.status})"

    @property
    def condition_disputed(self):
        """
        True when the officer's finding differs from the employee's declaration.

        Surfaced rather than resolved: the system's job is to record that the two
        did not agree, not to decide who was right.
        """
        return bool(self.declared_condition and self.inspected_condition
                    and self.declared_condition != self.inspected_condition)


class MaintenanceTicket(models.Model):
    """
    An asset needing work (Phase 70.8).

    A ticket, not a status. The asset already had a `maintenance` status before
    this phase and it could not answer any of the questions that matter: what is
    wrong with it, who is fixing it, since when, and whether this has happened
    before. A ticket answers all four and gives the asset a maintenance HISTORY,
    which is what turns "this laptop keeps breaking" from an impression into a
    number.
    """

    class Status(models.TextChoices):
        REPORTED = "reported", "Reported"
        ASSIGNED = "assigned", "Assigned"
        IN_MAINTENANCE = "in_maintenance", "In Maintenance"
        COMPLETED = "completed", "Completed"
        RETURNED = "returned", "Returned to Service"
        CANCELLED = "cancelled", "Cancelled"

    OPEN_STATUSES = frozenset({Status.REPORTED, Status.ASSIGNED,
                               Status.IN_MAINTENANCE, Status.COMPLETED})

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        CRITICAL = "critical", "Critical"

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
    reference = models.CharField(max_length=32, editable=False)

    item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="maintenance_tickets")
    item_code = models.CharField(max_length=32, blank=True, default="")
    item_name = models.CharField(max_length=150, blank=True, default="")

    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="maintenance_reported")
    reported_by_name = models.CharField(max_length=150, blank=True, default="")
    issue = models.TextField()
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.REPORTED, db_index=True)

    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="maintenance_assigned")
    assigned_to_name = models.CharField(max_length=150, blank=True, default="")
    vendor = models.CharField(
        max_length=150, blank=True, default="",
        help_text="External repairer, when the work is not done in house.")
    assigned_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)

    resolution = models.TextField(blank=True, default="")
    cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    condition_after = models.CharField(
        max_length=20, choices=InventoryItem.Condition.choices, blank=True, default="")

    # The status the asset held before it went for maintenance, so returning it to
    # service puts it back where it was rather than guessing at "available".
    previous_item_status = models.CharField(max_length=20, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="maint_status_idx"),
            models.Index(fields=["item", "status"], name="maint_item_idx"),
        ]
        # Phase S4: was unique=True on `reference` alone -- a GLOBAL namespace.
        # Phase S3 already gives each tenant its own document prefix, so a
        # collision was impossible in practice; the composite makes it
        # impossible by construction and removes the last way one tenant's
        # numbering could refuse another tenant's insert.
        constraints = [
            models.UniqueConstraint(fields=["organization", "reference"],
                                    name="uniq_maintenance_reference_org"),
        ]

    def __str__(self):
        return f"{self.reference} · {self.item_code} ({self.status})"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def days_open(self):
        """Calendar days since the ticket was raised, until it returns to service."""
        from django.utils import timezone
        end = self.returned_at or timezone.now()
        return (end - self.created_at).days if self.created_at else 0



def transfer_attachment_path(instance, filename):
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    #
    # The transfer id replaces the random directory here: it is a UUID, so the
    # path stays unguessable, and grouping a transfer's evidence together is
    # worth keeping.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="inventory",
                       kind=f"transfers/{instance.transfer_id}",
                       dated=False, unguessable=False)


class AssetTransfer(models.Model):
    """
    An APPROVED movement of an asset from one employee's custody to another's
    (Phase ASSET-CUSTODY-TRANSFER).

    WHAT THIS IS NOT
    ----------------
    It is not a second history. Custody already has a permanent record:
    ItemAssignment rows (one active holder per asset, closed rather than deleted)
    and AssetLifecycleEvent rows (append-only). A transfer is the GOVERNANCE in
    front of a custody change - who asked, why, who approved at each gate - and on
    completion it hands the actual change to `services.handover_item`, which is
    still the only code that closes one assignment and opens the next. So history
    is appended by construction, never overwritten, and a transfer can never
    disagree with the register about who holds what.

    WHY STATUSES AND EVENTS DIFFER
    ------------------------------
    The workflow is Draft, Submitted, Department Head Review, HR Review, Admin
    Approval, Completed. "Submitted" is something that HAPPENS - it is the moment
    a draft enters review - while the other five are places a transfer RESTS. So
    submission is recorded as an event and a `submitted_at` stamp, and the status
    it leads to is DEPT_HEAD_REVIEW. Modelling it as a resting state would leave
    a status nobody is responsible for acting on.

    Snapshots (names, departments, asset code) are kept for the same reason every
    other history table here keeps them: deleting a user or an asset must not
    erase the record that this transfer happened.
    """

    class Reason(models.TextChoices):
        EMPLOYEE_EXIT = "employee_exit", "Employee Exit"
        DEPARTMENT_TRANSFER = "department_transfer", "Department Transfer"
        ROLE_CHANGE = "role_change", "Role Change"
        ASSET_REPLACEMENT = "asset_replacement", "Asset Replacement"
        TEMPORARY_ASSIGNMENT = "temporary_assignment", "Temporary Assignment"
        PROJECT_ASSIGNMENT = "project_assignment", "Project Assignment"
        OTHER = "other", "Other"

    class Condition(models.TextChoices):
        # Wider than InventoryItem.Condition on purpose: "Excellent" and "Lost" are
        # things a person handing an asset over genuinely says. They are mapped
        # onto the register's vocabulary at completion (see CONDITION_TO_ITEM).
        EXCELLENT = "excellent", "Excellent"
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"
        DAMAGED = "damaged", "Damaged"
        LOST = "lost", "Lost"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        DEPT_HEAD_REVIEW = "dept_head_review", "Department Head Review"
        HR_REVIEW = "hr_review", "HR Review"
        ADMIN_APPROVAL = "admin_approval", "Admin Approval"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    # HR IS THE ONLY GATE (Phase ASSET-TRANSFER-GOVERNANCE).
    #
    #     Draft -> HR Review -> Completed
    #
    # Three gates were governance on paper and a queue in practice: a laptop
    # moving one desk needed a department head, HR and an administrator, and the
    # brief replaces that with HR as the single final authority.
    #
    # DEPT_HEAD_REVIEW and ADMIN_APPROVAL STAY IN `Status` and are not removed.
    # Transfers already completed carry `dept_head_at` / `approved_at` stamps and
    # event rows reading "Department Head approved"; deleting the values would
    # leave that history unreadable, and the record of who approved what is the
    # thing a transfer exists to keep. They are simply no longer reachable: no
    # transition routes to them, and migration 0011 moves the in-flight ones on.
    REVIEW_STAGES = (Status.HR_REVIEW,)
    # The gates this workflow used to have. Kept so the stage tracker and the
    # reports can still name them when reading an older record.
    RETIRED_STAGES = (Status.DEPT_HEAD_REVIEW, Status.ADMIN_APPROVAL)
    # Includes the retired gates: a transfer left at one by an older release is
    # still open, and must still be found by the "in progress" queries.
    OPEN_STATUSES = frozenset({Status.DRAFT, *REVIEW_STAGES, *RETIRED_STAGES})
    CLOSED_STATUSES = frozenset({Status.COMPLETED, Status.REJECTED, Status.CANCELLED})

    # The register's condition vocabulary has no "excellent"; it is a good asset.
    # LOST has no mapping because a lost asset cannot be handed to anybody, which
    # is enforced at submission rather than discovered at completion.
    CONDITION_TO_ITEM = {
        Condition.EXCELLENT: InventoryItem.Condition.GOOD,
        Condition.GOOD: InventoryItem.Condition.GOOD,
        Condition.FAIR: InventoryItem.Condition.FAIR,
        Condition.DAMAGED: InventoryItem.Condition.DAMAGED,
    }

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
    transfer_number = models.CharField(max_length=20, editable=False)

    item = models.ForeignKey(
        InventoryItem, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transfers")
    item_code = models.CharField(max_length=32, blank=True, default="")
    item_name = models.CharField(max_length=150, blank=True, default="")

    from_employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_transfers_out")
    from_employee_name = models.CharField(max_length=150, blank=True, default="")
    from_department_name = models.CharField(max_length=150, blank=True, default="")

    to_employee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_transfers_in")
    to_employee_name = models.CharField(max_length=150, blank=True, default="")
    to_department_name = models.CharField(max_length=150, blank=True, default="")

    transfer_date = models.DateField()
    reason = models.CharField(max_length=24, choices=Reason.choices)
    condition = models.CharField(max_length=12, choices=Condition.choices)
    remarks = models.TextField(blank=True, default="")

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_transfers_requested")
    requested_by_name = models.CharField(max_length=150, blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)

    # One set of fields per gate, so a reader sees who cleared each one without
    # replaying the event log. The log is still the authority.
    dept_head_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    dept_head_name = models.CharField(max_length=150, blank=True, default="")
    dept_head_at = models.DateTimeField(null=True, blank=True)
    dept_head_remarks = models.TextField(blank=True, default="")

    hr_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    hr_name = models.CharField(max_length=150, blank=True, default="")
    hr_at = models.DateTimeField(null=True, blank=True)
    hr_remarks = models.TextField(blank=True, default="")

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_transfers_approved",
        help_text="The Admin who gave final approval. Completion follows at once.")
    approved_by_name = models.CharField(max_length=150, blank=True, default="")
    approved_at = models.DateTimeField(null=True, blank=True)
    admin_remarks = models.TextField(blank=True, default="")

    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    rejected_by_name = models.CharField(max_length=150, blank=True, default="")
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_stage = models.CharField(max_length=20, blank=True, default="")
    rejection_remarks = models.TextField(blank=True, default="")

    completed_at = models.DateTimeField(null=True, blank=True)
    # The custody rows this transfer closed and opened - the join between the
    # governance record and the register.
    closed_assignment = models.ForeignKey(
        "ItemAssignment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    opened_assignment = models.ForeignKey(
        "ItemAssignment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="transfer_status_idx"),
            models.Index(fields=["item", "status"], name="transfer_item_idx"),
        ]
        constraints = [
            # Phase S4: was unique=True on `transfer_number` alone -- a GLOBAL namespace.
            # Phase S3 already gives each tenant its own document prefix, so a
            # collision was impossible in practice; the composite makes it
            # impossible by construction and removes the last way one tenant's
            # numbering could refuse another tenant's insert.
            models.UniqueConstraint(fields=["organization", "transfer_number"],
                                    name="uniq_transfer_number_org"),
            # One transfer in flight per asset. Two open transfers for one laptop
            # would let two people each be approved to receive it, and whichever
            # completed second would silently undo the first.
            models.UniqueConstraint(
                fields=["item"],
                condition=Q(status__in=["draft", "dept_head_review", "hr_review",
                                        "admin_approval"]),
                name="uniq_open_transfer_per_item"),
        ]

    def __str__(self):
        return f"{self.transfer_number} · {self.item_code} ({self.status})"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    def get_rejected_stage_display_safe(self):
        """The label of the gate that rejected it, or "review" if unknown."""
        try:
            return self.Status(self.rejected_stage).label
        except ValueError:
            return "review"


class AssetTransferAttachment(models.Model):
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
    transfer = models.ForeignKey(
        AssetTransfer, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to=transfer_attachment_path, max_length=255)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    uploaded_by_name = models.CharField(max_length=150, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]


class AssetTransferEvent(models.Model):
    """
    The transfer's own audit trail. Append-only: nothing updates or deletes a row.

    Separate from AssetLifecycleEvent because the two answer different questions.
    The lifecycle log is about the ASSET ("who has held this laptop"); this one is
    about the DECISION ("who approved moving it, and when"). A rejected transfer
    changes nothing about the asset and belongs only here.
    """

    class Action(models.TextChoices):
        CREATED = "created", "Transfer created"
        SUBMITTED = "submitted", "Submitted"
        DEPT_HEAD_APPROVED = "dept_head_approved", "Approved by Department Head"
        HR_APPROVED = "hr_approved", "Approved by HR"
        ADMIN_APPROVED = "admin_approved", "Approved by Admin"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"
        OWNER_CHANGED = "owner_changed", "Owner changed"
        DEPARTMENT_CHANGED = "department_changed", "Department changed"
        ATTACHMENT_ADDED = "attachment_added", "Attachment added"

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
    transfer = models.ForeignKey(
        AssetTransfer, on_delete=models.CASCADE, related_name="events")
    sequence = models.PositiveIntegerField(default=0)
    action = models.CharField(max_length=24, choices=Action.choices, db_index=True)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    actor_name = models.CharField(max_length=150, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "sequence"]


def disposal_attachment_path(instance, filename):
    # Phase S3: tenant-scoped, via the one shared standard in
    # tenancy.storage. The function NAME and LOCATION are unchanged, so
    # Django's serialised `upload_to` import path still resolves and NO
    # migration is needed. Files already stored keep their old paths and
    # still resolve -- the media view looks a file up by its stored name.
    from tenancy.storage import upload_path

    return upload_path(instance, filename, module="inventory",
                       kind=f"disposals/{instance.disposal_id}",
                       dated=False, unguessable=False)


class AssetDisposal(models.Model):
    """
    An APPROVED removal of an asset from the books (Phase ASSET-LIFECYCLE-DISPOSAL).

        DRAFT -> DEPT_HEAD_REVIEW -> ADMIN_APPROVAL -> DISPOSED
                        \\__________________\\________-> REJECTED
        (any open state, by the requester) -------------> CANCELLED

    The asset is NEVER deleted. On final approval the existing `lifecycle.dispose`
    moves it to DISPOSED - still on file, with its whole history - and this record
    keeps who asked, why, who approved it, and what it was worth at that moment.

    WHY THE FIGURES ARE SNAPSHOTS
    -----------------------------
    Book value is normally derived, never stored (depreciation.py). At disposal it
    is FROZEN here, because a write-off is a historic fact: if somebody later
    corrected the asset's cost or useful life, a recomputed figure would silently
    rewrite a loss the organisation has already reported.
    """

    class DisposalType(models.TextChoices):
        SALE = "sale", "Sold"
        SCRAP = "scrap", "Scrapped"
        DONATION = "donation", "Donated"
        RECYCLE = "recycle", "E-waste recycling"
        WRITE_OFF = "write_off", "Written off"
        LOST = "lost", "Lost or stolen"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        DEPT_HEAD_REVIEW = "dept_head_review", "Department Head Review"
        ADMIN_APPROVAL = "admin_approval", "Admin Approval"
        DISPOSED = "disposed", "Disposed"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    REVIEW_STAGES = (Status.DEPT_HEAD_REVIEW, Status.ADMIN_APPROVAL)
    OPEN_STATUSES = frozenset({Status.DRAFT, *REVIEW_STAGES})
    # Types that bring no money back: the whole book value is written off.
    NO_PROCEEDS_TYPES = frozenset({DisposalType.SCRAP, DisposalType.DONATION,
                                   DisposalType.WRITE_OFF, DisposalType.LOST})

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
    disposal_number = models.CharField(max_length=20, editable=False)

    item = models.ForeignKey(
        InventoryItem, on_delete=models.PROTECT, null=True, blank=True,
        related_name="disposals",
        help_text="PROTECT: an asset with a disposal record can never be deleted.")
    item_code = models.CharField(max_length=32, blank=True, default="")
    item_name = models.CharField(max_length=150, blank=True, default="")
    department_name = models.CharField(max_length=150, blank=True, default="")

    disposal_type = models.CharField(max_length=12, choices=DisposalType.choices)
    reason = models.TextField()
    expected_proceeds = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="What a sale is expected to raise. Ignored for types with no proceeds.")
    remarks = models.TextField(blank=True, default="")

    # Who held it when it was reported lost - the chain of custody's last link.
    last_holder = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    last_holder_name = models.CharField(max_length=150, blank=True, default="")

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_disposals_requested")
    requested_by_name = models.CharField(max_length=150, blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)

    dept_head_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    dept_head_name = models.CharField(max_length=150, blank=True, default="")
    dept_head_at = models.DateTimeField(null=True, blank=True)
    dept_head_remarks = models.TextField(blank=True, default="")

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="asset_disposals_approved")
    approved_by_name = models.CharField(max_length=150, blank=True, default="")
    approved_at = models.DateTimeField(null=True, blank=True)
    admin_remarks = models.TextField(blank=True, default="")

    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    rejected_by_name = models.CharField(max_length=150, blank=True, default="")
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_stage = models.CharField(max_length=20, blank=True, default="")
    rejection_remarks = models.TextField(blank=True, default="")

    # Frozen at the moment of disposal.
    disposed_at = models.DateTimeField(null=True, blank=True)
    purchase_cost_at_disposal = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    accumulated_at_disposal = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    book_value_at_disposal = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True)
    proceeds = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"], name="disposal_status_idx")]
        constraints = [
            # Phase S4: was unique=True on `disposal_number` alone -- a GLOBAL namespace.
            # Phase S3 already gives each tenant its own document prefix, so a
            # collision was impossible in practice; the composite makes it
            # impossible by construction and removes the last way one tenant's
            # numbering could refuse another tenant's insert.
            models.UniqueConstraint(fields=["organization", "disposal_number"],
                                    name="uniq_disposal_number_org"),
            # One disposal in flight per asset: two approved disposals of one laptop
            # would each record a write-off, and the loss would be reported twice.
            models.UniqueConstraint(
                fields=["item"],
                condition=Q(status__in=["draft", "dept_head_review", "admin_approval"]),
                name="uniq_open_disposal_per_item"),
        ]

    def __str__(self):
        return f"{self.disposal_number} · {self.item_code} ({self.status})"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES

    @property
    def written_off(self):
        """Book value not recovered. Zero when a sale raised more than book value."""
        if self.book_value_at_disposal is None:
            return None
        return max(self.book_value_at_disposal - (self.proceeds or 0), 0)

    @property
    def gain_on_disposal(self):
        if self.book_value_at_disposal is None:
            return None
        return max((self.proceeds or 0) - self.book_value_at_disposal, 0)

    def get_rejected_stage_display_safe(self):
        try:
            return self.Status(self.rejected_stage).label
        except ValueError:
            return "review"


class AssetDisposalAttachment(models.Model):
    """Evidence: a buyer's receipt, a scrap certificate, a police report for a theft."""
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
    disposal = models.ForeignKey(
        AssetDisposal, on_delete=models.PROTECT, related_name="attachments")
    file = models.FileField(upload_to=disposal_attachment_path, max_length=255)
    original_name = models.CharField(max_length=255)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    uploaded_by_name = models.CharField(max_length=150, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]


class AssetDisposalEvent(models.Model):
    """The disposal decision's own append-only audit trail."""

    class Action(models.TextChoices):
        CREATED = "created", "Disposal requested"
        SUBMITTED = "submitted", "Submitted"
        DEPT_HEAD_APPROVED = "dept_head_approved", "Approved by Department Head"
        ADMIN_APPROVED = "admin_approved", "Approved by Admin"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        CUSTODY_CLOSED = "custody_closed", "Custody closed"
        DISPOSED = "disposed", "Asset disposed"
        ATTACHMENT_ADDED = "attachment_added", "Attachment added"

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
    disposal = models.ForeignKey(
        AssetDisposal, on_delete=models.PROTECT, related_name="events")
    sequence = models.PositiveIntegerField(default=0)
    action = models.CharField(max_length=24, choices=Action.choices, db_index=True)
    from_status = models.CharField(max_length=20, blank=True, default="")
    to_status = models.CharField(max_length=20, blank=True, default="")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    actor_name = models.CharField(max_length=150, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "sequence"]
