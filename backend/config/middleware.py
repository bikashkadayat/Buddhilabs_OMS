"""Request-scoped correlation id middleware."""
import uuid

from .logging_utils import set_request_id


class RequestIDMiddleware:
    """
    Assigns each request a correlation id (honouring an inbound X-Request-ID),
    exposes it as request.request_id, echoes it on the response, and makes it
    available to the logging layer via a ContextVar.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        set_request_id(request_id)
        request.request_id = request_id
        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response
