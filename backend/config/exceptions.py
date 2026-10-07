"""Human words for the API's stock refusals.

Django REST Framework answers with its own default sentences -- "Request was
throttled. Expected available in 37 seconds.", "You do not have permission
to perform this action.", "No LeaveRequest matches the given query." -- and
the frontend shows the server's sentence verbatim, because the server's
sentence is usually the specific, useful one. For these defaults it is not:
they name an internal model, or a mechanism, and tell the person nothing
they can act on.

ONLY THE DEFAULTS ARE REWRITTEN. A view that raised PermissionDenied("Only
HR can approve this stage.") meant exactly that, and it passes through
untouched. The test is "is this still the framework's own text", so every
message a developer wrote on purpose survives.

The status code and the shape (`{"detail": ...}`) never change, so nothing
that branches on them is affected.
"""
import re

from django.http import Http404
from rest_framework import exceptions
from rest_framework.views import exception_handler as drf_exception_handler

_MODEL_404 = re.compile(r"^No \w+ matches the given query\.?$")

NOT_FOUND = "We couldn't find that. It may have been deleted or moved."
NO_ACCESS = ("You don't have access to do this. Ask your HR team or "
             "administrator if you think you should.")
SIGNED_OUT = "Your session has ended. Please sign in again."
BAD_REQUEST = ("Something about that request wasn't right. Refresh the page "
               "and try again.")


def _wait_message(seconds):
    if not seconds:
        return ("You've made a lot of requests in a short time. Please wait a "
                "moment and try again.")
    seconds = int(seconds + 0.999)
    if seconds <= 90:
        when = f"about {seconds} seconds"
    else:
        minutes = max(1, round(seconds / 60))
        when = f"about {minutes} minute{'s' if minutes != 1 else ''}"
    return ("You've made a lot of requests in a short time. Please wait "
            f"{when} and try again.")


def _is_default(exc):
    """True while the exception still carries the framework's own sentence."""
    detail = getattr(exc, "detail", None)
    return str(detail) == str(getattr(type(exc), "default_detail", object()))


def human_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(response.data, dict):
        return response
    detail = response.data.get("detail")
    if not isinstance(detail, str):
        return response

    message = None
    if isinstance(exc, exceptions.Throttled):
        message = _wait_message(exc.wait)
    elif isinstance(exc, Http404) or isinstance(exc, exceptions.NotFound):
        if _MODEL_404.match(detail) or detail in ("Not found.", "No data."):
            message = NOT_FOUND
    elif isinstance(exc, exceptions.PermissionDenied) and _is_default(exc):
        message = NO_ACCESS
    elif isinstance(exc, (exceptions.NotAuthenticated,
                          exceptions.AuthenticationFailed)) and _is_default(exc):
        message = SIGNED_OUT
    elif isinstance(exc, (exceptions.MethodNotAllowed,
                          exceptions.UnsupportedMediaType,
                          exceptions.ParseError)):
        message = BAD_REQUEST

    if message:
        response.data["detail"] = message
    return response
