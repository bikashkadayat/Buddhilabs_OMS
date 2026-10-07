"""ASGI entry point.

Serves the existing Django HTTP application unchanged and adds a WebSocket
protocol path for live attendance. Everything HTTP keeps going through the same
middleware stack it always has — switching servers must not change how a single
API request behaves.

Run with:
    gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Must be built BEFORE importing anything that touches models: get_asgi_application()
# runs django.setup(), and the routing import chain pulls in consumers -> models.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from biometric.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # AllowedHostsOriginValidator rejects a handshake whose Origin is not in
    # DJANGO_ALLOWED_HOSTS — the WebSocket equivalent of CSRF protection, and the
    # reason a hostile page cannot open an authenticated socket on a user's behalf.
    "websocket": AllowedHostsOriginValidator(
        URLRouter(websocket_urlpatterns)
    ),
})
