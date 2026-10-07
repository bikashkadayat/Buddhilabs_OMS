"""Signed, expiring URLs for ALL user-uploaded media (memo attachments/vouchers,
profile photos, generated report files).

Why signed URLs: the SPA authenticates with a Bearer JWT header, which a browser
cannot attach to a native <img>/<a> file request. The old design left an
unauthenticated ``/media/`` catch-all open to work around that — exposing every
upload. Instead, the server issues short-lived HMAC-signed URLs (only to
already-authenticated/authorized requests, e.g. inside a memo serializer that ran
CanViewMemo), and this view validates the signature — no header needed, nothing
public.

Phase 11 (audit findings M3 + M7) changed two things:

* **The signature is bound to a user.** Previously it covered only
  ``name|expiry``; it now covers ``name|expiry|user_id``. Links issued without a
  user (server-side jobs, email attachments) still work and are marked as such.

  **What that does and does not buy — stated precisely, because the wording here
  was wrong before and a reader acted on it.** Including the user id makes the
  link *unforgeable for a different user*: an attacker cannot take a link, swap
  ``u`` for someone else's id and have it validate. It does **not** stop replay.
  This view is deliberately unauthenticated (``AllowAny``, no authentication
  classes) because a browser cannot attach an Authorization header to a native
  ``<img>``/``<a>`` request — that is the whole reason signed URLs exist here.
  Nothing therefore compares the *caller* to ``u``, and a link that leaks by the
  exact routes named above — a pasted chat message, a shared screen, browser
  history — still works for whoever holds it.

  So these URLs are **time-boxed bearer capabilities**, and the control that
  actually limits the damage is the short TTL below, not the user binding. Treat
  a signed URL as being as sensitive as the file it points at: do not log it, and
  do not paste it anywhere the file itself would not belong.

  Closing the replay gap properly needs a credential the browser *will* send on a
  subresource request — a short-lived cookie scoped to ``/api/v1/media/``, checked
  against ``u``. That is a design change, not a patch, and has not been made.
* **The default TTL dropped from 1 hour to 5 minutes.** An hour was chosen when
  a link was only as sensitive as its filename; a link is used within seconds of
  being issued, so the extra 55 minutes bought nothing but exposure.
* **Downloads are audited**, so reading a file is a recorded act.
"""
import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponseForbidden
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from config.uploads import harden_file_response
from tenancy.uploads import PLATFORM_PREFIX

logger = logging.getLogger(__name__)

# Anonymous links (issued by a background job with no user in hand) sign with
# this sentinel, so an anonymous link and a user link can never collide.
ANONYMOUS = "-"


def _sign(name, exp, user_id=ANONYMOUS, org=ANONYMOUS):
    msg = f"{name}|{exp}|{user_id}|{org}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def _unsafe_path(name):
    """Paths that must never be signed or served, whoever asks.

    PHASE S10 PART 2. The serving view has refused these since Phase 11;
    the SIGNER did not, so `org/<mine>/../<theirs>/secret.pdf` produced a
    perfectly valid link. It was not exploitable -- the same check at the
    far end answered 403 -- but the asymmetry is the exact shape of the
    Phase S9 branding bug, where one end normalised a token and the other
    did not and the feature silently broke for every customer.

    Minting a link that cannot be served is also indistinguishable, from
    the caller's side, from a feature that is broken. Refusing at both ends
    means the attempt is refused once, loudly, where it was made.
    """
    return (".." in name or name.startswith("/") or name.startswith("\\")
            or "\\" in name)


def path_organization(name):
    """The tenant token a stored path declares, or None for a legacy path.

    New uploads live at ``org/<token>/...`` (tenancy.storage). Files written
    before Phase S3 have no owner in their path at all, which is why this
    returns None rather than guessing -- a legacy path is checked by the
    signature and the issuing view's own authorisation, exactly as before.

    THE SEGMENT IS NORMALISED, AND THAT IS A BUG FIX, NOT TIDYING UP. Two
    spellings of the same organization id reached the media tree:
    ``tenancy.keys.token_for`` strips the hyphens out of the UUID, which is
    what ``tenancy.storage.upload_path`` and every cache key use, while
    ``tenancy.uploads.org_asset_path`` interpolated the id directly and so
    wrote ``org/f5c9a735-1653-.../branding/...``.

    Both ends of the tenant binding compared those strings raw, so for every
    BRANDING asset -- the logos and the favicon -- the comparison was
    "f5c9a735-1653-46ac-a211-aa52ba22ca68" against
    "f5c9a735165346aca211aa52ba22ca68": never equal. ``signed_media_url``
    refused to mint a link for a file the tenant did own, logged it at ERROR
    as a cross-tenant attempt, and returned None -- so a customer who uploaded
    their own login logo was shown the platform's instead. Part 7's "a tenant
    sees only its own branding" failed in the one direction nobody tests for.

    Normalising HERE rather than only at the write side fixes the files
    already stored, which a change to the upload path cannot do.
    """
    if not name:
        return None
    parts = name.split("/", 2)
    if len(parts) >= 2 and parts[0] == "org" and parts[1]:
        return parts[1].replace("-", "").lower()
    return None


def signed_media_url(name, *, ttl=None, download=False, user=None,
                     organization=None):
    """Header-free, expiring URL for the stored file `name`.

    ``user`` binds the link to one account. Callers should always pass it when a
    request is in hand — the argument is optional only because scheduled report
    email has no requesting user, and refusing to issue a link there would break
    a working feature to close a smaller hole than it opens.

    ``organization`` binds the link to one TENANT (Phase S3), and the binding is
    enforced at BOTH ends:

    * **Here, at signing time.** A caller inside tenant A cannot mint a link for
      a path under ``org/<B>/`` -- this refuses and returns None. That is the
      control that actually prevents cross-tenant access, because it stops the
      capability from ever existing. A signature cannot be forged without
      SECRET_KEY, so if no tenant-A code path will sign a tenant-B path, no
      tenant-A user can obtain one.
    * **In the view, at serve time.** The tenant is inside the signed message,
      so the ``o`` parameter cannot be swapped; and it is compared against the
      path's own ``org/<token>/`` segment, so a valid signature for one tenant's
      file cannot be replayed against another's.

    The tenant defaults to the user's own organization, then to the tenant in
    context -- so existing callers gain the binding without being changed.
    """
    if not name:
        return None

    # PLATFORM FILES ARE NEVER SERVABLE HERE (Phase S6.5). A payment proof and
    # a tenant export bundle are stored outside `org/` precisely so this view
    # cannot reach them -- but a path with no `org/<token>/` segment reads as a
    # pre-tenancy LEGACY path, which is deliberately signable. So the
    # convention protected those files only for as long as nobody passed one
    # of their names to this function, and an export bundle is a whole
    # customer's dataset. Refused by prefix instead.
    if name.startswith(PLATFORM_PREFIX):
        logger.error("refusing to sign a platform-only file: %s", name[:120])
        return None

    # Phase S10 Part 2: the same refusal the serving view has always made.
    # `org/<mine>/../<theirs>/secret.pdf` passes the tenant check below,
    # because that reads the FIRST path segment and the traversal is in the
    # third -- so without this the signer happily mints a link for a file
    # inside another tenant's directory. See `_unsafe_path`.
    if _unsafe_path(name):
        logger.error("refusing to sign an unsafe media path: %s", name[:120])
        return None

    org_token = _resolve_org_token(organization, user)

    # SIGN-TIME REFUSAL. The path says who owns the file; if that disagrees
    # with the tenant we are signing for, the only safe answer is no link.
    declared = path_organization(name)
    if declared is not None and org_token not in (ANONYMOUS, declared):
        logger.error(
            "refusing to sign media link: path belongs to tenant %s but the "
            "link was requested for tenant %s (%s)",
            declared, org_token, name[:120])
        return None

    ttl = int(ttl if ttl is not None else getattr(settings, "MEDIA_SIGNED_URL_TTL", 300))
    exp = int(time.time()) + ttl
    user_id = str(getattr(user, "pk", "") or ANONYMOUS)
    query = {"p": name, "e": exp,
             "s": _sign(name, exp, user_id, org_token)}
    if user_id != ANONYMOUS:
        query["u"] = user_id
    if org_token != ANONYMOUS:
        query["o"] = org_token
    if download:
        query["dl"] = "1"
    return f"/api/v1/media/?{urlencode(query)}"


def _resolve_org_token(organization, user):
    """The tenant a link is being minted for.

    Explicit argument first, then the user's own organization, then the tenant
    in context. ANONYMOUS only when none of the three is available -- a
    background job with no request and no user, signing a legacy path.
    """
    from tenancy.keys import token_for

    if organization is not None:
        return token_for(organization)

    user_org = getattr(user, "organization_id", None)
    if user_org is not None:
        return token_for(user_org)

    from tenancy.scoping import active_organization_id

    org_id = active_organization_id(required=False)
    return token_for(org_id) if org_id is not None else ANONYMOUS


class ProtectedMediaView(APIView):
    """Streams a stored file ONLY when the HMAC signature + expiry validate.

    The signature is the credential, so this needs no Authorization header —
    which is the whole point, since a browser cannot attach one to an <img>.
    """
    permission_classes = [AllowAny]     # signature is the credential
    authentication_classes = []

    # ITS OWN THROTTLE BUCKET.
    #
    # With no authentication classes every caller here is anonymous, so the
    # global default handed this view `AnonRateThrottle` -- 20 requests a
    # minute, keyed by IP. That is a login-page rate applied to file reading:
    # a ten-attachment memo consumed half of it, and every user behind a
    # shared office address drew on the same bucket, so downloads began
    # failing with 429 for reasons no user could see.
    #
    # ScopedRateThrottle with a `media` scope separates reading a document
    # from the anonymous-endpoint budget it was never meant to share.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "media"

    def get(self, request):
        name = request.query_params.get("p", "")
        exp = request.query_params.get("e", "")
        sig = request.query_params.get("s", "")
        bound_user = request.query_params.get("u", ANONYMOUS)
        bound_org = request.query_params.get("o", ANONYMOUS)
        if not (name and exp and sig):
            return HttpResponseForbidden("Missing media signature.")
        try:
            expired = int(exp) < int(time.time())
        except (TypeError, ValueError):
            return HttpResponseForbidden("Invalid media signature.")
        if expired:
            return HttpResponseForbidden("Media link has expired.")

        # The user id AND the tenant are part of the signed message, so neither
        # can be swapped for another's without invalidating the signature.
        if not hmac.compare_digest(sig, _sign(name, exp, bound_user, bound_org)):
            logger.warning("Media signature mismatch for %s", name[:120])
            return HttpResponseForbidden("Invalid media signature.")

        # Defense in depth against path traversal / absolute paths. Shares
        # `_unsafe_path` with the signer (Phase S10) so the two ends cannot
        # drift apart -- which is how one end of the tenant binding came to
        # normalise a token while the other did not, in Phase S9.
        if _unsafe_path(name):
            return HttpResponseForbidden("Invalid media path.")

        # And the platform tree, at the serving end too (Phase S6.5). A
        # signature minted before that refusal existed must not still work.
        if name.startswith(PLATFORM_PREFIX):
            logger.warning("refused a media request for a platform-only "
                           "file: %s", name[:120])
            return HttpResponseForbidden("Invalid media path.")

        # THE TENANT CLAIM MUST MATCH THE PATH (Phase S3).
        #
        # A link is a bearer capability: within its TTL the signature IS the
        # credential, and this view is deliberately unauthenticated because a
        # browser cannot attach an Authorization header to an <img>. So the
        # question is not "who is asking" but "what may this link address".
        #
        # A path under org/<token>/ names its owner. If the link's tenant claim
        # disagrees, the link is being used against a file it was not minted
        # for, and that is refused regardless of how it was obtained.
        #
        # A legacy path (no org/ segment, written before Phase S3) declares no
        # owner, so there is nothing to compare; those remain protected by the
        # signature, the short TTL and the authorisation the issuing view ran.
        declared = path_organization(name)
        if declared is not None and bound_org != declared:
            logger.warning(
                "cross-tenant media request refused: path owner %s, link "
                "tenant %s, file %s", declared, bound_org, name[:120])
            return HttpResponseForbidden("Invalid media signature.")
        if not default_storage.exists(name):
            raise Http404("File not found.")

        self._audit(name, bound_user, request, bound_org)

        download = request.query_params.get("dl") == "1"
        response = FileResponse(
            default_storage.open(name, "rb"),
            as_attachment=download,
            filename=name.rsplit("/", 1)[-1],
        )
        # CACHEABLE FOR AS LONG AS THE SIGNATURE IS VALID, AND NOT ONE SECOND MORE.
        #
        # No cache directive was sent at all, so browsers fell back to
        # heuristic caching: an avatar could be re-requested on any remount,
        # and each of those spends the media throttle for no benefit. The
        # opposite mistake would be worse — caching past the signature would
        # leave a copy readable after the link that authorised it had expired
        # — so max-age is the REMAINING life of this URL, not a fixed number.
        #
        # `private` because the response is bound to one user: a shared proxy
        # must never hold it. Clamped at zero for a link presented in its final
        # second, which would otherwise send a negative max-age.
        remaining = max(0, int(exp) - int(time.time()))
        response["Cache-Control"] = f"private, max-age={remaining}"

        # Never let an uploaded HTML/SVG execute in the app origin.
        return harden_file_response(response)

    @staticmethod
    def _audit(name, bound_user, request, bound_org=ANONYMOUS):
        """Record the download (Phase 11, audit finding M3).

        Wrapped: this view has no authentication, so an audit failure must not
        turn into a 500 that makes every image on the page break.
        """
        try:
            from audit.models import AuditLog
            from audit.services import log_action
            from users.models import User

            # `all_tenants`, not `objects`: the id comes from a SIGNED link, so
            # this only decides which account is named, never what anyone may
            # see -- and `objects` refuses outright on a request that resolved
            # to no tenant. That was every platform operator's photo, fetched
            # on the console's own host: the download was served and its
            # audit entry was silently lost, logged here as an ERROR.
            actor = None
            if bound_user and bound_user != ANONYMOUS:
                actor = User.all_tenants.filter(pk=bound_user).first()
            changes = {"event": "MEDIA_DOWNLOADED", "path": name[:250],
                       "bound_to_user": bound_user != ANONYMOUS,
                       "tenant": bound_org}
            if actor is not None and actor.organization_id is None:
                # An operator's row carries no organization, which row-level
                # security admits only under a declared platform scope.
                from tenancy.context import no_tenant

                with no_tenant():
                    log_action(actor, AuditLog.Action.OTHER, changes=changes,
                               request=request)
                return
            log_action(actor, AuditLog.Action.OTHER, changes=changes,
                       request=request)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to audit media download for %s", name[:120])
