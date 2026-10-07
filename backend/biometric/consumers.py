"""WebSocket consumer for live attendance.

Authentication rides in the ``Sec-WebSocket-Protocol`` header rather than a query
string. Browsers cannot set arbitrary headers on a handshake, so the usual
workaround is ``?token=<jwt>`` — but nginx logs the full request line by default,
which would write a valid 30-minute JWT into the access log of both proxy layers
(one of which lives in another repo). The subprotocol carries the same token in a
header that nothing logs.

    client:  new WebSocket(url, ["jwt", accessToken])
    server:  scope["subprotocols"] == ["jwt", "<token>"]
"""
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken

from .events import (
    EVENT_CONNECTION_READY,
    group_all,
    group_devices,
    group_for_department,
    group_for_user,
)

logger = logging.getLogger(__name__)

SUBPROTOCOL = "jwt"

# Close codes in the 4000-4999 private range, so the client can tell an auth
# failure (do not retry with this token) from a transport drop (do retry).
CLOSE_UNAUTHORIZED = 4401


@database_sync_to_async
def _user_from_token(raw_token):
    """Resolve a JWT to an active user, or None.

    Deliberately re-checks ``is_active``: a token stays cryptographically valid
    for its full 30 minutes after an account is disabled, and a deactivated
    employee must not keep streaming attendance.
    """
    User = get_user_model()
    try:
        token = AccessToken(raw_token)
        user = User.objects.select_related("department_ref").get(pk=token["user_id"])
    except (InvalidToken, TokenError, KeyError, User.DoesNotExist, ValueError, TypeError):
        return None
    return user if user.is_active else None


class AttendanceConsumer(AsyncJsonWebsocketConsumer):
    """Streams attendance events, scoped to what the user may already see."""

    user = None
    joined_groups = ()

    async def connect(self):
        raw_token = self._token_from_subprotocols()
        if not raw_token:
            await self.close(code=CLOSE_UNAUTHORIZED)
            return

        self.user = await _user_from_token(raw_token)
        if self.user is None:
            await self.close(code=CLOSE_UNAUTHORIZED)
            return

        self.joined_groups = self._groups_for(self.user)
        for group in self.joined_groups:
            await self.channel_layer.group_add(group, self.channel_name)

        # Echoing the subprotocol is required — a browser aborts the connection
        # if the server accepts without confirming one it offered.
        await self.accept(subprotocol=SUBPROTOCOL)
        await self.send_json({
            "type": EVENT_CONNECTION_READY,
            "data": {"groups": list(self.joined_groups), "user": str(self.user.pk)},
        })
        logger.info("WebSocket connected: user=%s groups=%s", self.user.pk, self.joined_groups)

    async def disconnect(self, code):
        for group in self.joined_groups or ():
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """The client sends nothing but keepalives.

        This socket is deliberately one-directional: every state change goes
        through the authenticated REST API, so there is no command surface here
        to get authorisation wrong on.
        """
        if isinstance(content, dict) and content.get("type") == "ping":
            await self.send_json({"type": "pong"})

    # -- group scoping ----------------------------------------------------

    @staticmethod
    def _groups_for(user):
        """Mirror of AttendanceListView's role scoping, scoped to the tenant.

        HR/Admin -> everything and device health.
        Department head -> their own department.
        Employee -> only themselves.

        EVERY GROUP IS THIS USER'S TENANT'S (Phase S3). The organization comes
        off the authenticated user's own row -- never from the handshake, which
        the client controls -- so a socket can only ever be subscribed to the
        tenant its token belongs to. Before this, an HR user at any company
        joined the platform-wide "attendance.all" and received every other
        company's punches live.

        A user with no organization (a platform account, or an account created
        before the Phase S2 backfill) gets the UNRESOLVED token, which is a
        real namespace that no tenant publishes to -- so such a socket connects
        and receives nothing, rather than receiving everything.
        """
        from users.models import User

        organization = getattr(user, "organization_id", None)

        groups = [group_for_user(user.pk, organization)]
        if user.role in (User.Roles.APPROVER, User.Roles.ADMIN):
            groups += [group_all(organization), group_devices(organization)]
        elif user.role == User.Roles.BOD:
            # Every employee's attendance, but not device health: a terminal is
            # infrastructure, and the Board configures none (Phase BOD).
            groups.append(group_all(organization))
        elif user.role == User.Roles.CHECKER and user.department_ref_id:
            groups.append(group_for_department(user.department_ref_id,
                                               organization))
        return tuple(dict.fromkeys(groups))  # dedupe, keep order

    def _token_from_subprotocols(self):
        """Pull the token out of ["jwt", "<token>"]."""
        protocols = self.scope.get("subprotocols") or []
        if len(protocols) >= 2 and protocols[0] == SUBPROTOCOL:
            return protocols[1]
        return None

    # -- event handlers ---------------------------------------------------
    # Channels maps a message type to a method by replacing "." with "_", so
    # "attendance.punch.v1" arrives here as attendance_punch_v1.

    async def _forward(self, message):
        await self.send_json({"type": message["type"], **message["payload"]})

    async def attendance_punch_v1(self, message):
        await self._forward(message)

    async def attendance_updated_v1(self, message):
        await self._forward(message)

    async def device_online_v1(self, message):
        await self._forward(message)

    async def device_offline_v1(self, message):
        await self._forward(message)
