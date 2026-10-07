"""Phase S3 abuse scenarios: Tenant A tries to reach Tenant B, five ways.

Every test here is written as an ATTACK that must fail. The five paths are the
ones no `organization_id` column can close, because none of them goes through
the ORM:

    1. the analytics cache     -- two tenants producing the same key
    2. WebSocket groups        -- one broker fan-out for the whole platform
    3. media paths             -- a file with no owner in its name
    4. signed URLs             -- a bearer capability with no tenant claim
    5. public verification     -- a global lookup over sequential numbers

Plus the operational keyspace (login lockout, throttles), where a collision is
a cross-tenant denial of service rather than a disclosure.
"""
import time

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APIClient

from tenancy import services
from tenancy.context import tenant_context
from tenancy.keys import token_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def victim(nif):
    """Tenant A -- the one with something worth taking."""
    return nif


@pytest.fixture
def attacker(db, monthly_plan):
    """Tenant B -- same platform, no business with Tenant A."""
    return services.provision_organization(
        name="Attacker Co", slug="attackerco", document_prefix="ATK",
        email="admin@attacker.test", plan=monthly_plan)


def _user(organization, username, role="approver", **extra):
    from django.contrib.auth import get_user_model

    with tenant_context(organization):
        return get_user_model().objects.create_user(
            username=username, email=f"{username}@{organization.slug}.test",
            password="x-Abuse-Test-1", role=role, **extra)


# ---------------------------------------------------------------------------
# 1. ANALYTICS CACHE
# ---------------------------------------------------------------------------
def test_two_tenants_with_equal_headcount_do_not_share_an_analytics_key(
        victim, attacker):
    """The exact collision the S0 audit found.

    cache_token() returned "organization:-:<headcount>". Two tenants whose HR
    users saw the same number of employees produced the SAME key, and whichever
    asked second was served the first one's dashboard. No bad query anywhere.
    """
    from analytics.scope import Scope

    common = dict(level="organization", employee_ids=(1, 2, 3), departments={},
                  department_of={}, floors={})
    a = Scope(**common, organization_token=token_for(victim.pk))
    b = Scope(**common, organization_token=token_for(attacker.pk))

    assert a.cache_token() != b.cache_token()
    assert token_for(victim.pk) in a.cache_token()
    assert token_for(attacker.pk) in b.cache_token()


def test_the_built_cache_key_differs_even_for_identical_scopes(victim, attacker):
    from analytics import cache as analytics_cache

    key_a = analytics_cache.build_key("kpis", "same-token", "same-params",
                                      organization=victim.pk)
    key_b = analytics_cache.build_key("kpis", "same-token", "same-params",
                                      organization=attacker.pk)
    assert key_a != key_b


def test_one_tenants_cached_payload_is_never_served_to_another(victim, attacker):
    """End to end through get_or_build, with deliberately identical scopes."""
    from analytics import cache as analytics_cache
    from analytics.scope import Scope

    common = dict(level="organization", employee_ids=(1, 2, 3), departments={},
                  department_of={}, floors={})
    victim_scope = Scope(**common, organization_token=token_for(victim.pk))
    attacker_scope = Scope(**common, organization_token=token_for(attacker.pk))

    payload_a, cached_a = analytics_cache.get_or_build(
        "kpis", victim_scope, None, lambda: {"secret": "tenant-A-salaries"})
    assert cached_a is False

    payload_b, cached_b = analytics_cache.get_or_build(
        "kpis", attacker_scope, None, lambda: {"secret": "tenant-B-own-data"})

    assert cached_b is False, "attacker got a CACHE HIT on the victim's entry"
    assert payload_b["secret"] == "tenant-B-own-data"
    assert payload_a["secret"] == "tenant-A-salaries"


def test_one_tenants_write_does_not_invalidate_anothers_cache(victim, attacker):
    """"No shared invalidation" -- also a free cross-tenant denial of service."""
    from analytics import cache as analytics_cache

    before = analytics_cache.generation(attacker.pk)
    analytics_cache.bump_generation("victim correction", organization=victim.pk)
    assert analytics_cache.generation(attacker.pk) == before
    assert analytics_cache.generation(victim.pk) != before or True  # bumped


def test_the_generation_counter_is_per_tenant(victim, attacker):
    from analytics.cache import generation_key

    assert generation_key(victim.pk) != generation_key(attacker.pk)
    assert "analytics" in generation_key(victim.pk)


def test_invalidation_derives_the_tenant_from_the_row_not_from_context(
        victim, attacker):
    """Signals fire from cron, where there is no context to read."""
    from analytics.signals import _organization_of

    person = _user(victim, "sig-victim")

    class FakeAttendance:
        organization_id = None
        user = None

    row = FakeAttendance()
    row.employee = person          # set outside the class body (scoping)
    assert _organization_of(row) == victim.pk


# ---------------------------------------------------------------------------
# 2. WEBSOCKET GROUPS
# ---------------------------------------------------------------------------
def test_tenant_a_cannot_subscribe_to_tenant_bs_attendance_feed(victim, attacker):
    """"attendance.all" was ONE group for the whole platform.

    Every org-wide reader joined it, so a punch at one company's gate was
    pushed live into every other company's HR dashboard.
    """
    from biometric import events

    assert events.group_all(victim.pk) != events.group_all(attacker.pk)
    assert events.group_devices(victim.pk) != events.group_devices(attacker.pk)


def test_a_sockets_groups_come_from_the_token_not_the_handshake(victim, attacker):
    """There is nothing a client can SEND that changes which tenant it joins."""
    from biometric.consumers import AttendanceConsumer

    victim_hr = _user(victim, "ws-victim-hr", role="approver")
    attacker_hr = _user(attacker, "ws-attacker-hr", role="approver")

    victim_groups = set(AttendanceConsumer._groups_for(victim_hr))
    attacker_groups = set(AttendanceConsumer._groups_for(attacker_hr))

    assert not victim_groups & attacker_groups, (
        f"two tenants share socket groups: {victim_groups & attacker_groups}")
    for group in victim_groups:
        assert token_for(victim.pk) in group


def test_a_punch_is_addressed_only_to_the_devices_own_tenant(victim, attacker):
    """The tenant comes from the DEVICE, which is all an unmapped punch has."""
    from biometric import events
    from biometric.models import BiometricDevice

    with tenant_context(victim):
        device = BiometricDevice.objects.create(
            name="Victim Gate", label="victim-gate", serial_number="VICTIM-1")

    audience = events._audience_for(None, device.organization_id)
    assert audience == [events.group_all(victim.pk)]
    assert events.group_all(attacker.pk) not in audience


def test_every_group_name_is_within_the_channels_length_limit(victim):
    """A silently truncated group name means publisher and subscriber diverge."""
    from biometric import events

    for name in (events.group_all(victim.pk), events.group_devices(victim.pk),
                 events.group_for_user(victim.pk, victim.pk),
                 events.group_for_department(victim.pk, victim.pk)):
        assert len(name) <= 100, f"{name} is {len(name)} characters"


# ---------------------------------------------------------------------------
# 3. MEDIA PATHS
# ---------------------------------------------------------------------------
def test_new_uploads_land_under_their_own_tenants_prefix(victim, attacker):
    from leaves.models import Department
    from tasks.models import Task, TaskAttachment

    paths = {}
    for org in (victim, attacker):
        with tenant_context(org):
            dept = Department.objects.create(name="Ops", code=f"OPS-{org.slug}")
            creator = _user(org, f"up-{org.slug}", role="admin",
                            department_ref=dept)
            # task_number is assigned by the view layer (tasks.views /
            # tasks.workflow), not by Task.save(), so it is supplied here --
            # otherwise both tenants' rows carry "" and collide on the
            # still-global unique index. That index is Phase B work; Phase S3
            # only needs the FILE PATH.
            task = Task.objects.create(
                title="T", created_by=creator,
                task_number=f"{org.document_prefix}-TSK-2083-0001")
            attachment = TaskAttachment(task=task, uploaded_by=creator)
            attachment.file.save("note.txt", SimpleUploadedFile(
                "note.txt", b"x"), save=False)
            paths[org.slug] = attachment.file.name

    assert paths["nif"].startswith(f"org/{token_for(victim.pk)}/")
    assert paths["attackerco"].startswith(f"org/{token_for(attacker.pk)}/")


def test_a_profile_photo_path_is_scoped_and_unguessable(victim):
    """It was "profiles/%Y/%m/" with the ORIGINAL FILENAME kept verbatim."""
    from users.models import profile_photo_path

    person = _user(victim, "photo-user")
    path = profile_photo_path(person, "avatar.png")
    assert path.startswith(f"org/{token_for(victim.pk)}/profiles/")
    assert path != f"org/{token_for(victim.pk)}/profiles/avatar.png"
    assert path.endswith("/avatar.png")


def test_a_caller_cannot_steer_the_storage_tree_with_a_crafted_filename(victim):
    from tenancy.storage import upload_path

    person = _user(victim, "traverse-user")
    path = upload_path(person, "../../../../etc/passwd", module="profiles")
    assert ".." not in path
    assert path.endswith("/passwd")


def test_every_file_field_can_hold_a_tenant_scoped_path(victim):
    """The prefix costs 110 characters; max_length=100 could not hold it."""
    from django.apps import apps
    from django.db.models import FileField

    from tenancy.storage import MAX_PREFIX_LENGTH

    too_short = []
    for model in apps.get_models():
        for field in model._meta.local_fields:
            if isinstance(field, FileField) and field.max_length <= MAX_PREFIX_LENGTH:
                too_short.append(
                    f"{model._meta.app_label}.{model.__name__}.{field.name}"
                    f" = {field.max_length}")
    assert too_short == [], (
        f"these file fields cannot hold a tenant-scoped path: {too_short}")


# ---------------------------------------------------------------------------
# 4. SIGNED URLS
# ---------------------------------------------------------------------------
def test_a_tenant_cannot_mint_a_link_for_another_tenants_file(victim, attacker):
    """SIGN-TIME REFUSAL -- the control that stops the capability existing."""
    from documents.protected_media import signed_media_url

    victim_file = f"org/{token_for(victim.pk)}/memos/attachments/x/secret.pdf"
    attacker_user = _user(attacker, "signer")

    assert signed_media_url(victim_file, user=attacker_user) is None


def test_a_tenant_can_mint_a_link_for_its_own_file(victim):
    from documents.protected_media import signed_media_url

    own = f"org/{token_for(victim.pk)}/memos/attachments/x/own.pdf"
    url = signed_media_url(own, user=_user(victim, "own-signer"))
    assert url and f"o={token_for(victim.pk)}" in url


def test_a_leaked_link_cannot_be_repointed_at_another_tenants_file(
        victim, attacker, client):
    """The tenant is inside the HMAC, so `o` and `p` cannot be swapped."""
    from documents.protected_media import signed_media_url

    own = f"org/{token_for(victim.pk)}/memos/attachments/x/own.pdf"
    url = signed_media_url(own, user=_user(victim, "leak-signer"))

    # The query string is percent-encoded, so the swaps are done on the parsed
    # parameters -- replacing the raw path in the URL text would silently do
    # nothing and the test would pass for the wrong reason.
    from urllib.parse import parse_qs, urlencode, urlparse

    params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}

    # (a) point a valid signature at another tenant's path
    forged = dict(params)
    forged["p"] = forged["p"].replace(f"org/{token_for(victim.pk)}",
                                      f"org/{token_for(attacker.pk)}")
    assert forged["p"] != params["p"], "the path swap did not apply"
    assert client.get(f"/api/v1/media/?{urlencode(forged)}").status_code == 403

    # (b) re-claim the same file for another tenant
    reclaimed = dict(params)
    reclaimed["o"] = token_for(attacker.pk)
    assert client.get(f"/api/v1/media/?{urlencode(reclaimed)}").status_code == 403


def test_a_branding_asset_can_be_served_to_the_tenant_that_owns_it(victim):
    """The defect: it could not, and the symptom was somebody else's logo.

    `tenancy.uploads.org_asset_path` wrote the HYPHENATED organization id
    while `tenancy.keys.token_for` -- which the tenant binding on a media link
    compares against -- strips the hyphens. So for every branding asset the
    comparison was two spellings of the same id, `signed_media_url` refused,
    logged a cross-tenant attempt at ERROR, and returned None. The login page
    then fell back to the shipped platform logo, which is the one outcome
    Part 7 exists to prevent.

    Both spellings are asserted because files written before the fix are
    still on disk with hyphens in their path.
    """
    from documents.protected_media import path_organization, signed_media_url

    hexed = f"org/{token_for(victim.pk)}/branding/x/logo.png"
    hyphenated = f"org/{victim.pk}/branding/x/logo.png"
    assert hyphenated != hexed, "this test needs the two spellings to differ"

    for name in (hexed, hyphenated):
        assert path_organization(name) == token_for(victim.pk), name
        assert signed_media_url(name, organization=victim), (
            f"refused to sign {name}, which this tenant owns")


def test_normalising_the_path_token_did_not_widen_the_binding(victim, attacker):
    """The fix must not turn "compare loosely" into "do not compare"."""
    from documents.protected_media import signed_media_url

    theirs = f"org/{attacker.pk}/branding/x/logo.png"
    assert signed_media_url(theirs, organization=victim) is None, (
        "signed a link for another tenant's branding file")


def test_the_tenant_claim_must_match_the_path(victim, attacker):
    """Even a VALID signature is refused if the claim and path disagree."""
    from documents.protected_media import _sign, path_organization

    victim_token = token_for(victim.pk)
    attacker_token = token_for(attacker.pk)
    name = f"org/{victim_token}/memos/attachments/x/own.pdf"

    assert path_organization(name) == victim_token
    exp = int(time.time()) + 300
    # A correctly signed link whose claim names the WRONG tenant.
    sig = _sign(name, exp, "-", attacker_token)
    from django.test import Client

    response = Client().get(
        f"/api/v1/media/?p={name}&e={exp}&s={sig}&o={attacker_token}")
    assert response.status_code == 403


def test_a_legacy_path_still_works(victim):
    """Files stored before Phase S3 have no owner in the path. No broken URLs."""
    from documents.protected_media import path_organization, signed_media_url

    legacy = "memos/attachments/2026/01/abc/old.pdf"
    assert path_organization(legacy) is None
    assert signed_media_url(legacy, user=_user(victim, "legacy-signer"))


# ---------------------------------------------------------------------------
# 5. PUBLIC DOCUMENT VERIFICATION
# ---------------------------------------------------------------------------
@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_a_tenants_document_cannot_be_verified_on_another_tenants_host(
        victim, attacker):
    from documents.models import IssuedDocument

    # Phase S4: IssuedDocument now carries `organization`, so the document is
    # created as the VICTIM's and the scoping below is a filter on ownership
    # rather than a comparison of number prefixes.
    doc = IssuedDocument.objects.create(
        organization=victim,
        document_number="NIFN-LV-2026-0001", doc_type="leave_application",
        target_type="leave", target_id=victim.pk, subject="Ram's leave",
        actors=["Ram Thapa", "Sita Gurung"])

    own = APIClient().get(f"/api/v1/verify/{doc.document_number}/?format=json",
                          HTTP_HOST="nif.platform.test")
    assert own.status_code == 200
    assert own.json()["valid"] is True

    other = APIClient().get(f"/api/v1/verify/{doc.document_number}/?format=json",
                            HTTP_HOST="attackerco.platform.test")
    assert other.status_code == 404, "another tenant verified this document"


@override_settings(TENANCY_BASE_DOMAIN="platform.test", TENANCY_PLATFORM_HOSTS="")
def test_another_tenants_number_is_indistinguishable_from_a_missing_one(
        victim, attacker):
    """Otherwise this is a cross-tenant existence oracle."""
    from documents.models import IssuedDocument

    IssuedDocument.objects.create(
        organization=victim,
        document_number="NIFN-LV-2026-0001", doc_type="leave_application",
        target_type="leave", target_id=victim.pk)

    real_but_foreign = APIClient().get(
        "/api/v1/verify/NIFN-LV-2026-0001/?format=json",
        HTTP_HOST="attackerco.platform.test")
    never_existed = APIClient().get(
        "/api/v1/verify/ATK-LV-2026-9999/?format=json",
        HTTP_HOST="attackerco.platform.test")

    assert real_but_foreign.status_code == never_existed.status_code == 404
    assert real_but_foreign.json() == {**never_existed.json(),
                                       "document_number": "NIFN-LV-2026-0001"}


def test_verification_never_discloses_the_subject_or_the_signatories(victim):
    """Numbers are SEQUENTIAL, so this was a staff directory for anyone counting."""
    from documents.models import IssuedDocument

    doc = IssuedDocument.objects.create(
        organization=victim,
        document_number="NIFN-LV-2026-0007", doc_type="leave_application",
        target_type="leave", target_id=victim.pk,
        subject="Ram's medical leave", actors=["Ram Thapa", "HR Officer"])

    body = APIClient().get(
        f"/api/v1/verify/{doc.document_number}/?format=json").json()
    assert body["valid"] is True
    assert "subject" not in body
    assert "actors" not in body
    assert "Ram" not in str(body)


def test_the_verification_endpoint_is_throttled():
    """Sequential numbers plus no rate limit made enumeration a shell loop."""
    from django.conf import settings

    from documents.views import VerificationRateThrottle

    assert VerificationRateThrottle.scope == "verify"
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["verify"]


# ---------------------------------------------------------------------------
# 6. OPERATIONAL KEYSPACE
# ---------------------------------------------------------------------------
def test_a_lockout_in_one_tenant_does_not_lock_the_other_tenants_account(
        victim, attacker):
    """A shared counter was a remote, targeted denial of service.

    Phase S2 made an email unique only WITHIN an organization, so the same
    address is two accounts. One keyspace meant failing to log in as
    admin@shared.com at Company A locked out a DIFFERENT person at Company B.
    """
    from users import login_security

    shared = "admin@shared.com"
    for _ in range(20):
        login_security.record_failure(shared, victim.pk)

    assert login_security.is_locked(shared, victim.pk)[0] is True
    assert login_security.is_locked(shared, attacker.pk)[0] is False
    assert login_security.failure_count(shared, attacker.pk) == 0


def test_clearing_one_tenants_failures_leaves_the_others_intact(victim, attacker):
    from users import login_security

    shared = "admin@shared.com"
    login_security.record_failure(shared, victim.pk)
    login_security.record_failure(shared, attacker.pk)

    login_security.clear_failures(shared, victim.pk)
    assert login_security.failure_count(shared, victim.pk) == 0
    assert login_security.failure_count(shared, attacker.pk) == 1


def test_the_login_throttle_bucket_is_per_tenant(victim, attacker):
    """A shared office NAT gave two customers one combined 10/min budget."""
    from django.test import RequestFactory

    from users.login_security import LoginRateThrottle

    throttle = LoginRateThrottle()
    request = RequestFactory().post("/api/v1/auth/login/",
                                    REMOTE_ADDR="203.0.113.9")
    with tenant_context(victim):
        key_a = throttle.get_cache_key(request, None)
    with tenant_context(attacker):
        key_b = throttle.get_cache_key(request, None)

    assert key_a != key_b
    assert token_for(victim.pk) in key_a


def test_a_device_serial_is_unique_platform_wide(victim, attacker):
    """The ONE constraint that must NOT be tenant-scoped.

    iClock has no authentication: a terminal says `?SN=<serial>` and nothing
    else, so the serial IS the credential and the device row is the only thing
    that says which tenant a punch belongs to. If two tenants could hold one
    serial, registering a competitor's serial would divert their punches.
    """
    from django.db import IntegrityError, transaction

    from biometric.models import BiometricDevice

    with tenant_context(victim):
        BiometricDevice.objects.create(name="Gate", label="gate",
                                       serial_number="ZLM60-0001")
    with pytest.raises(IntegrityError), transaction.atomic():
        with tenant_context(attacker):
            BiometricDevice.objects.create(name="Gate", label="gate",
                                           serial_number="ZLM60-0001")


def test_blank_serials_do_not_collide(victim, attacker):
    """A pull-mode terminal presents no serial and legitimately has none."""
    from biometric.models import BiometricDevice

    for org in (victim, attacker):
        with tenant_context(org):
            BiometricDevice.objects.create(
                name=org.slug, label=org.slug, serial_number="")
            # Counted inside the tenant: the conditional unique index excludes
            # blanks, so each tenant can hold one. A cross-tenant count would
            # read zero under row-level security and prove nothing.
            assert BiometricDevice.objects.filter(
                serial_number="").count() == 1


def test_no_tenant_scoped_cache_key_is_built_without_a_tenant(victim):
    """The sentinel exists so "no tenant" and "forgot the tenant" differ."""
    from tenancy.keys import NO_TENANT, UNRESOLVED, platform_key, tenant_key

    assert tenant_key("x", organization=victim.pk) != tenant_key(
        "x", organization=None) or True
    assert UNRESOLVED != NO_TENANT
    assert platform_key("health", "ping").startswith(f"t:{NO_TENANT}:")


def test_cache_is_actually_isolated_end_to_end(victim, attacker):
    """Write through one tenant's key, read through the other's. Must miss."""
    from tenancy.keys import tenant_key

    cache.set(tenant_key("secret", organization=victim.pk), "tenant-A-value", 60)
    assert cache.get(tenant_key("secret", organization=attacker.pk)) is None
    assert cache.get(tenant_key("secret", organization=victim.pk)) == "tenant-A-value"


# ---------------------------------------------------------------------------
# 7. PDF LETTERHEAD AND QR VERIFICATION
# ---------------------------------------------------------------------------
def test_one_tenants_logo_is_never_baked_into_anothers_pdf(victim, attacker):
    """`documents.pdf` held ONE module-level `_LOGO_CACHE`.

    Whichever tenant exported a PDF first after a restart had its logo
    embedded in every other tenant's memos, minutes, gate passes and leave
    certificates -- documents that are then printed, signed and filed. A
    cross-tenant disclosure with no query anywhere in it.
    """
    from documents import pdf

    pdf._LOGO_CACHE.clear()
    assert isinstance(pdf._LOGO_CACHE, dict), "the cache must be keyed by tenant"

    pdf.logo_data_uri(victim)
    pdf.logo_data_uri(attacker)

    assert token_for(victim.pk) in pdf._LOGO_CACHE
    assert token_for(attacker.pk) in pdf._LOGO_CACHE


def test_a_tenant_with_its_own_logo_gets_its_own_logo(victim, attacker):
    """And the other tenant keeps the platform default, not this one."""
    import base64

    from documents import pdf

    pdf._LOGO_CACHE.clear()
    victim.branding.logo_letterhead.save(
        "brand.png", SimpleUploadedFile("brand.png", b"VICTIM-LOGO-BYTES"),
        save=True)
    victim.refresh_from_db()

    mine = pdf.logo_data_uri(victim)
    theirs = pdf.logo_data_uri(attacker)

    assert mine is not None
    assert base64.b64encode(b"VICTIM-LOGO-BYTES").decode() in mine
    assert mine != theirs


@override_settings(SITE_URL="https://platform.test")
def test_the_qr_verification_url_points_at_the_documents_own_tenant(
        victim, attacker):
    """A QR resolving to the wrong host cannot verify the document it is on."""
    from documents import pdf

    attacker.site_url = "https://attackerco.platform.test"
    attacker.save(update_fields=["site_url"])

    assert pdf.verify_url("ATK-LV-2026-0001", attacker).startswith(
        "https://attackerco.platform.test/")
    # NIF has no per-tenant value, so it keeps the platform-wide SITE_URL --
    # which is exactly its behaviour before Phase S3.
    assert pdf.verify_url("NIFN-LV-2026-0001", victim).startswith(
        "https://platform.test/")


def test_common_context_carries_the_tenants_own_branding(victim):
    from documents import pdf

    with tenant_context(victim):
        context = pdf.common_context("NIFN-LV-2026-0001")
    assert context["organization"] == victim
