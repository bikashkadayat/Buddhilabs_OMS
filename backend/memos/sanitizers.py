"""
HTML sanitization for memo content.

The allowlist and the sanitizer itself live in ``common.html_sanitizer`` since
Phase TASK-AUTOSAVE-AND-SUBTASKS: tasks needed the same rules for a rich
description, and ``tasks`` may not import ``memos`` (see
tasks/tests/test_independence.py). This module keeps the memo-facing name so
every existing import and test is untouched.
"""
from common.html_sanitizer import (  # noqa: F401
    ALLOWED_CLASSES, ALLOWED_CSS_PROPERTIES, ALLOWED_PROTOCOLS, ALLOWED_TAGS,
    sanitize_html,
)


def sanitize_memo_html(raw):
    """Sanitized copy of ``raw`` HTML safe to store and render."""
    return sanitize_html(raw)
