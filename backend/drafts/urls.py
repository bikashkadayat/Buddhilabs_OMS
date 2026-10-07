"""
Draft autosave routes (Phase 111).

`document_key` is a UUID string or the literal "new", so it is matched as a
loose slug rather than <uuid:...>: a create form has no document id yet and must
still have somewhere to autosave.
"""
from django.urls import path

from .views import (
    DraftDetailView, DraftListView, DraftRecoveredView, DraftRestoreView,
    DraftVersionView,
)

urlpatterns = [
    path("drafts/", DraftListView.as_view(), name="draft-list"),
    path("drafts/<str:kind>/<str:document_key>/",
         DraftDetailView.as_view(), name="draft-detail"),
    path("drafts/<str:kind>/<str:document_key>/restore/",
         DraftRestoreView.as_view(), name="draft-restore"),
    path("drafts/<str:kind>/<str:document_key>/recovered/",
         DraftRecoveredView.as_view(), name="draft-recovered"),
    path("drafts/<str:kind>/<str:document_key>/versions/<int:version>/",
         DraftVersionView.as_view(), name="draft-version"),
]
