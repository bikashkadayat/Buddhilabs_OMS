"""Metric functions: pure ``(scope, window) -> dict``.

No request, no Response, no permission logic anywhere in this package. That is
what lets ``analytics.exports`` call the same function as the dashboard it
exports, so a PDF handed to a director cannot disagree with the screen it came
from -- and it is what makes these cheap to unit-test.
"""
