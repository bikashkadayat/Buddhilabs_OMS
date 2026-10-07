"""
Read and write shapes for the Phase 70 workflows.

A separate module from `serializers.py` for the same reason `lifecycle.py` is
separate from `services.py`: the pre-existing shapes are load-bearing for three
existing test files and a working UI, and mixing four hundred new lines into them
would make the diff unreadable and the regression risk invisible.

Capability flags follow the convention the memo, minute and circular modules
settled on: the UI renders its action bar purely from the server's answer, so every
authorization rule has exactly one home and the client never re-derives who may do
what.
"""
from rest_framework import serializers

from .models import (
    AssetLifecycleEvent, AssetRequest, AssetReturn, InventoryItem,
    MaintenanceTicket,
)


class AssetLifecycleEventSerializer(serializers.ModelSerializer):
    label = serializers.CharField(source="get_event_display", read_only=True)
    actor = serializers.CharField(source="actor_name", read_only=True)

    class Meta:
        model = AssetLifecycleEvent
        fields = ["id", "sequence", "event", "label", "from_status", "to_status",
                  "actor", "subject_name", "remarks", "metadata", "at"]
        read_only_fields = fields


class AssetRequestSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    category_name = serializers.CharField(
        source="requested_category.name", read_only=True, default="")
    department_name = serializers.CharField(
        source="department.name", read_only=True, default="")
    is_open = serializers.BooleanField(read_only=True)
    # Capability flags, per the project's convention.
    can_supervisor_approve = serializers.SerializerMethodField()
    can_inventory_approve = serializers.SerializerMethodField()
    can_hand_over = serializers.SerializerMethodField()
    can_accept = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()

    class Meta:
        model = AssetRequest
        fields = [
            "id", "reference", "item", "item_code", "item_name",
            "requested_category", "category_name",
            "requested_by", "requested_by_name", "department", "department_name",
            "purpose", "needed_by", "status", "status_label", "is_open",
            "supervisor_name", "supervisor_remarks", "supervisor_at",
            "inventory_officer_name", "inventory_remarks", "inventory_at",
            "handed_over_at", "handover_condition", "accessories",
            "accepted_at", "acceptance_remarks",
            "rejected_by_name", "rejection_reason",
            "assignment", "created_at", "updated_at",
            "can_supervisor_approve", "can_inventory_approve", "can_hand_over",
            "can_accept", "can_cancel",
        ]
        read_only_fields = fields

    def _user(self):
        return getattr(self.context.get("request"), "user", None)

    def get_can_supervisor_approve(self, obj):
        from .roles import can_approve_as_supervisor
        user = self._user()
        return bool(user and obj.status == AssetRequest.Status.PENDING
                    and can_approve_as_supervisor(user, obj))

    def get_can_inventory_approve(self, obj):
        from .roles import can_approve_as_inventory
        user = self._user()
        return bool(user
                    and obj.status == AssetRequest.Status.SUPERVISOR_APPROVED
                    and can_approve_as_inventory(user, obj))

    def get_can_hand_over(self, obj):
        from .roles import can_approve_as_inventory
        user = self._user()
        return bool(user
                    and obj.status == AssetRequest.Status.INVENTORY_APPROVED
                    and can_approve_as_inventory(user, None))

    def get_can_accept(self, obj):
        """Only the requester, and only once it is in their hands."""
        user = self._user()
        return bool(user and obj.status == AssetRequest.Status.HANDED_OVER
                    and obj.requested_by_id == user.id)

    def get_can_cancel(self, obj):
        from .roles import can_manage_assets
        user = self._user()
        if user is None or obj.status not in (
                AssetRequest.Status.PENDING,
                AssetRequest.Status.SUPERVISOR_APPROVED,
                AssetRequest.Status.INVENTORY_APPROVED):
            return False
        return obj.requested_by_id == user.id or can_manage_assets(user)


class AssetRequestCreateSerializer(serializers.Serializer):
    """Either a specific asset or a category - the service enforces which."""
    item = serializers.UUIDField(required=False, allow_null=True)
    requested_category = serializers.UUIDField(required=False, allow_null=True)
    purpose = serializers.CharField()
    needed_by = serializers.DateField(required=False, allow_null=True)


class DecisionSerializer(serializers.Serializer):
    approve = serializers.BooleanField(default=True)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    # The inventory officer may substitute the asset - a request for a laptop that
    # has since gone should not be refused when an identical one is on the shelf.
    item = serializers.UUIDField(required=False, allow_null=True)
    accessories = serializers.CharField(required=False, allow_blank=True, default="")


class HandOverSerializer(serializers.Serializer):
    condition = serializers.ChoiceField(
        choices=InventoryItem.Condition.choices, required=False, allow_blank=True)
    accessories = serializers.CharField(required=False, allow_blank=True, default="")
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class RemarksSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField()


class AssetReturnSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    condition_disputed = serializers.BooleanField(read_only=True)
    can_verify = serializers.SerializerMethodField()
    can_inspect = serializers.SerializerMethodField()
    can_accept = serializers.SerializerMethodField()

    class Meta:
        model = AssetReturn
        fields = [
            "id", "reference", "item", "item_code", "item_name", "assignment",
            "returned_by", "returned_by_name", "reason",
            "declared_condition", "declared_remarks",
            "status", "status_label",
            "verified_by_name", "verified_at",
            "inspected_condition", "inspection_remarks", "inspected_at",
            "condition_disputed",
            "accepted_at", "rejection_reason", "returned_date",
            "created_at", "updated_at",
            "can_verify", "can_inspect", "can_accept",
        ]
        read_only_fields = fields

    def _officer(self):
        from .roles import can_manage_assets
        user = getattr(self.context.get("request"), "user", None)
        return bool(user and can_manage_assets(user))

    def get_can_verify(self, obj):
        return self._officer() and obj.status == AssetReturn.Status.REQUESTED

    def get_can_inspect(self, obj):
        return self._officer() and obj.status in (
            AssetReturn.Status.REQUESTED, AssetReturn.Status.VERIFYING)

    def get_can_accept(self, obj):
        return self._officer() and obj.status == AssetReturn.Status.INSPECTED


class AssetReturnCreateSerializer(serializers.Serializer):
    item = serializers.UUIDField()
    reason = serializers.CharField(required=False, allow_blank=True, default="")
    declared_condition = serializers.ChoiceField(
        choices=InventoryItem.Condition.choices, required=False, allow_blank=True)
    declared_remarks = serializers.CharField(
        required=False, allow_blank=True, default="")


class InspectSerializer(serializers.Serializer):
    condition = serializers.ChoiceField(choices=InventoryItem.Condition.choices)
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class MaintenanceTicketSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    priority_label = serializers.CharField(
        source="get_priority_display", read_only=True)
    days_open = serializers.IntegerField(read_only=True)
    is_open = serializers.BooleanField(read_only=True)
    can_manage = serializers.SerializerMethodField()

    class Meta:
        model = MaintenanceTicket
        fields = [
            "id", "reference", "item", "item_code", "item_name",
            "reported_by", "reported_by_name", "issue",
            "priority", "priority_label", "status", "status_label",
            "assigned_to", "assigned_to_name", "vendor",
            "assigned_at", "started_at", "completed_at", "returned_at",
            "resolution", "cost", "condition_after",
            "days_open", "is_open", "created_at", "updated_at", "can_manage",
        ]
        read_only_fields = fields

    def get_can_manage(self, obj):
        from .roles import can_manage_assets
        user = getattr(self.context.get("request"), "user", None)
        return bool(user and can_manage_assets(user) and obj.is_open)


class MaintenanceCreateSerializer(serializers.Serializer):
    item = serializers.UUIDField()
    issue = serializers.CharField()
    priority = serializers.ChoiceField(
        choices=MaintenanceTicket.Priority.choices, required=False,
        default=MaintenanceTicket.Priority.NORMAL)


class MaintenanceAssignSerializer(serializers.Serializer):
    technician = serializers.UUIDField(required=False, allow_null=True)
    vendor = serializers.CharField(required=False, allow_blank=True, default="")
    remarks = serializers.CharField(required=False, allow_blank=True, default="")


class MaintenanceCompleteSerializer(serializers.Serializer):
    resolution = serializers.CharField()
    condition = serializers.ChoiceField(
        choices=InventoryItem.Condition.choices, required=False, allow_blank=True)
    cost = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True)


class DisposeSerializer(serializers.Serializer):
    reason = serializers.CharField()
    method = serializers.CharField(required=False, allow_blank=True, default="")
    value = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True)


class StockSerializer(serializers.Serializer):
    remarks = serializers.CharField(required=False, allow_blank=True, default="")
    location = serializers.CharField(required=False, allow_blank=True, default="")
    condition = serializers.ChoiceField(
        choices=InventoryItem.Condition.choices, required=False, allow_blank=True)
