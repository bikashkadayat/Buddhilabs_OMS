"""The caller's own signed-in sessions.

A SESSION HERE IS A LIVE REFRESH TOKEN. Every sign-in outstands one, every
refresh rotates it (the old one is blacklisted), and signing out blacklists
it. So "refresh tokens of mine that are neither expired nor blacklisted" is
exactly the set of places this account is still signed in -- the platform
already keeps that list, it just never showed it to anyone.

What it cannot say is WHERE, or when the session began. SimpleJWT records no
address or browser against a token, and rotation replaces the token on every
refresh, so its ``created_at`` is when the session was last renewed -- "last
active", which is what is reported, rather than a sign-in time it is not. The
one action that matters is offered: end every session but this one.
"""
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _live(user):
    from rest_framework_simplejwt.token_blacklist.models import (
        OutstandingToken,
    )

    return (OutstandingToken.objects
            .filter(user=user, expires_at__gt=timezone.now(),
                    blacklistedtoken__isnull=True)
            .order_by("-created_at"))


class SessionListView(APIView):
    """GET the caller's live sessions. Nobody else's, ever."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([
            {"jti": row.jti, "last_active": row.created_at,
             "expires_at": row.expires_at}
            for row in _live(request.user)])


class EndOtherSessionsView(APIView):
    """POST {refresh}: sign this account out everywhere except here.

    The caller proves which session is "here" by presenting its refresh
    token, which must be valid and must be theirs. Without that, "end the
    others" would have no way to spare the current one.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        from rest_framework_simplejwt.exceptions import TokenError
        from rest_framework_simplejwt.settings import api_settings
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken,
        )

        from tenancy.tokens import TenantSafeRefreshToken

        try:
            current = TenantSafeRefreshToken(request.data.get("refresh") or "")
        except TokenError as exc:
            raise ValidationError(
                {"refresh": "Your session has ended. Please sign in again."}) from exc
        owner = str(current.payload.get(api_settings.USER_ID_CLAIM, ""))
        if owner != str(getattr(request.user, api_settings.USER_ID_FIELD)):
            raise ValidationError(
                {"refresh": "Your session has ended. Please sign in again."})

        keep = current.payload[api_settings.JTI_CLAIM]
        ended = 0
        for row in _live(request.user).exclude(jti=keep):
            BlacklistedToken.objects.get_or_create(token=row)
            ended += 1
        return Response({"ended": ended})
