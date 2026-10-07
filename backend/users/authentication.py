"""Authentication backends.

``EmailBackend`` lets people sign in with their email address. Phase S2 made it
tenant-aware: the lookup is scoped to the organization resolved from the
request, so the same address at two companies is two different credentials.
"""
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

from .tenant_login import find_by_email


class EmailBackend(ModelBackend):
    """Sign in with an email address, scoped to the request's tenant.

    Previously this did ``User.objects.get(email=username)`` -- unscoped, and
    able to raise MultipleObjectsReturned straight out of a login attempt. The
    lookup now goes through ``users.tenant_login.find_by_email``, which cannot.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        if username is None:
            username = kwargs.get(User.EMAIL_FIELD)

        if username is None or password is None:
            return None

        user = find_by_email(username, request=request)
        if user is None:
            # Run the default hasher anyway so a missing account and a wrong
            # password take indistinguishable time. Django's ModelBackend does
            # the same thing for the same reason.
            User().set_password(password)
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
