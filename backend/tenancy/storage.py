"""The upload-path standard: every new file lands under ``org/<id>/``.

THE RULE
--------
    org/<organization id>/<module>/<kind>/<YYYY>/<MM>/<uuid>/<filename>

One prefix, derived the same way everywhere, so a future tenant inherits it
automatically rather than by somebody remembering.

WHY A PREFIX IS A SECURITY CONTROL AND NOT JUST TIDINESS
--------------------------------------------------------
Four things become possible only once the owner is in the path:

  * per-tenant storage accounting and quota enforcement (there is nothing else
    to count by -- the files have no row to join to in most modules);
  * deleting or exporting one tenant's files on request;
  * a bucket policy or filesystem permission per tenant, if a customer ever
    requires it;
  * and the check in documents.protected_media that a signed link's tenant
    claim matches the path it points at -- which is what stops a link minted
    for one tenant from ever addressing another tenant's file.

EXISTING FILES ARE NOT MOVED, AND NOTHING BREAKS
------------------------------------------------
``upload_to`` is consulted only when a file is WRITTEN. Every row already in
the database keeps the path it was stored under, and those paths still resolve:
the media view looks a file up by its stored name, not by re-deriving it. So
this change is additive -- new uploads are organised, old ones keep working,
and no data migration is needed.

A later phase may backfill old files into the new tree. That is a storage
migration with its own rollback story, not something to fold in here.
"""
import logging
import os
import uuid

from django.utils import timezone

logger = logging.getLogger(__name__)

# Used when no tenant can be resolved at all. Visibly wrong on purpose: a file
# here is a bug to investigate, not a file to serve. While TENANCY_ENABLED is
# False the compatibility shim resolves the single organization, so this should
# never appear.
UNSCOPED = "_unscoped"


def organization_token(instance=None):
    """The tenant segment for a path.

    Prefers the instance's own ``organization_id`` -- correct even with no
    request in hand, which matters because several of these uploads happen in
    background jobs. Falls back to the tenant in context, then to UNSCOPED with
    a warning.
    """
    from .keys import token_for

    org_id = getattr(instance, "organization_id", None)
    if org_id is not None:
        return token_for(org_id)

    # Some Phase B models reach their owner through a parent that IS scoped.
    for attribute in ("organization", "user", "employee", "owner", "uploaded_by"):
        parent = getattr(instance, attribute, None)
        parent_org = getattr(parent, "organization_id", None)
        if parent_org is not None:
            return token_for(parent_org)

    from .scoping import active_organization_id

    org_id = active_organization_id(required=False)
    if org_id is not None:
        return token_for(org_id)

    logger.warning("upload path built with no resolvable organization for %s",
                   type(instance).__name__ if instance is not None else "?")
    return UNSCOPED


# The prefix this module adds is bounded and worth writing down, because it
# consumes a FileField's max_length:
#
#     org/<32-hex>/            37
#     <module>/<kind>/      <= 32
#     YYYY/MM/                  8
#     <32-hex>/                33
#                             ---
#                             110 worst case, before the filename
#
# Every FileField that uses this standard is therefore max_length=255 (Phase
# S3 raised the ten that were still on Django's default of 100). At 100 a
# tenant-scoped path left almost no room for the filename at all, and an
# upload with a long name failed with SuspiciousFileOperation -- found by
# documents/test_protected_media.py the first time this ran.
MAX_PREFIX_LENGTH = 110


def upload_path(instance, filename, *, module, kind="", dated=True,
                unguessable=True):
    """Build a tenant-scoped upload path.

    ``unguessable`` adds a random directory, which every existing module here
    already did: it means knowing a filename is not enough to construct its
    URL, so a file is not readable by guessing even before the signature check.

    The filename is reduced to its BASENAME. A caller passing "a/b/c.png"
    would otherwise nest directories inside the generated path -- which
    lengthens it unpredictably and lets the caller influence the layout of the
    storage tree.
    """
    filename = os.path.basename(str(filename).replace("\\", "/")) or "file"
    parts = [f"org/{organization_token(instance)}", module]
    if kind:
        parts.append(kind)
    if dated:
        parts.append(f"{timezone.now():%Y/%m}")
    if unguessable:
        parts.append(uuid.uuid4().hex)
    parts.append(filename)
    return "/".join(parts)


def make_upload_to(module, kind="", **options):
    """A named ``upload_to`` callable for a model field.

    A named module-level function, not a lambda or a partial: Django serialises
    ``upload_to`` into migrations by import path, and an unnamed callable cannot
    be written down there.
    """
    def _upload_to(instance, filename):
        return upload_path(instance, filename, module=module, kind=kind,
                           **options)

    _upload_to.__name__ = f"upload_to_{module}_{kind}".rstrip("_")
    _upload_to.__qualname__ = _upload_to.__name__
    return _upload_to


# ---------------------------------------------------------------------------
# WHERE THE PER-FIELD CALLABLES LIVE
# ---------------------------------------------------------------------------
#
# Deliberately NOT here. Each `upload_to` callable stays in the app that owns
# the field -- memos.memo_attachment_path, tasks.task_attachment_path,
# inventory.asset_photo_path and so on -- and its body delegates to
# `upload_path` above.
#
# That is what made Phase S3 need only TWO migrations instead of twelve: Django
# serialises `upload_to` into a migration as an IMPORT PATH, so moving a
# callable to a new module is a schema change, while changing what it returns
# is not. The two migrations that do exist (users.0015, minutes.0012) are for
# the only two fields that had a string `upload_to` and so had no callable to
# redirect.
