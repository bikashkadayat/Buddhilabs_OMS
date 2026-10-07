"""Stock API refusals are reworded; deliberate messages are not."""
from django.http import Http404
from rest_framework import exceptions

from config.exceptions import (NO_ACCESS, NOT_FOUND, SIGNED_OUT,
                               human_exception_handler)


def _detail(exc):
    return human_exception_handler(exc, {}).data["detail"]


def test_throttling_says_how_long_in_words():
    message = _detail(exceptions.Throttled(wait=37))
    assert "Expected available" not in message
    assert "about 37 seconds" in message
    assert "about 3 minutes" in _detail(exceptions.Throttled(wait=170))


def test_a_404_never_names_an_internal_model():
    assert _detail(Http404("No LeaveRequest matches the given query.")) == NOT_FOUND
    assert _detail(exceptions.NotFound()) == NOT_FOUND


def test_a_specific_404_passes_through():
    assert _detail(Http404("No workspace with the address 'abc'.")) == \
        "No workspace with the address 'abc'."


def test_the_default_permission_refusal_is_reworded_but_a_written_one_is_kept():
    assert _detail(exceptions.PermissionDenied()) == NO_ACCESS
    assert _detail(exceptions.PermissionDenied("Only HR can approve this stage.")) == \
        "Only HR can approve this stage."


def test_a_missing_session_reads_as_a_session_that_ended():
    assert _detail(exceptions.NotAuthenticated()) == SIGNED_OUT


def test_field_errors_are_left_alone():
    response = human_exception_handler(
        exceptions.ValidationError({"email": ["Enter a valid email address."]}), {})
    assert response.data == {"email": ["Enter a valid email address."]}
