from django.contrib import admin

from .models import (
    InventoryCategory, InventoryItem, ItemAssignment, TakeOutRequest, InventorySequence)


class NeverDelete:
    """
    Phase ASSET-LIFECYCLE-DISPOSAL. The admin's delete buttons and bulk "delete
    selected" action were a second way to remove an asset - or the custody rows
    that prove who held it - beside the API, which now refuses. An asset leaves the
    books through an approved disposal; custody rows are closed, never removed.
    """

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InventoryCategory)
class InventoryCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name",)


@admin.register(InventoryItem)
class InventoryItemAdmin(NeverDelete, admin.ModelAdmin):
    list_display = ("asset_code", "name", "category", "status", "condition", "department")
    list_filter = ("status", "condition", "category")
    search_fields = ("asset_code", "name", "serial_number")


@admin.register(ItemAssignment)
class ItemAssignmentAdmin(NeverDelete, admin.ModelAdmin):
    list_display = ("item", "assigned_to_name", "assigned_by_name", "is_active", "assigned_at", "returned_at")
    list_filter = ("is_active",)


@admin.register(TakeOutRequest)
class TakeOutRequestAdmin(NeverDelete, admin.ModelAdmin):
    list_display = ("reference", "item_code", "requested_by_name", "purpose", "status", "created_at")
    list_filter = ("status", "purpose")
    search_fields = ("reference", "item_code", "requested_by_name")


@admin.register(InventorySequence)
class InventorySequenceAdmin(NeverDelete, admin.ModelAdmin):
    """Deleting a sequence row would reissue asset codes and reference numbers."""
