from django.contrib import admin

from .models import IssuedDocument


@admin.register(IssuedDocument)
class IssuedDocumentAdmin(admin.ModelAdmin):
    list_display = ("document_number", "doc_type", "subject", "issued_by", "is_valid", "issued_at")
    list_filter = ("doc_type", "is_valid")
    search_fields = ("document_number", "subject")
    readonly_fields = ("id", "issued_at")
