from django.urls import path

from .views import verify_document

urlpatterns = [
    path("verify/<str:document_number>/", verify_document, name="verify-document"),
]
