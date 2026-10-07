from django.urls import path

from .consumers import AttendanceConsumer

websocket_urlpatterns = [
    path("ws/attendance/", AttendanceConsumer.as_asgi()),
]
