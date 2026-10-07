from rest_framework import serializers

from config.nepali_dates import to_bs
from config.uploads import validate_attachment
from .models import InventoryCategory, InventoryItem, ItemAssignment, TakeOutRequest

# An asset photo is a photo. Allowing pdf/docx here as the shared default does
# would let a document through a field the UI renders in an <img>, and the
# narrower set is enforced on the BYTES, not just the extension.
ASSET_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg"}
# Invoices, warranty cards and delivery notes: the paper that proves an asset was
# bought. Spreadsheets are in because a bulk purchase often arrives as one.
ASSET_DOCUMENT_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "docx", "xlsx"}


class InventoryCategorySerializer(serializers.ModelSerializer):
    item_count = serializers.IntegerField(source="items.count", read_only=True)

    class Meta:
        model = InventoryCategory
        fields = ["id", "name", "description", "item_count", "created_at"]
        read_only_fields = ["id", "created_at"]


class ItemAssignmentSerializer(serializers.ModelSerializer):
    assigned_date_bs = serializers.SerializerMethodField()
    handover_condition_display = serializers.CharField(source="get_handover_condition_display", read_only=True, default="")
    return_condition_display = serializers.CharField(source="get_return_condition_display", read_only=True, default="")

    class Meta:
        model = ItemAssignment
        fields = [
            "id", "item", "item_code", "item_name",
            "assigned_to", "assigned_to_name", "assigned_by",
            "assigned_by_name", "note", "assigned_date", "assigned_date_bs",
            "handover_condition", "handover_condition_display", "accessories",
            "is_handover", "return_condition", "return_condition_display",
            "return_remarks", "assigned_at", "returned_at", "is_active",
        ]
        read_only_fields = fields

    def get_assigned_date_bs(self, obj):
        return to_bs(obj.assigned_date) if obj.assigned_date else None


class AssignmentBoardSerializer(serializers.ModelSerializer):
    """Row for the 'who has what' board / My Assigned Assets — one active
    assignment enriched with the item + holder details needed at a glance."""
    # Prefer the live item, fall back to the snapshot so history stays readable
    # after the asset itself is deleted (item FK is SET_NULL).
    item_code = serializers.SerializerMethodField()
    item_name = serializers.SerializerMethodField()
    item_status = serializers.CharField(source="item.status", read_only=True, default="")
    item_status_display = serializers.CharField(source="item.get_status_display", read_only=True, default="")
    asset_type = serializers.CharField(source="item.get_asset_type_display", read_only=True, default="")
    category = serializers.CharField(source="item.category_id", read_only=True, default=None)
    category_name = serializers.CharField(source="item.category.name", read_only=True, default=None)
    spec = serializers.SerializerMethodField()
    department_name = serializers.SerializerMethodField()
    assigned_date_bs = serializers.SerializerMethodField()
    handover_condition_display = serializers.CharField(source="get_handover_condition_display", read_only=True, default="")

    class Meta:
        model = ItemAssignment
        fields = [
            "id", "item", "item_code", "item_name", "item_status", "item_status_display",
            "asset_type", "category", "category_name", "spec",
            "assigned_to", "assigned_to_name", "department_name",
            "assigned_by_name", "assigned_date", "assigned_date_bs",
            "handover_condition", "handover_condition_display", "accessories",
            "note", "is_handover", "is_active",
        ]
        read_only_fields = fields

    def get_item_code(self, obj):
        return obj.item.asset_code if obj.item_id else (obj.item_code or "")

    def get_item_name(self, obj):
        return obj.item.name if obj.item_id else (obj.item_name or "")

    def get_spec(self, obj):
        it = obj.item
        if not it:
            return ""
        bits = [it.brand, it.model, it.cpu, it.ram]
        return " · ".join(b for b in bits if b) or (it.serial_number or "")

    def get_department_name(self, obj):
        u = obj.assigned_to
        dep = getattr(u, "department_ref", None) if u else None
        return dep.name if dep else (getattr(u, "department", None) or None)

    def get_assigned_date_bs(self, obj):
        return to_bs(obj.assigned_date) if obj.assigned_date else None


class InventoryItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    condition_display = serializers.CharField(source="get_condition_display", read_only=True)
    asset_type_display = serializers.CharField(source="get_asset_type_display", read_only=True)
    is_it_asset = serializers.BooleanField(read_only=True)
    current_holder = serializers.SerializerMethodField()
    current_holder_id = serializers.SerializerMethodField()
    assigned_date = serializers.SerializerMethodField()
    assigned_date_bs = serializers.SerializerMethodField()
    purchase_date_bs = serializers.SerializerMethodField()
    warranty_expiry_bs = serializers.SerializerMethodField()
    warranty_start_bs = serializers.SerializerMethodField()
    warranty_state = serializers.CharField(read_only=True)
    is_terminal = serializers.BooleanField(read_only=True)
    is_in_service = serializers.BooleanField(read_only=True)
    # Phase ASSET-LIFECYCLE-DISPOSAL. Derived on read, never stored.
    amc_state = serializers.CharField(read_only=True)
    effective_useful_life_months = serializers.IntegerField(read_only=True)
    end_of_life_date = serializers.DateField(read_only=True)
    end_of_life_state = serializers.CharField(read_only=True)

    class Meta:
        model = InventoryItem
        fields = [
            "id", "asset_code", "name", "asset_type", "asset_type_display", "is_it_asset",
            "category", "category_name", "serial_number", "department", "department_name",
            "status", "status_display", "condition", "condition_display",
            "purchase_date", "purchase_date_bs", "notes",
            # device specifications
            "brand", "model", "cpu", "ram", "storage_type", "storage_size", "gpu",
            "screen_size", "os", "mac_address", "ip_address", "warranty_expiry",
            "warranty_expiry_bs", "purchase_cost", "vendor", "accessories", "specifications",
            # Phase 70.4 asset master: where it physically is, how long it is
            # covered for, and the paper that proves it was bought.
            "location", "warranty_start", "warranty_start_bs", "warranty_state",
            "photo", "document", "document_name",
            # Phase 70.4 disposal. Read-only here on purpose: disposing of an asset
            # is a lifecycle transition that writes an event, and a PATCH that could
            # set `disposed_at` directly would be a way to dispose of something
            # without the history recording that anybody did.
            "disposed_at", "disposal_method", "disposal_reason", "disposal_value",
            "is_terminal", "is_in_service",
            # Phase ASSET-LIFECYCLE-DISPOSAL: depreciation inputs, the maintenance
            # contract, and the states derived from them.
            "useful_life_months", "salvage_value", "effective_useful_life_months",
            "end_of_life_date", "end_of_life_state",
            "amc_provider", "amc_contract_number", "amc_start", "amc_end", "amc_cost",
            "amc_state",
            # holder-at-a-glance
            "current_holder", "current_holder_id", "assigned_date", "assigned_date_bs",
            "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "asset_code", "created_at", "updated_at",
            "disposed_at", "disposal_method", "disposal_reason", "disposal_value",
        ]

    def validate_photo(self, value):
        return validate_attachment(value, extensions=ASSET_PHOTO_EXTENSIONS)

    def validate_document(self, value):
        return validate_attachment(value, extensions=ASSET_DOCUMENT_EXTENSIONS)

    def validate_status(self, value):
        """
        Status is settable when an asset is REGISTERED and never afterwards.

        Registering is a legitimate choice - an asset already on the shelf starts
        `available`, one still on order starts `procurement`. After that, the
        lifecycle engine is the only thing that writes `status`, and it always
        writes an event naming who caused the change. A PATCH that could set
        `status` directly would be a way to move an asset - to retire it, to take
        it out of somebody's custody - with nothing in the history saying anybody
        did, which is exactly the hole the event log exists to close.
        """
        if self.instance is not None and value != self.instance.status:
            raise serializers.ValidationError(
                "An asset's status cannot be edited directly. Use the lifecycle "
                "actions (receive, check in, assign, return, maintenance, "
                "dispose) so the change is recorded against whoever made it.")
        return value

    def validate(self, attrs):
        """A warranty that ends before it starts is a typo, not a warranty."""
        start = attrs.get("warranty_start",
                          getattr(self.instance, "warranty_start", None))
        expiry = attrs.get("warranty_expiry",
                           getattr(self.instance, "warranty_expiry", None))
        if start and expiry and expiry < start:
            raise serializers.ValidationError(
                {"warranty_expiry": "Warranty expiry cannot be before the "
                                    "warranty start date."})
        # Phase ASSET-LIFECYCLE-DISPOSAL. Added HERE, in the one validate() this
        # class has: a second validate() defined above it was silently replaced
        # by this one, so its checks would never have run.
        amc_start = attrs.get("amc_start", getattr(self.instance, "amc_start", None))
        amc_end = attrs.get("amc_end", getattr(self.instance, "amc_end", None))
        if amc_start and amc_end and amc_end < amc_start:
            raise serializers.ValidationError(
                {"amc_end": "A maintenance contract cannot end before it starts."})
        cost = attrs.get("purchase_cost", getattr(self.instance, "purchase_cost", None))
        salvage = attrs.get("salvage_value", getattr(self.instance, "salvage_value", None))
        if cost is not None and salvage is not None and salvage > cost:
            raise serializers.ValidationError(
                {"salvage_value": "Salvage value cannot be more than the purchase cost."})
        return attrs

    def get_warranty_start_bs(self, obj):
        return to_bs(obj.warranty_start) if obj.warranty_start else None

    def get_current_holder(self, obj):
        a = obj.active_assignment
        return a.assigned_to_name if a else None

    def get_current_holder_id(self, obj):
        a = obj.active_assignment
        return str(a.assigned_to_id) if a and a.assigned_to_id else None

    def get_assigned_date(self, obj):
        a = obj.active_assignment
        return str(a.assigned_date) if a and a.assigned_date else None

    def get_assigned_date_bs(self, obj):
        a = obj.active_assignment
        return to_bs(a.assigned_date) if a and a.assigned_date else None

    def get_purchase_date_bs(self, obj):
        return to_bs(obj.purchase_date) if obj.purchase_date else None

    def get_warranty_expiry_bs(self, obj):
        return to_bs(obj.warranty_expiry) if obj.warranty_expiry else None


class TakeOutRequestSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(read_only=True)
    item_name = serializers.CharField(read_only=True)
    requested_by_name = serializers.CharField(read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    purpose_display = serializers.CharField(source="get_purpose_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    expected_out_date_bs = serializers.SerializerMethodField()
    expected_return_date_bs = serializers.SerializerMethodField()
    actual_return_date_bs = serializers.SerializerMethodField()
    created_at_bs = serializers.SerializerMethodField()

    class Meta:
        model = TakeOutRequest
        fields = [
            "id", "reference", "item", "item_code", "item_name",
            "requested_by", "requested_by_name", "department", "department_name",
            "purpose", "purpose_display", "reason",
            "expected_out_date", "expected_out_date_bs",
            "expected_return_date", "expected_return_date_bs",
            "status", "status_display", "approver", "approver_name",
            "approver_remarks", "action_date",
            "actual_return_date", "actual_return_date_bs", "created_at_bs",
            "created_at", "updated_at",
        ]
        # Server-set fields the client can never write directly.
        read_only_fields = [
            "id", "reference", "item_code", "item_name", "requested_by",
            "requested_by_name", "department", "status", "approver", "approver_name",
            "approver_remarks", "action_date", "actual_return_date",
            "created_at", "updated_at",
        ]

    def _bs(self, d):
        return to_bs(d) if d else None

    def get_expected_out_date_bs(self, obj):
        return self._bs(obj.expected_out_date)

    def get_expected_return_date_bs(self, obj):
        return self._bs(obj.expected_return_date)

    def get_actual_return_date_bs(self, obj):
        return self._bs(obj.actual_return_date)

    def get_created_at_bs(self, obj):
        return self._bs(obj.created_at)


class EligibleAssetSerializer(serializers.ModelSerializer):
    """
    Phase 70.19-G — one row of the take-out asset selector.

    Deliberately NOT InventoryItemSerializer. That one carries ~40 fields including
    purchase price, warranty state, supplier and device specifications; serving it to
    every employee who opens a take-out form would leak the commercial side of the
    register to people who may not read it, and would pay for forty fields to render
    four. This is the projection the selector actually displays, and nothing else.
    """
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    current_holder = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()

    class Meta:
        model = InventoryItem
        fields = [
            "id", "asset_code", "name",
            "category", "category_name",
            "department_name",
            "status", "status_display",
            "current_holder", "is_mine",
        ]
        read_only_fields = fields

    def get_current_holder(self, obj):
        a = obj.active_assignment
        return a.assigned_to_name if a else None

    def get_is_mine(self, obj):
        """
        Lets the selector group "your assets" ahead of stock without the client
        having to compare user ids it would have to be told separately.
        """
        user = self.context.get("request").user if self.context.get("request") else None
        if user is None:
            return False
        a = obj.active_assignment
        return bool(a and a.assigned_to_id == user.id)
