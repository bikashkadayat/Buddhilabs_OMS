"""
Server-side HTML sanitization for memo bodies.

The memo body is authored in a rich-text editor (TipTap) and stored as an HTML
string. Because any authenticated user can author a memo and that HTML is later
rendered to other users in the browser AND into a generated PDF, the raw HTML is
untrusted input and MUST be sanitized before it is persisted (stored-XSS
surface).

THIS MODULE IS THE GATE
-----------------------
Nothing the editor can produce reaches a reader unless it is allowed here. Before
Phase 13 the allowlist was twelve tags with no attributes except on <a>, so a
table, an image, a text colour or an alignment was silently stripped on save -
the author saw their formatting vanish with no error. Widening the editor without
widening this list would have changed nothing at all.

There are three allowlists that must move together, and they are listed here so
the next person finds all three:

  1. this module                              (write path, authoritative)
  2. frontend RichTextEditor PURIFY_CONFIG    (read path, defence in depth)
  3. documents/templates/pdf/base_pdf.html    (styling for what is allowed)

WHAT IS DELIBERATELY STILL REFUSED
----------------------------------
  * <script>, <style>, <iframe>, <object>, event-handler attributes - stripped.
  * javascript: and vbscript: URLs - neutralised.
  * Remote image URLs. Images must be data: URIs of a known raster type. A remote
    <img src> would make the PDF renderer fetch an arbitrary URL from inside our
    network at render time, which is an SSRF primitive, and would leave the
    document dependent on a third party staying online. Self-contained beats
    convenient here.
  * SVG, in any form. An SVG is a script container.
  * Arbitrary CSS. Only the properties in ALLOWED_CSS_PROPERTIES survive, so
    `position: fixed` overlays cannot be smuggled in.
  * `url(...)` in any surviving CSS value. The `background` shorthand is not
    allowed (only `background-color`), and _strip_css_urls() removes url() from
    whatever is left. Both matter: WeasyPrint would FETCH such a URL from inside
    our network when rendering the PDF, which is both an SSRF primitive and a
    read receipt for whoever hosts it.
"""
import re

import bleach
from bleach.css_sanitizer import CSSSanitizer

# Tags the enterprise editor toolbar can emit.
ALLOWED_TAGS = [
    # block + inline text
    "p", "br", "span", "div",
    "strong", "b", "em", "i", "u", "s", "del", "strike",
    "sub", "sup", "code", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "hr",
    # lists, including the checklist TipTap emits as a data-typed <ul>
    "ul", "ol", "li",
    # tables - the headline Phase 13 requirement
    "table", "thead", "tbody", "tfoot", "caption",
    "colgroup", "col", "tr", "th", "td",
    # links and embedded raster images
    "a", "img",
]

# CSS properties the editor needs. Everything else in a style attribute is
# dropped. `position`, `content`, `url()` and friends are absent on purpose.
ALLOWED_CSS_PROPERTIES = [
    "color", "background-color",
    "font-family", "font-size", "font-weight", "font-style",
    "text-align", "text-decoration", "line-height",
    "vertical-align", "white-space",
    "margin", "margin-left", "margin-right", "margin-top", "margin-bottom",
    "padding", "padding-left", "padding-right", "padding-top", "padding-bottom",
    "width", "min-width", "max-width", "height", "min-height",
    "border", "border-top", "border-right", "border-bottom", "border-left",
    "border-color", "border-width", "border-style", "border-collapse",
    "page-break-before", "page-break-after", "break-before", "break-after",
]

# Class names our own stylesheets define. Anything else is dropped, so a class
# attribute cannot be used to borrow unrelated styling from the host page.
ALLOWED_CLASSES = {
    "memo-page-break", "memo-divider",
    "memo-table", "memo-table-bordered", "memo-table-striped",
    "memo-table-financial", "memo-table-compact",
    "text-left", "text-center", "text-right", "text-justify",
    # TipTap's own checklist markup
    "task-list", "task-item",
}

# Raster data URIs only - no SVG, no remote fetches.
_DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=\s]+$", re.I)

# Schemes permitted anywhere a URI can appear. `data` is here for <img src>; the
# per-attribute checks below stop it being used in an <a href>, where
# `data:text/html` would be a stored-XSS vector.
ALLOWED_PROTOCOLS = ["http", "https", "mailto", "data"]

_SAFE_HREF = re.compile(r"^(https?:|mailto:|#|/)", re.I)


def _filter_classes(value):
    """Keep only the class names we actually style."""
    kept = [cls for cls in (value or "").split() if cls in ALLOWED_CLASSES]
    return " ".join(kept)


def _attribute_filter(tag, name, value):
    """
    Per-attribute allowlist.

    A callable rather than a static dict because three of the decisions depend on
    the VALUE, not just the attribute name: an image source must be a raster data
    URI, a link href must not be a data URI, and a class must be one we style.
    """
    if name == "class":
        return bool(_filter_classes(value))
    if name == "style":
        # Values are sanitized property-by-property by the CSSSanitizer below.
        return True

    if tag == "a":
        if name in ("href", "title"):
            # Anchors get http(s)/mailto/fragment/relative only. `data:` here
            # would be a stored-XSS vector even though <img> needs it.
            return name == "title" or bool(_SAFE_HREF.match((value or "").strip()))
        return name in ("rel", "target")

    if tag == "img":
        if name == "src":
            return bool(_DATA_IMAGE.match((value or "").strip()))
        return name in ("alt", "title", "width", "height")

    if tag in ("td", "th"):
        return name in ("colspan", "rowspan", "colwidth", "align", "valign", "scope")
    if tag in ("table", "col", "colgroup"):
        return name in ("width", "align", "border", "cellpadding", "cellspacing", "span")
    if tag in ("ul", "li"):
        # TipTap marks a checklist as data-type="taskList" and each item's state
        # as data-checked; without these a checklist saves as a plain bullet list.
        return name in ("data-type", "data-checked")
    if tag == "div":
        return name == "data-type"

    return False


_CSS_URL = re.compile(r"url\s*\(", re.I)


def _strip_css_urls(html):
    """
    Drop any style declaration still containing url().

    Belt and braces behind the property allowlist: a future addition to
    ALLOWED_CSS_PROPERTIES that happens to accept a URL value would otherwise
    quietly reopen the fetch-on-render hole.
    """
    def clean(match):
        declarations = [
            d for d in match.group(2).split(";")
            if d.strip() and not _CSS_URL.search(d)
        ]
        if not declarations:
            return ""
        return f'{match.group(1)}"{";".join(declarations)};"'
    return re.sub(r'(style=)"([^"]*)"', clean, html)


def _drop_empty_images(html):
    """
    Remove <img> tags whose src was rejected.

    Without this a blocked image leaves a bare <img>, which renders as a broken-
    image icon in the memo and an empty box in the PDF - a visible defect where
    the correct outcome is nothing at all.
    """
    return re.sub(r"<img(?![^>]*\ssrc=)[^>]*>", "", html)


def _sanitize_class_values(html):
    """
    Reduce every surviving class attribute to the allowed names.

    bleach's attribute callable can only accept or reject an attribute wholesale,
    so a `class="memo-table evil"` would be kept in full. This second pass trims
    the value itself.
    """
    def trim(match):
        kept = _filter_classes(match.group(2))
        return f'{match.group(1)}"{kept}"' if kept else ""
    return re.sub(r'(class=)"([^"]*)"', trim, html)


def _safe_link(attrs, new=False):
    """linkify callback: force safe rel/target on every anchor."""
    attrs[(None, "rel")] = "noopener noreferrer nofollow"
    attrs[(None, "target")] = "_blank"
    return attrs


def sanitize_html(raw):
    """
    Return a sanitized copy of ``raw`` HTML safe to store and render.

    Strips any tag/attribute outside the allowlist (``strip=True``), removes
    event-handler attributes, neutralises dangerous URL schemes, reduces inline
    CSS to the allowed properties, trims class attributes to names we style, and
    hardens every anchor's rel/target. Returns "" for falsy input.
    """
    if not raw:
        return ""

    cleaned = bleach.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes=_attribute_filter,
        protocols=ALLOWED_PROTOCOLS,
        css_sanitizer=CSSSanitizer(allowed_css_properties=ALLOWED_CSS_PROPERTIES),
        strip=True,
    )
    cleaned = _sanitize_class_values(cleaned)
    cleaned = _strip_css_urls(cleaned)
    cleaned = _drop_empty_images(cleaned)

    # linkify only bare URLs in text; it must not touch anchors we already
    # hardened, and `skip_tags` keeps it out of table markup and code blocks.
    return bleach.linkify(cleaned, callbacks=[_safe_link], skip_tags=["pre", "code"])


# Backwards-compatible alias; memos.sanitizers re-exports this under its own name.
sanitize_memo_html = sanitize_html
