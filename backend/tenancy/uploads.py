"""Upload paths for tenancy-owned files.

Two separate trees, and the separation is the point:

* ``org_asset_path``  -- tenant branding (logos, favicons). Tenant-owned, and
  prefixed by organization id so per-tenant storage accounting, quota
  enforcement and bulk deletion are all possible later. Every business module's
  ``upload_to`` will move under the same ``org/<id>/`` prefix in a later phase;
  this app establishes the convention.

* ``payment_proof_path`` -- a payment receipt. Uploaded BY a tenant but read BY
  platform staff, so it crosses the tenant boundary. It deliberately does NOT
  live under ``org/<id>/``: the tenant media tree is served by
  ``documents.protected_media``, which is ``AllowAny`` and validates only an
  HMAC signature. A payment proof must only ever be reachable through a
  platform-admin-authenticated view, so it is stored somewhere that mechanism
  does not reach.

* ``export_bundle_path`` (Phase S6.5) -- a whole tenant's data, in one file.
  Same tree as a payment proof and for a stronger version of the same reason:
  this is the single most sensitive object the platform produces, so it is
  downloadable ONLY through a platform-authenticated view.

  AND THE SIGNER NOW REFUSES THIS PREFIX OUTRIGHT. Keeping a file out of
  ``org/`` was never quite the guarantee the paragraph above claims: a path
  with no ``org/<token>/`` segment reads as a PRE-tenancy legacy path, which
  the signer deliberately permits, so anything that passed one of these names
  to ``signed_media_url`` would have minted a public link to it.
  ``documents.protected_media`` refuses ``platform/`` at both ends, which
  turns a convention that depended on nobody making a mistake into something
  enforced.
"""
import uuid

# The one prefix that is NEVER servable through the media view. Named here,
# beside the paths that use it, and imported by documents.protected_media so
# the two cannot drift.
PLATFORM_PREFIX = "platform/"


def org_asset_path(instance, filename):
    """``org/<token>/branding/<uuid>/<filename>``.

    THROUGH ``token_for``, like every other upload path. Interpolating the id
    directly wrote the HYPHENATED uuid here while `tenancy.storage` wrote the
    hex form everywhere else, and the tenant binding on media links compares
    those two strings -- see `documents.protected_media.path_organization`,
    which is where the consequence is written down. Files already stored keep
    their old paths and still resolve, because that comparison now normalises.
    """
    from .keys import token_for

    org_id = (getattr(instance, "organization_id", None)
              or getattr(instance, "id", None))
    return f"org/{token_for(org_id)}/branding/{uuid.uuid4().hex}/{filename}"


def payment_proof_path(instance, filename):
    """``platform/payments/<organization id>/<uuid>/<filename>``.

    Outside the tenant media tree on purpose -- see the module docstring.
    """
    return (f"platform/payments/{instance.organization_id}/"
            f"{uuid.uuid4().hex}/{filename}")


def export_bundle_path(instance, filename):
    """``platform/exports/<organization id>/<uuid>/<filename>``.

    The uuid directory is not decoration: two exports of the same tenant on
    the same day must not be able to overwrite one another, and a bundle an
    operator has already handed to a customer must stay byte-identical to the
    checksum recorded in its manifest.
    """
    return (f"platform/exports/{instance.organization_id}/"
            f"{uuid.uuid4().hex}/{filename}")


def support_file_path(instance, filename):
    """``platform/support/<organization id>/<uuid>/<filename>``.

    A ticket's screenshot or attachment, uploaded by a customer and read by
    platform staff -- the payment-proof situation exactly, so the same tree:
    outside ``org/``, which the signed media view refuses, and served only by
    the authenticated ticket views.
    """
    ticket = getattr(instance, "ticket", None) or instance
    return (f"platform/support/{ticket.organization_id or 'platform'}/"
            f"{uuid.uuid4().hex}/{filename}")

