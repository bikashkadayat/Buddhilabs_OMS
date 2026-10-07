"""
HTML sanitization for circular content.

A thin alias over the memo sanitizer, not a fork. Circular content is authored in
the same TipTap editor and allows exactly what a memo body allows - tables,
financial tables, raster data-URI images, checklists, formatting - so a second
allowlist would be a second thing to keep in step, and the one that fell behind
would be the one that let something through.

The memo sanitizer's module docstring is the authoritative explanation of what is
allowed and what is refused, including why remote images and SVG are rejected
outright (both are fetch-on-render primitives for the PDF engine). Read it there.

If circulars ever need to allow something memos must not, this is where the fork
belongs - and at that point the shared allowlist should move to a common module
rather than being copied.
"""
from memos.sanitizers import sanitize_memo_html


def sanitize_circular_html(raw):
    """Sanitized copy of `raw`, safe to store, render and print."""
    return sanitize_memo_html(raw)
