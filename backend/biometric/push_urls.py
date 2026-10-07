"""iClock / PUSH routes.

Mounted at the **web root** as ``/iclock/...`` rather than under ``/api/v1/``,
because the paths are hardcoded in the terminal's firmware. There is no version
prefix to add and no way to move them.
"""
from django.urls import path

from .push_views import CdataView, DeviceCmdView, GetRequestView, PingView

urlpatterns = [
    path("iclock/cdata", CdataView.as_view(), name="iclock-cdata"),
    path("iclock/getrequest", GetRequestView.as_view(), name="iclock-getrequest"),
    path("iclock/devicecmd", DeviceCmdView.as_view(), name="iclock-devicecmd"),
    path("iclock/ping", PingView.as_view(), name="iclock-ping"),
    # Firmware is inconsistent about the trailing slash; serve both so a
    # revision that appends one does not silently 404 and drop attendance.
    path("iclock/cdata/", CdataView.as_view()),
    path("iclock/getrequest/", GetRequestView.as_view()),
    path("iclock/devicecmd/", DeviceCmdView.as_view()),
    path("iclock/ping/", PingView.as_view()),
]
