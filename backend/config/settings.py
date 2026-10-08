import os
import sys
from pathlib import Path
from datetime import timedelta

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# H6: fail-closed, prod-safe defaults. DEBUG must be explicitly opted in; the
# insecure fallbacks below are only tolerated in DEBUG or under the test runner,
# and the boot guards refuse to start a real production process configured
# insecurely.
DEBUG = os.getenv('DJANGO_DEBUG', 'False').lower() in ('true', '1', 'yes')
_RUNNING_TESTS = 'pytest' in sys.modules or 'test' in sys.argv
_ALLOW_INSECURE = DEBUG or _RUNNING_TESTS


def _env_bool(name, default):
    """Read a boolean from the environment, falling back to `default`."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('true', '1', 'yes', 'on')


def _env_list(name, default=''):
    """Read a comma-separated list from the environment."""
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if _ALLOW_INSECURE:
        SECRET_KEY = 'django-insecure-nif-portal-secret-key'  # dev / test only
    else:
        raise ImproperlyConfigured(
            'DJANGO_SECRET_KEY must be set when DEBUG is off.'
        )

_default_hosts = '*' if _ALLOW_INSECURE else ''
ALLOWED_HOSTS = [h.strip() for h in os.getenv('DJANGO_ALLOWED_HOSTS', _default_hosts).split(',') if h.strip()]
if not _ALLOW_INSECURE and (not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS):
    raise ImproperlyConfigured(
        'DJANGO_ALLOWED_HOSTS must be an explicit host list (no "*") when DEBUG is off.'
    )

# Daphne's app replaces `runserver` with an ASGI server so a local
# `manage.py runserver` speaks WebSocket. Without it Django serves the WSGI
# stack only, /ws/attendance/ never completes its handshake, and the dashboard
# silently degrades to 20-second polling — which looks like the live feed "just
# being slow" rather than a protocol that never connected. It MUST stay first
# when present, so it is prepended below.
#
# daphne is a DEV-ONLY dependency (requirements/development.txt): production
# serves ASGI through gunicorn/uvicorn loading config.asgi directly and does not
# ship daphne. Listing it unconditionally forces `import daphne` at app-registry
# populate time, which crashes the production container ("No module named
# 'daphne'"). So include it only when it is actually installed.
import importlib.util as _importlib_util

_DAPHNE_APP = ['daphne'] if _importlib_util.find_spec('daphne') else []

INSTALLED_APPS = _DAPHNE_APP + [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party
    # channels must precede staticfiles-dependent apps only in older releases;
    # here it just needs to be installed for the ASGI/WebSocket layer.
    'channels',
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_filters',
    
    # Local Apps
    'users.apps.UsersConfig',
    'leaves.apps.LeavesConfig',
    'memos.apps.MemosConfig',
    'minutes.apps.MinutesConfig',
    'circulars.apps.CircularsConfig',
    'audit.apps.AuditConfig',
    'reports.apps.ReportsConfig',
    'notifications.apps.NotificationsConfig',
    'documents.apps.DocumentsConfig',
    'attendance.apps.AttendanceConfig',
    'inventory.apps.InventoryConfig',
    'biometric.apps.BiometricConfig',
    'analytics.apps.AnalyticsConfig',
    'monitoring.apps.MonitoringConfig',
    'drafts.apps.DraftsConfig',
    # Phase T1. Task Management is deliberately self-contained: it imports
    # no other business module and none imports it. See tasks/models.py.
    'tasks.apps.TasksConfig',
    # Phase T6. A contract and a registry, nothing else: no models, no
    # migrations, no views, and no import of any business module. Modules
    # register themselves as evidence sources; this app never knows they exist.
    'evidence.apps.EvidenceConfig',
    # Phase APM-02. Consumes the evidence contract; computes no metric of its
    # own and modifies no other module.
    'appraisal.apps.AppraisalConfig',
    # Phase S1. The SaaS foundation: Organization, Subscription, Payment.
    # Deliberately imports NO business module -- the dependency points this way
    # only, which is what let the foundation land without touching one of the
    # 106 existing business tables. See tenancy/inventory.py for what the later
    # phases will attach to it.
    'tenancy.apps.TenancyConfig',
]

# ---------------------------------------------------------------------------
# Multi-tenancy (Phase S1 -- FOUNDATION ONLY, ENFORCEMENT OFF)
#
# TENANCY_ENABLED is the master switch, and it is OFF. With it off:
#
#   * TenantResolutionMiddleware resolves a tenant and attaches it to the
#     request, but REFUSES NOTHING -- no 403 on a host mismatch, no 402 on an
#     expired subscription, no rejection of an unresolved host.
#   * An unresolved host falls back to the sole Organization row, so NIF (which
#     is reached today on an IP, on localhost and on its own hostname) keeps
#     working regardless of what Host header arrives.
#
# That fallback is a migration affordance with a flag on it, not a default.
# It is the single thing that makes this phase safe to deploy: every new code
# path is exercised in production while nothing is enforced. The flag flips to
# True only after the Phase S2/S3 isolation work lands and the two-tenant
# conformance suite is green.
TENANCY_ENABLED = _env_bool('TENANCY_ENABLED', False)

# auth.W004: "'User.username' is named as the USERNAME_FIELD, but it is not
# unique."
#
# Correct, and deliberate (Phase S2). Username uniqueness is now PER
# ORGANIZATION -- `uniq_user_org_username` plus `uniq_platform_user_username`
# on users.User -- because two companies must both be able to have an "admin".
# A global unique index is exactly what multi-tenancy has to remove.
#
# Django's hint asks that the authentication backends handle non-unique
# usernames, and they do:
#   * users.authentication.EmailBackend resolves through
#     users.tenant_login.find_by_email, which is tenant-scoped and returns at
#     most one row;
#   * users.models.TenantUserManager.get_by_natural_key narrows by the active
#     organization if a bare username lookup ever matches more than one row.
SILENCED_SYSTEM_CHECKS = ['auth.W004']

# ---------------------------------------------------------------------------
# PostgreSQL Row-Level Security (Phase S5)
#
# RLS is the control that makes isolation fail CLOSED: a policy on the table
# applies to every statement from the application role, whichever manager or
# raw query produced it. A developer who writes Model.objects.all() and forgets
# the tenant gets zero rows, never another tenant's rows.
#
# THREE THINGS MUST ALL BE TRUE or the policies are decorative:
#
#   1. TENANCY_RLS_ENABLED is on, so the middleware binds app.current_org.
#   2. The application connects as a role with NOBYPASSRLS and NOSUPERUSER,
#      and which does NOT own the tables. A superuser or the owner bypasses
#      RLS silently -- pg_policies still lists the policy, and it filters
#      nothing. FORCE ROW LEVEL SECURITY (set by the migration) closes the
#      owner case; the role closes the rest.
#   3. The database is PostgreSQL. SQLite has no row-level security, so on the
#      development/CI database isolation rests on TenantManager alone and the
#      RLS conformance tests skip. That is why the two-tenant gate has to be
#      run against PostgreSQL before a release.
#
# ATOMIC_REQUESTS is required with it. `SET LOCAL` is transaction-scoped --
# which is exactly the property that stops a pooled connection (CONN_MAX_AGE
# is 60 here) carrying one tenant's id into the next request that borrows it.
# Without a transaction around the request there is no LOCAL to scope to.
TENANCY_RLS_ENABLED = _env_bool('TENANCY_RLS_ENABLED', False)

# The guard that enforces the three preconditions lives further down, after
# DATABASES is defined -- see "Row-Level Security preconditions".

# The domain tenant subdomains hang off: nif.<TENANCY_BASE_DOMAIN>.
TENANCY_BASE_DOMAIN = os.getenv('TENANCY_BASE_DOMAIN', '').strip().lower()

# Hostnames that address the PLATFORM CONSOLE rather than any tenant. A tenant
# slug must never shadow one of these -- tenancy.slugs.RESERVED_SLUGS is the
# other half of that guarantee.
TENANCY_PLATFORM_HOSTS = os.getenv('TENANCY_PLATFORM_HOSTS', '').strip().lower()

# --- Phase S9: a verified custom domain is an allowed host ---
#
# ALLOWED_HOSTS is checked inside `HttpRequest.get_host()`, which runs before
# any middleware this project owns, and the block near the top of this file
# refuses to start with '*' in it once DEBUG is off -- correctly.
#
# The consequence, until this: the whole of Phase S9's custom-domain support
# worked and the feature did not. A customer could claim a hostname, publish
# the TXT record, watch it verify, point the name at us, and every request
# was answered "Invalid HTTP_HOST header" having never reached the resolver
# that knew the domain was theirs. The only remedy was an operator editing
# this environment variable and restarting -- the exact manual step that
# self-service custom domains exist to remove.
#
# NOT A WILDCARD. The dynamic entries are the hostnames of TenantDomain rows
# in a serving state, which exist only after DNS confirmed the customer's
# token; anything else is refused exactly as before. The configured patterns
# are iterated FIRST and `validate_host` uses `any()`, so an ordinary request
# on the platform's own domain never looks at the dynamic part.
#
# Only when tenancy is on: the single-tenant deployment has no TenantDomain
# rows to consult and should not grow a cache lookup for them.
if TENANCY_ENABLED:
    from tenancy.allowed_hosts import DynamicAllowedHosts

    ALLOWED_HOSTS = DynamicAllowedHosts(ALLOWED_HOSTS)

# How long a tenant EXPORT BUNDLE is kept before it may be discarded
# (Phase S6.5). Short on purpose, and the opposite default to the tenant's own
# records, which never expire: a bundle is a complete copy of one customer's
# workspace in a single file, regenerable at any time from live data, so every
# extra day it exists is risk with no benefit once the customer has collected
# it. The RECEIPT is kept for ever either way.
# See docs/tenant-retention-and-deletion-policy.md.
TENANCY_EXPORT_RETENTION_DAYS = int(
    os.getenv('TENANCY_EXPORT_RETENTION_DAYS', '30'))

# --- Phase S7: public self-service registration ---
#
# OFF BY DEFAULT, and that is not timidity. Turning this on means an anonymous
# stranger can cause a tenant to exist. The deployment NIF runs today is a
# single-tenant installation where that would be nonsense, so the capability
# ships inert and is switched on by the deployment that wants to sell seats.
TENANCY_PUBLIC_REGISTRATION = os.getenv(
    'TENANCY_PUBLIC_REGISTRATION', '').strip().lower() in ('1', 'true', 'yes')

# How long a verification link is good for. A day: long enough for somebody
# who registered at the end of an afternoon, short enough that a link sitting
# in an abandoned inbox is not a standing offer to create a tenant.
TENANCY_VERIFICATION_TTL_HOURS = int(
    os.getenv('TENANCY_VERIFICATION_TTL_HOURS', '24'))

# The trial a self-registered organization lands on (Phase S7 Part 5).
TENANCY_SELF_SERVICE_TRIAL_DAYS = int(
    os.getenv('TENANCY_SELF_SERVICE_TRIAL_DAYS', '14'))

# How many verification emails one registration may ask for before it has to
# start again. Each one is mail the platform sends to an address that has not
# been proven to belong to the registrant.
TENANCY_VERIFICATION_MAX_SENDS = int(
    os.getenv('TENANCY_VERIFICATION_MAX_SENDS', '3'))

# The absolute base URL the verification link points at, e.g.
# https://app.platform.com. Derived from TENANCY_PLATFORM_HOSTS when empty.
# It must NOT be a tenant subdomain: the workspace does not resolve until the
# link has been used.
TENANCY_PUBLIC_BASE_URL = os.getenv('TENANCY_PUBLIC_BASE_URL', '').strip()

# The slug of the organization an unresolved host falls back to while
# TENANCY_ENABLED is False. Empty = "the only organization that exists", which
# is correct for a single-tenant deployment and refuses to guess once a second
# tenant exists.
TENANCY_DEFAULT_SLUG = os.getenv('TENANCY_DEFAULT_SLUG', '').strip().lower()

# Attendance policy (configurable). Times are Asia/Kathmandu (TIME_ZONE).
ATTENDANCE_OFFICE_START = os.getenv('ATTENDANCE_OFFICE_START', '10:00')     # late after this
ATTENDANCE_FULL_DAY_HOURS = float(os.getenv('ATTENDANCE_FULL_DAY_HOURS', '8'))
ATTENDANCE_HALF_DAY_HOURS = float(os.getenv('ATTENDANCE_HALF_DAY_HOURS', '5'))  # half-day if below
# A day is only counted "Absent" on/after this global date (never before the
# attendance feature was live). Empty = no global floor (per-employee join date
# is always applied regardless). Format: YYYY-MM-DD.
ATTENDANCE_TRACKING_START = os.getenv('ATTENDANCE_TRACKING_START', '')
# Today is only eligible to be "Absent" after this local cut-off (the check-in
# window has closed) — never mid-day. Past days are always eligible.
ATTENDANCE_ABSENT_CUTOFF = os.getenv('ATTENDANCE_ABSENT_CUTOFF', '18:00')
# Date the arrival-based NIF rules (Present <=11:45, Late <13:00, then Half Day)
# take over from the strict office-start rule. Days BEFORE this keep resolving
# the old policy, so re-deriving history never restates it. Empty = the date the
# migration runs; pin it to keep staging and production identical.
ATTENDANCE_POLICY_GO_LIVE = os.getenv('ATTENDANCE_POLICY_GO_LIVE', '')

# Biometric device ingest (Phase 6). BIOMETRIC_MAX_BATCH caps a single ingest
# batch; it is also the chunk size `device_sync` splits a backlog into, so a
# full-backlog pull can never exceed it. Clock skew bounds the replay window on
# signed device requests (the pushed path only).
BIOMETRIC_MAX_BATCH = int(os.getenv('BIOMETRIC_MAX_BATCH', '500'))
BIOMETRIC_CLOCK_SKEW_SECONDS = int(os.getenv('BIOMETRIC_CLOCK_SKEW_SECONDS', '300'))

# In-OMS collector (Phase 12) — `device_sync` pulls from the terminal directly.
# COMM_KEY is 0 on a terminal with no key set, which is the common case; it is
# read from the device menu (Comm > Security > COMM Key) when one is.
#
# `_int_env` rather than int(os.getenv(...)): a commented-out or blanked entry in
# .env yields "" and int("") raises at import, which takes the whole application
# down at start-up over an optional tuning value. Falling back to the default is
# the proportionate response; a bad value is not.
def _int_env(name, default):
    raw = (os.getenv(name) or '').strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


BIOMETRIC_DEVICE_COMM_KEY = _int_env('BIOMETRIC_DEVICE_COMM_KEY', 0)
# Generous by design: a full attendance-log transfer on a busy terminal is slow,
# and a timeout mid-transfer costs a whole retry cycle.
BIOMETRIC_DEVICE_TIMEOUT = _int_env('BIOMETRIC_DEVICE_TIMEOUT', 20)

# Organization-managed devices (Settings -> Biometric Devices). The SERVER
# dials whatever address an organization administrator enters, so loopback
# and link-local (cloud metadata) are always refused. Private ranges are where
# terminals live and are allowed by default; a cloud deployment that reaches
# customer sites only over public addresses or a VPN can switch them off.
# BIOMETRIC_DEVICE_ALLOW_LOOPBACK is for development against the ZK simulator
# or an SSH tunnel -- never set it in production.
BIOMETRIC_DEVICE_ALLOW_PRIVATE_HOSTS = os.getenv(
    'BIOMETRIC_DEVICE_ALLOW_PRIVATE_HOSTS', '1').strip().lower() in ('1', 'true', 'yes')
BIOMETRIC_DEVICE_ALLOW_LOOPBACK = os.getenv(
    'BIOMETRIC_DEVICE_ALLOW_LOOPBACK', '').strip().lower() in ('1', 'true', 'yes')

# iClock / PUSH (Phase 12). The protocol has no authentication — a terminal
# identifies itself with ?SN=<serial> and nothing else, and the firmware cannot
# be made to send a key. Devices are therefore allow-listed by serial number on
# BiometricDevice, and this optionally narrows it further to known source IPs.
# Empty = accept any source IP whose serial is registered. Set it whenever the
# reverse proxy cannot keep /iclock/ off the public internet.
BIOMETRIC_PUSH_ALLOWED_IPS = os.getenv('BIOMETRIC_PUSH_ALLOWED_IPS', '')

# Biometric dashboard (morx) internal API. The backend reads the raw punch data
# from this URL server-side and returns only the requesting employee's rows, so
# the whole-dataset morx service is never exposed to individual employees. The
# default targets the `biometric` service on the Docker network.
BIOMETRIC_INTERNAL_URL = os.getenv('BIOMETRIC_INTERNAL_URL', 'http://biometric:8765')

# ── Location capture ────────────────────────────────────────────────────────
# Office geofence. With coordinates configured, every check-in/out stores its
# straight-line distance from the office, so "were they actually at work?" is
# answered by a number instead of by trusting a reverse-geocoded street name.
# Leave lat/lng empty to disable the geofence entirely (distance stays null).
ATTENDANCE_OFFICE_NAME = os.getenv('ATTENDANCE_OFFICE_NAME', 'Office')
ATTENDANCE_OFFICE_LAT = os.getenv('ATTENDANCE_OFFICE_LAT', '')
ATTENDANCE_OFFICE_LNG = os.getenv('ATTENDANCE_OFFICE_LNG', '')
# Inside this many metres of the office counts as "at the office". Keep it a bit
# larger than the building so a normal GPS fix indoors still lands inside.
ATTENDANCE_OFFICE_RADIUS_M = float(os.getenv('ATTENDANCE_OFFICE_RADIUS_M', '150'))
# Fixes worse than this (metres) are Wi-Fi/IP estimates rather than GPS. They are
# still stored, but flagged as approximate everywhere they are displayed.
ATTENDANCE_MAX_ACCURACY_M = float(os.getenv('ATTENDANCE_MAX_ACCURACY_M', '100'))

# Require a browser location to check in / out. When on (default), a check-in
# with no coordinates is refused so every attendance record carries a location
# for HR/Admin. NOTE: browser geolocation only works over HTTPS (or localhost) —
# on plain HTTP, or on a desktop that can't get a fix, this blocks check-in
# entirely. Set ATTENDANCE_REQUIRE_LOCATION=0 to fall back to optional capture
# (e.g. until production HTTPS is live) so staff are not locked out.
ATTENDANCE_REQUIRE_LOCATION = os.getenv('ATTENDANCE_REQUIRE_LOCATION', '1').lower() not in ('0', 'false', 'no', 'off')

# Reverse geocoding (coordinates -> human-readable place). Set to 0 to store
# coordinates only. Nominatim's public server is rate-limited to ~1 request/sec,
# which is not enough for a morning check-in rush — either point
# ATTENDANCE_NOMINATIM_URL at a self-hosted instance or set a Google Geocoding
# key, which is used in preference when present and resolves building/POI names
# far better in Nepal.
ATTENDANCE_REVERSE_GEOCODE = os.getenv('ATTENDANCE_REVERSE_GEOCODE', '1').lower() not in ('0', 'false', 'no')
ATTENDANCE_NOMINATIM_URL = os.getenv('ATTENDANCE_NOMINATIM_URL', 'https://nominatim.openstreetmap.org/reverse')
ATTENDANCE_GEOCODE_USER_AGENT = os.getenv(
    'ATTENDANCE_GEOCODE_USER_AGENT', 'NIF-OfficeManagement/1.0 (nepalinternetfoundation@gmail.com)')
ATTENDANCE_GOOGLE_GEOCODE_KEY = os.getenv('ATTENDANCE_GOOGLE_GEOCODE_KEY', '')

MIDDLEWARE = [
    # Phase S11 Part 3. FIRST, so it counts the status code that actually
    # reaches the client -- including the 400 Django raises for a disallowed
    # host and the 500 from a view that blew up, neither of which the inner
    # middleware ever sees. Feeds the `saas_5xx_rate` alert; the Phase S10
    # load test produced 339 identical 500s that nothing was counting.
    'monitoring.request_stats.ResponseStatsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise serves the collected static files (Django admin + DRF assets)
    # straight from Gunicorn in production; must sit immediately after
    # SecurityMiddleware and before everything else.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'config.middleware.RequestIDMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # Phase S1. Resolves Host -> Organization and puts it in context. INERT
    # while TENANCY_ENABLED is False: it attaches request.organization and
    # refuses nothing. Sits after AuthenticationMiddleware because enforcement
    # (Phase S3) must compare the JWT's `org` claim against the resolved host,
    # and that needs request.user.
    'tenancy.middleware.TenantResolutionMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# --- Redis: channel layer + shared cache -------------------------------------
# Unset REDIS_URL falls back to in-memory for both, so dev, CI and pytest run
# with no Redis at all — only cross-process fan-out is lost, and a single
# process does not need it.
REDIS_URL = os.getenv('REDIS_URL', '').strip()


def _redis_db(url, db):
    """Point a redis:// URL at a specific database number.

    Keeps the cache off the channel layer's db, so `FLUSHDB` on one can never
    wipe the other.
    """
    from urllib.parse import urlparse, urlunparse

    parts = urlparse(url)
    return urlunparse(parts._replace(path=f'/{db}'))


if REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [_redis_db(REDIS_URL, 0)],
                # Bound the per-channel backlog: a browser that stops reading
                # must not let Redis grow without limit.
                'capacity': int(os.getenv('CHANNEL_LAYER_CAPACITY', '500')),
                'expiry': int(os.getenv('CHANNEL_LAYER_EXPIRY', '30')),
            },
        },
    }
    # Fixes a real pre-existing bug: with no CACHES block Django falls back to
    # per-process LocMemCache, so DRF throttle counters were split across
    # GUNICORN_WORKERS processes and every rate limit was effectively 3x.
    CACHES = {
        'default': {
            # Resilient subclass: throttling runs on every request, so a stock
            # RedisCache would turn a Redis blip into a total API outage. See
            # config/cache.py.
            'BACKEND': 'config.cache.ResilientRedisCache',
            'LOCATION': _redis_db(REDIS_URL, 1),
            'KEY_PREFIX': 'nifn',
        },
    }
else:
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}

# Live attendance tuning.
# BIOMETRIC_BROADCAST_LIMIT: a first backlog sync is ~11k punches; replaying all
# of it into every open browser would be a self-inflicted DoS for no benefit.
BIOMETRIC_BROADCAST_LIMIT = int(os.getenv('BIOMETRIC_BROADCAST_LIMIT', '50'))
BIOMETRIC_DEVICE_OFFLINE_MINUTES = int(os.getenv('BIOMETRIC_DEVICE_OFFLINE_MINUTES', '15'))

# ---------------------------------------------------------------------------
# Task reminders and escalation (Phase T4.4 / T4.5)
#
# The escalation ladder is configuration, not a constant buried in a loop: an
# organisation that wants HR told after three days rather than seven changes a
# number here, and nobody has to find every place a 7 was written down.
#
# Each is DAYS OVERDUE at which that rung fires. They are cumulative — a task
# past the management threshold has already had the supervisor and HR rungs on
# earlier days — so visibility accumulates rather than moving up and going quiet.
TASK_ESCALATION_SUPERVISOR_DAYS = _int_env('TASK_ESCALATION_SUPERVISOR_DAYS', 3)
TASK_ESCALATION_HR_DAYS = _int_env('TASK_ESCALATION_HR_DAYS', 7)
TASK_ESCALATION_MANAGEMENT_DAYS = _int_env('TASK_ESCALATION_MANAGEMENT_DAYS', 14)
# How long submitted work may sit with a reviewer before they are nudged.
# Measured from submission, not from the due date: a review bottleneck is
# invisible on a due-date report, which is why it gets its own reminder.
TASK_REVIEW_PENDING_DAYS = _int_env('TASK_REVIEW_PENDING_DAYS', 2)

# Executive analytics (Phase 10). The kill switch from the rollback plan: with
# this off, analytics.urls registers nothing, every /api/v1/analytics/ path 404s
# and the app is inert. No code deploy needed to disable the whole layer.
ANALYTICS_ENABLED = os.getenv('ANALYTICS_ENABLED', '1').strip().lower() not in (
    '0', 'false', 'no', 'off')

# Database - configured entirely via environment variables (12-factor).
DATABASE_ENGINE = os.getenv('DATABASE_ENGINE', 'postgresql')
if DATABASE_ENGINE == 'sqlite3':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    # No hardcoded credentials: the DB password must come from the environment.
    # In a real (non-DEBUG, non-test) run a missing password fails fast instead
    # of silently falling back to a shared, world-readable default.
    _db_password = os.getenv('DATABASE_PASSWORD')
    if not _db_password:
        if _ALLOW_INSECURE:
            _db_password = 'postgres'  # dev / test only
        else:
            raise ImproperlyConfigured(
                'DATABASE_PASSWORD must be set when DEBUG is off.'
            )
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('DATABASE_NAME', 'leave_system'),
            'USER': os.getenv('DATABASE_USER', 'leave_user'),
            'PASSWORD': _db_password,
            'HOST': os.getenv('DATABASE_HOST', 'localhost'),
            'PORT': os.getenv('DATABASE_PORT', '5432'),
            # Reuse connections across requests (production performance).
            'CONN_MAX_AGE': int(os.getenv('DATABASE_CONN_MAX_AGE', '60')),
        }
    }

# ---------------------------------------------------------------------------
# Row-Level Security preconditions (Phase S5)
#
# Checked HERE rather than beside the flag because both DATABASE_ENGINE and
# DATABASES have to exist first. Failing at import is deliberate: a deployment
# that believes RLS is on when it is not is strictly more dangerous than one
# that refuses to boot.
if TENANCY_RLS_ENABLED:
    if DATABASE_ENGINE == 'sqlite3':
        raise ImproperlyConfigured(
            'TENANCY_RLS_ENABLED requires PostgreSQL; SQLite has no '
            'row-level security, so the policies would silently not exist.')
    # `SET LOCAL app.current_org` is transaction-scoped, which is what stops a
    # pooled connection carrying one tenant's id into the next request. With
    # no transaction around the request there is no LOCAL to scope to.
    #
    # THIS IS NO LONGER WHAT SUPPLIES THAT TRANSACTION (Phase S6), and the
    # reason is a defect it hid. ATOMIC_REQUESTS wraps the VIEW, not the
    # middleware stack -- so the binding, which happens in middleware, ran in
    # autocommit, where PostgreSQL warns and discards a `SET LOCAL`. With RLS
    # on, every tenant query would have returned zero rows in production.
    # TenantResolutionMiddleware now opens the transaction itself and binds
    # inside it.
    #
    # ATOMIC_REQUESTS stays on anyway: it is the right default for a
    # deployment where a request either wholly succeeds or wholly does not,
    # and it costs nothing now that the outer transaction already exists.
    DATABASES['default']['ATOMIC_REQUESTS'] = True

# A reset link works for an hour: long enough to find the email, short enough
# that one sitting in an old inbox is useless. The token also dies the moment
# the password changes or the person signs in (Django's generator).
PASSWORD_RESET_TIMEOUT = int(os.getenv('PASSWORD_RESET_TIMEOUT', '3600'))

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    # Phase 9: reject common/breached passwords and all-numeric passwords.
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Under the test runner ONLY, hash passwords with fast MD5 instead of Django's
# default PBKDF2 (~720k SHA256 rounds, ~150ms each). The suite creates users and
# authenticates thousands of times, and that per-hash cost was the single
# largest contributor to CI wall-clock — dropping it here cuts the backend test
# job by well over half. This NEVER reaches a real process: `_RUNNING_TESTS` is
# true only when pytest is imported or `manage.py test` runs. Production sets no
# PASSWORD_HASHERS at all, so Django keeps its secure default (PBKDF2) — this
# line is simply not defined there.
if _RUNNING_TESTS:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kathmandu'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
# Let WhiteNoise also serve assets straight from the finders under runserver,
# so behaviour matches production even before collectstatic.
WHITENOISE_USE_FINDERS = True

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ---------------------------------------------------------------------------
# Upload ceilings (Phase MEMO-P1-PRODUCTION-BLOCKERS)
#
# READ THIS BEFORE RAISING DATA_UPLOAD_MAX_MEMORY_SIZE TO "FIX" A BIG UPLOAD:
# it does not do what its name suggests here. Django applies it to the request
# body EXCLUDING file upload data, so it caps form fields, not attachments. It
# is set explicitly anyway so the value is a decision rather than a default.
#
# DATA_UPLOAD_MAX_NUMBER_FILES (Django 5.1+) is the one framework control that
# does bound a multipart upload, and it is a backstop only: it applies to every
# endpoint, so it sits well above what any single module allows (memo permits
# 10 files per request, enforced in memos.services). Its job is to stop a
# request with thousands of parts from being parsed at all.
#
# NOTHING IN DJANGO CAPS TOTAL UPLOADED BYTES. That is enforced per module by
# config.uploads.validate_attachment_batch, and should ALSO be enforced at the
# reverse proxy in production -- nginx `client_max_body_size 60m;` -- so an
# oversized body is refused at the edge instead of being buffered by a worker
# and only then rejected.
#
# The two MEMORY_SIZE values below are Django's own defaults, written out so the
# value is a recorded decision rather than an accident. They are deliberately
# NOT tightened: lowering them would change behaviour for every module for no
# gain, since neither one bounds an attachment upload.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2621440              # 2.5MB, form fields only
FILE_UPLOAD_MAX_MEMORY_SIZE = 2621440              # 2.5MB, spool to disk beyond
DATA_UPLOAD_MAX_NUMBER_FILES = 50                  # global backstop (was 100)

# ---------------------------------------------------------------------------
# File storage (Django 4.2+ STORAGES API) - Phase 1 (static) & Phase 2 (media).
#   staticfiles -> WhiteNoise, compressed + content-hashed manifest, so the
#                  Django admin / DRF browsable API load their CSS/JS in prod
#                  with far-future cache headers.
#   default     -> local filesystem by default (mount a persistent volume so
#                  uploads survive redeploys), or an S3-compatible bucket when
#                  USE_S3=True (recommended at scale / multi-instance).
# ---------------------------------------------------------------------------
# Default lifetime (seconds) of signed media URLs (documents.protected_media).
# Phase 11 (audit finding M7): reduced from 3600. A signed link is followed
# within seconds of being issued, so an hour of validity bought nothing but
# exposure for a link that might be pasted into a chat or left in history.
# Links also carry the requesting user's id INSIDE the signed message, which
# means a link cannot be re-pointed at another account (the signature breaks)
# and every download is attributable. It does NOT mean a leaked link is useless
# to its finder: ProtectedMediaView is deliberately unauthenticated - a browser
# cannot attach an Authorization header to an <img> - so within the TTL the
# signature IS the credential and whoever holds it can fetch the file. The short
# TTL above is the mitigation, and it is the reason this value should stay small.
# Phase 46 item 7 verified this by fetching a signed link as a different user and
# unauthenticated; minutes/tests/test_security.py pins the real behaviour.
MEDIA_SIGNED_URL_TTL = int(os.getenv('MEDIA_SIGNED_URL_TTL', '300'))

# Branding images get their OWN, much longer signature (Phase S9 Part 8,
# closing R27). 300 seconds is right for a document attachment and wrong for
# a logo: it is fetched on every page of a session, a dashboard left open
# over lunch re-requests it, and the answer after five minutes is a 403 where
# the brand should be. A logo is not confidential -- it is what the customer
# shows the world -- so the signature here only stops the media view serving
# arbitrary paths. One day outlives any realistic session and still stops a
# replaced logo being served beyond it.
BRANDING_URL_TTL = int(os.getenv('BRANDING_URL_TTL', '86400'))

USE_S3 = _env_bool('USE_S3', False)
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
if USE_S3:
    STORAGES['default'] = {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'bucket_name': os.getenv('AWS_STORAGE_BUCKET_NAME'),
            'region_name': os.getenv('AWS_S3_REGION_NAME', ''),
            # Set for S3-compatible providers (Cloudflare R2, MinIO, Spaces).
            'endpoint_url': os.getenv('AWS_S3_ENDPOINT_URL') or None,
            'access_key': os.getenv('AWS_ACCESS_KEY_ID'),
            'secret_key': os.getenv('AWS_SECRET_ACCESS_KEY'),
            'file_overwrite': False,
            'querystring_auth': _env_bool('AWS_S3_QUERYSTRING_AUTH', False),
            'default_acl': None,
        },
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'users.User'

AUTHENTICATION_BACKENDS = [
    'users.authentication.EmailBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# H6: do not allow all CORS origins in production unless explicitly opted in.
CORS_ALLOW_ALL_ORIGINS = _env_bool('CORS_ALLOW_ALL_ORIGINS', DEBUG)
CORS_ALLOWED_ORIGINS = _env_list('CORS_ALLOWED_ORIGINS', 'http://localhost:5173,http://localhost:8000')

# ---------------------------------------------------------------------------
# HTTPS / security hardening (Phase 3).
# Designed to run behind a TLS-terminating reverse proxy (nginx / Caddy): the
# proxy forwards X-Forwarded-Proto, so Django recognises forwarded HTTPS and
# does not redirect-loop. Cookie / HSTS / redirect enforcement is gated on production
# (not DEBUG) and each toggle is env-overridable, so local HTTP and the test
# suite keep working while production is locked down by default.
# ---------------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Admin / session CSRF over HTTPS behind a proxy needs the exact origin(s).
# Set your domain via DJANGO_CSRF_TRUSTED_ORIGINS, e.g. https://nif.example.com.
CSRF_TRUSTED_ORIGINS = _env_list('DJANGO_CSRF_TRUSTED_ORIGINS')

# Response security headers (always on - harmless over HTTP too).
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'

# HTTPS enforcement - defaults to ON in production, OFF in DEBUG/local HTTP.
SESSION_COOKIE_SECURE = _env_bool('DJANGO_SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = _env_bool('DJANGO_CSRF_COOKIE_SECURE', not DEBUG)
SECURE_SSL_REDIRECT = _env_bool('DJANGO_SECURE_SSL_REDIRECT', not DEBUG)
# The container healthcheck and the deploy probe call http://localhost:8000/api/v1/health/
# directly (no proxy, so no X-Forwarded-Proto). With SECURE_SSL_REDIRECT on, that 301s to
# https://localhost:8000 where the TLS handshake fails against plain-HTTP gunicorn, so the
# container never reports healthy and the deploy auto-rolls-back. Exempt ONLY the exact
# liveness path: SecurityMiddleware re.searches the stripped path, so this anchored regex
# matches 'api/v1/health/' but NOT 'api/v1/health/detailed/' (admin-only, stays enforced).
#
# /iclock/ is exempt for a different but equally hard reason: the ZKTeco
# terminal speaks plain HTTP and will not follow a 301 to https. Left enforced,
# every punch it posts is answered with a redirect it cannot act on, the device
# never receives an OK, and it retries until its buffer fills — attendance is
# lost silently, which is the exact failure this whole path is arranged to
# prevent. This is the same shape of bug as the health-check redirect above.
#
# The security cost is real and bounded: these endpoints carry attendance
# punches over the LAN in clear text. They carry no credentials, because the
# protocol has none to carry (see biometric/push_views). Keep /iclock/ off the
# public internet at the proxy, and set BIOMETRIC_PUSH_ALLOWED_IPS.
SECURE_REDIRECT_EXEMPT = [r'^api/v1/health/$', r'^iclock/']
SECURE_HSTS_SECONDS = int(os.getenv('DJANGO_SECURE_HSTS_SECONDS', '0' if DEBUG else '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', not DEBUG)
SECURE_HSTS_PRELOAD = _env_bool('DJANGO_SECURE_HSTS_PRELOAD', not DEBUG)

# ---------------------------------------------------------------------------
# Trusted proxies (Phase 11, audit finding H1/M1)
#
# How many proxies WE control sit in front of Django. Everything further left in
# X-Forwarded-For is caller-supplied and must not be trusted.
#
#   0  Django directly exposed (no proxy)
#   1  one reverse proxy (typical single-nginx deployment)
#   2  Cloudflare + ingress nginx (the documented production topology)
#
# Getting this WRONG IS NOT SYMMETRICAL. Too low means every client shares one
# throttle bucket -- too strict, visible, harmless. Too high means we read
# caller-supplied text as the client address, which is the rate-limit bypass
# this setting exists to close. The default therefore errs low.
#
# Set it from evidence, not from the diagram:
#     python manage.py verify_proxy_config --url https://your-domain/api/v1/health/
# ---------------------------------------------------------------------------
TRUSTED_PROXY_DEPTH = int(os.getenv('TRUSTED_PROXY_DEPTH', '1'))

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        # Phase S6: SimpleJWT's own class resolves the token's subject through
        # the tenant-scoped default manager, which cannot find a platform
        # operator (organization IS NULL) and made every authenticated request
        # to the platform console answer 500. The subclass resolves the user
        # unscoped and then refuses one that belongs to another tenant -- the
        # first place in an API request where the token's subject is known.
        # See tenancy/authentication.py.
        'tenancy.authentication.TenantJWTAuthentication',
    ],
    # THE FIX for H1. Unset, DRF used the entire X-Forwarded-For header as the
    # throttle identity -- caller-controlled, so rotating it gave a fresh bucket
    # per request and made every anonymous rate limit decorative. Bound to the
    # same setting the audit log uses, so throttling and forensics can never
    # disagree about who made a request.
    'NUM_PROXIES': TRUSTED_PROXY_DEPTH,
    # H6: fail closed - every endpoint requires auth unless it explicitly opts
    # out with permission_classes = [AllowAny] (health, login, refresh).
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'config.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    # Human words for DRF's stock refusals; specific messages pass through.
    'EXCEPTION_HANDLER': 'config.exceptions.human_exception_handler',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '20/min',
        'user': '200/min',
        # Phase 11 (H1/H2): the login endpoint gets its own, much tighter
        # ceiling. Folding it into `anon` meant either throttling every
        # anonymous endpoint to login speed or leaving login at 20/min.
        'login': '10/min',
        'memo_directory': '30/min',  # assignee-directory search (H5)
        # Public document verification (documents.views). Its own, TIGHT scope
        # because document numbers are SEQUENTIAL: NIFN-LV-2026-0001, -0002,
        # -0003. The endpoint is unauthenticated by necessity (a QR code is
        # scanned by someone with no account), so a rate limit is the only
        # thing standing between a stranger and walking a tenant's entire
        # issued-document history. Before Phase S3 it was a plain function
        # view and got no throttle at all.
        #
        # A genuine verifier scans one code. 10/min is generous for that and
        # useless for enumeration.
        'verify': '10/min',
        # Attachment fetches (documents.protected_media). Its own scope because
        # that view is deliberately unauthenticated -- a browser cannot put an
        # Authorization header on a native <img>/<a> -- so DRF saw every
        # download as anonymous and applied `anon`, 20/min KEYED BY IP. One
        # memo carrying ten quotations spent half of that, and a whole office
        # behind one NAT address shared the bucket: downloads failed at random
        # with no explanation, which is exactly what was reported. Measured
        # before this scope existed: 19 requests, then 429.
        #
        # 120/min is a reading rate, not a security control -- the HMAC
        # signature is the credential and it expires in 300s (M3/M7). This
        # ceiling only stops someone hammering storage through a link they
        # already legitimately hold.
        'media': '120/min',
        # A first backlog sync is ~22 back-to-back bulk requests, so this
        # ceiling has to clear that or onboarding a terminal throttles itself.
        'biometric_device': '240/min',
        # --- Phase S7: public self-service registration ---
        #
        # Registration CREATES A TENANT, which is the most expensive thing an
        # anonymous caller can cause on this platform: an organization, a
        # subscription, a bootstrap of 76 configuration rows and an
        # administrator account. So its ceiling is per hour, not per minute,
        # and it is the tightest scope here.
        #
        # A real person registering one organization makes one request. Three
        # an hour from one address covers a mistyped subdomain and a retry;
        # anything beyond it is not somebody signing up.
        'registration': '3/hour',
        # Verification is a token lookup. Tighter than a login because the
        # token is 32 bytes of entropy -- there is nothing to guess -- so the
        # only reason to call this repeatedly is to probe.
        'registration_verify': '10/hour',
        # The subdomain checker runs on every keystroke-debounce of a form
        # field, so it has to be generous enough to be usable and tight
        # enough not to be a customer directory. See the Security Findings:
        # this endpoint necessarily reveals that a subdomain is taken.
        'registration_slug': '30/min',
        # --- Phase S6.75 Part 7 (rate limit review) ---
        #
        # The password-change endpoint had no scope of its own, so it
        # inherited `user` -- 200 a minute. It verifies `current_password`
        # before accepting a new one, which makes it an oracle for the
        # CURRENT password at 200 guesses a minute to anybody holding a
        # stolen session or an unattended laptop.
        #
        # There is no self-service password RESET on this platform (an
        # administrator resets and tells the user), so this is the only
        # password-guessing surface behind a session.
        'password_change': '10/min',
        # Emailing a client their sign-in details (tenancy.handover). Each
        # call can reissue a temporary password and sends mail to a customer,
        # so a console bug or a stuck button must not become a mail storm.
        'access_send': '20/hour',
        # Forgot password: per IP, request and confirm together. A person
        # needs one or two; a script walking addresses needs thousands.
        'password_reset': '10/hour',
        # Support Center and feedback: generous for a person, useless for spam.
        'support': '30/hour',
        # --- Phase S8 ---
        #
        # Opening a plan request or submitting proof. Each one creates work
        # for a human at the platform -- a payment reference to reconcile, a
        # receipt to look at -- so the ceiling is about protecting the review
        # queue, not about load. A customer paying their bill does this once.
        'subscription_request': '20/hour',
        # Each call is a DNS query the platform makes on the customer's
        # behalf, and somebody waiting for propagation will press the button
        # repeatedly. Generous enough to be usable, tight enough that the
        # platform is not a DNS load generator.
        'domain_verify': '30/hour',
    },
}

# ---------------------------------------------------------------------------
# Login brute-force protection (Phase 11, audit finding H2)
#
# Counters live in the cache, which is the resilient Redis backend: on a Redis
# outage lockout FAILS OPEN. That is deliberate -- an availability incident must
# not become an authentication incident and lock a whole organisation out of its
# own system. The per-IP throttle and the audit trail still apply.
#
# Set LOGIN_LOCKOUT_ENABLED=False to keep throttling and auditing but never lock
# an account (alerting-only posture).
# ---------------------------------------------------------------------------
LOGIN_MAX_FAILURES = int(os.getenv('LOGIN_MAX_FAILURES', '10'))
LOGIN_FAILURE_WINDOW = int(os.getenv('LOGIN_FAILURE_WINDOW', '900'))
LOGIN_LOCKOUT_SECONDS = int(os.getenv('LOGIN_LOCKOUT_SECONDS', '900'))
LOGIN_LOCKOUT_ENABLED = _env_bool('LOGIN_LOCKOUT_ENABLED', True)

SIMPLE_JWT = {
    # M3: short-lived access tokens; refresh tokens rotate and the old one is
    # blacklisted on use, so a leaked token has a small window and can be revoked.
    #
    # 10 MINUTES, NOT 30 (Phase OMS-FINAL-RELEASE-BLOCKERS).
    #
    # An access token is a STATELESS bearer credential: nothing on the server can
    # withdraw one before it expires. Logout blacklists the REFRESH token, which
    # was measured doing its job (post-logout refresh -> 401) — but the access
    # token in the same session kept returning 200 for the rest of its life. On a
    # shared workstation that made "Sign out" a promise the system could not keep
    # for half an hour, on a system holding confidential memos.
    #
    # The window is now 10 minutes. Refresh rotation already renews silently, so
    # nobody is signed out sooner; only the blast radius of a copied token
    # shrinks. Overridable so an operator can trade it against token traffic.
    'ACCESS_TOKEN_LIFETIME': timedelta(
        minutes=int(os.getenv('DJANGO_ACCESS_TOKEN_MINUTES', '10'))),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}


# ---------------------------------------------------------------------------
# Structured logging (Phase 5)
# Daily-rotating JSON file at logs/nifn.log (30 days), per-module loggers,
# correlation ids injected by config.middleware.RequestIDMiddleware.
# ---------------------------------------------------------------------------
# Email (scheduled reports). Console backend by default in dev; configure SMTP
# via env in production.
# Default to the console backend only in DEBUG/tests; real deployments default to
# SMTP so nobody silently ships with emails going nowhere.
_DEFAULT_EMAIL_BACKEND = (
    'django.core.mail.backends.console.EmailBackend' if _ALLOW_INSECURE
    else 'django.core.mail.backends.smtp.EmailBackend'
)
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', _DEFAULT_EMAIL_BACKEND)
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'no-reply@nifportal.local')

# Fail-closed on boot: if a prod run uses the SMTP backend, the host + credentials
# must be present (a misconfigured mailer should fail at startup, not silently at
# send time). The request path itself stays fail-safe (sends are wrapped + logged).
if not _ALLOW_INSECURE and EMAIL_BACKEND.endswith('smtp.EmailBackend'):
    _missing_smtp = [
        name for name, val in (
            ('EMAIL_HOST', EMAIL_HOST),
            ('EMAIL_HOST_USER', EMAIL_HOST_USER),
            ('EMAIL_HOST_PASSWORD', EMAIL_HOST_PASSWORD),
        ) if not val
    ]
    if _missing_smtp:
        raise ImproperlyConfigured(
            f"SMTP email backend requires {', '.join(_missing_smtp)} when DEBUG is off. "
            "Set them, or set EMAIL_BACKEND to the console backend for a mail-less deployment."
        )

# Send notification emails synchronously (tests / small deployments). In prod the
# default (False) runs each send in a background thread so the API never blocks.
NOTIFICATIONS_RUN_SYNC = os.getenv('NOTIFICATIONS_RUN_SYNC', 'False').lower() in ('true', '1', 'yes')
# Leave-workflow email toggles (global; per-user opt-out lives in NotificationPreference).
NOTIFY_CC_HR_ON_SUBMIT = os.getenv('NOTIFY_CC_HR_ON_SUBMIT', 'True').lower() in ('true', '1', 'yes')
NOTIFY_EMPLOYEE_ON_L1 = os.getenv('NOTIFY_EMPLOYEE_ON_L1', 'True').lower() in ('true', '1', 'yes')
# Record copy of the final decision (grant / reject) to HR + Admin for oversight.
# Default ON: the Department Head's approval is final, so HR/Admin are kept in the
# loop for the record without being an approval stage.
NOTIFY_AUDIT_COPY_ON_FINAL = os.getenv('NOTIFY_AUDIT_COPY_ON_FINAL', 'True').lower() in ('true', '1', 'yes')

# Generate reports synchronously (set True in tests / small deployments).
REPORTS_RUN_SYNC = os.getenv('REPORTS_RUN_SYNC', 'False').lower() in ('true', '1', 'yes')

# Notifications: base URL for building action/unsubscribe links in emails, and a
# flag to send notification emails synchronously (tests / small deployments).
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173')
# Public base URL of this backend (used to build QR verification links on PDFs).
SITE_URL = os.getenv('SITE_URL', 'http://localhost:8001')
# Organization details printed on PDF letterheads/certificates (CMS-overridable).
# Official organisation contact details — single source for every PDF letterhead
# and footer (documents/templates/pdf/_org_contact.html). Override per-env if these
# ever change; no template edit needed.
# THESE ARE THE PLATFORM OPERATOR'S DETAILS, NOT A CUSTOMER'S.
#
# Brand migration: these defaulted to Nepal Internet Foundation's name,
# address, telephone number and website, from when the platform and its only
# tenant were the same organisation. They are now Buddhi Labs', which is who
# operates the platform.
#
# A TENANT NEVER SEES THESE unless it has filled in nothing of its own.
# `documents.pdf.org_letterhead` reads the tenant's own name, address, phone
# and website from its Organization row and falls back here only when a field
# is blank — so a customer's memo carries the customer's letterhead, and this
# is what the platform's own documents carry.
#
# Set ORG_* in the environment to whatever operates a given deployment.
ORG_INFO = {
    'name': os.getenv('ORG_NAME', 'Buddhi Labs'),
    'address': os.getenv('ORG_ADDRESS', 'Kathmandu, Nepal'),
    'tel': os.getenv('ORG_TEL', ''),
    'email': os.getenv('ORG_EMAIL', 'hello@buddhilabs.com'),
    'website': os.getenv('ORG_WEBSITE', 'www.buddhilabs.com'),
}

# The attribution carried on every TENANT-facing surface — emails, PDFs, the
# login page. Not a copyright notice: a tenant's documents belong to the
# tenant, and this says who runs the software underneath them.
PLATFORM_NAME = os.getenv('PLATFORM_NAME', 'Buddhi Labs')
PLATFORM_POWERED_BY = os.getenv('PLATFORM_POWERED_BY',
                                f'Powered by {PLATFORM_NAME}')
# Where a new client is told to write if they cannot sign in. Printed in the
# welcome email and the handover package. Blank means the line is left out
# rather than pointing at an inbox nobody reads.
PLATFORM_SUPPORT_EMAIL = os.getenv('PLATFORM_SUPPORT_EMAIL', '').strip()
# Support Desk 3.0. Who is emailed when a ticket is escalated (comma list;
# team leads are added, and PLATFORM_SUPPORT_EMAIL is the fallback), and the
# escalation rules: priority -> hours of SLA clock before automatic escalation.
SUPPORT_LEADERSHIP_EMAILS = [e.strip() for e in os.getenv('SUPPORT_LEADERSHIP_EMAILS', '').split(',')
                             if e.strip()]
SUPPORT_ESCALATION_HOURS = {'critical': float(os.getenv('SUPPORT_ESCALATE_CRITICAL_HOURS', '4') or 4)}
NOTIFICATIONS_RUN_SYNC = os.getenv('NOTIFICATIONS_RUN_SYNC', 'False').lower() in ('true', '1', 'yes')
# Autosave snapshots (Phase 111). Retention runs off last-touched, so a document
# someone returns to each week survives regardless of when it was started.
DRAFT_RETENTION_DAYS = int(os.getenv('DRAFT_RETENTION_DAYS', '90'))
# A memo with a large pasted table sits well under this; above it is a bug or an
# attack, and either way must not become an unbounded row.
DRAFT_MAX_PAYLOAD_BYTES = int(os.getenv('DRAFT_MAX_PAYLOAD_BYTES', str(512 * 1024)))

# Reports older than this are purged by purge_expired_reports.
REPORTS_RETENTION_DAYS = int(os.getenv('REPORTS_RETENTION_DAYS', '30'))

# ---------------------------------------------------------------------------
# Logging (Phase 6): stream everything to stdout so the platform (Docker)
# aggregates it. No local log files -> safe under multi-worker Gunicorn
# (no rotation races) and nothing is lost on redeploy. Structured JSON in
# production for log aggregation, human-readable in DEBUG. Correlation ids are
# injected by config.middleware.RequestIDMiddleware.
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv('DJANGO_LOG_LEVEL', 'INFO').upper()
_LOG_FORMATTER = 'simple' if DEBUG else 'json'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
        'request_id': {'()': 'config.logging_utils.RequestIDFilter'},
    },
    'formatters': {
        'json': {'()': 'config.logging_utils.JSONFormatter'},
        'simple': {'format': '%(levelname)s %(name)s [%(request_id)s] %(message)s'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': _LOG_FORMATTER,
            'filters': ['request_id'],
        },
    },
    'loggers': {
        # Phase 11 (audit finding M9): this listed only memos/leaves/audit, so
        # everything else fell through to the root logger at WARNING and every
        # logger.info() in the rest of the codebase was DISCARDED — including
        # "Cache unavailable, degrading to no-cache" and "WebSocket connected",
        # which are precisely the lines you want when diagnosing an incident.
        module: {'handlers': ['console'], 'level': LOG_LEVEL, 'propagate': False}
        for module in ('memos', 'minutes', 'leaves', 'audit', 'users', 'attendance',
                       'biometric', 'reports', 'analytics', 'monitoring',
                       'notifications', 'inventory', 'documents', 'config',
                       # PHASE S10 PART 6. Exactly the M9 bug above, repeated
                       # for the packages added since it was fixed -- and
                       # `tenancy` is the worst possible one to lose, because
                       # it logs from THIRTY files and they are the
                       # security-relevant ones: provisioning steps, export
                       # progress and integrity, retention actions, resolver
                       # decisions, domain verification outcomes.
                       #
                       # WARNING and ERROR still reached the root logger, so
                       # the gap was invisible in normal operation and
                       # total during an incident: "domain X failed
                       # verification: no TXT record at ..." is logged at
                       # INFO and was being discarded, which is the one line
                       # support needs when a customer says their address
                       # does not work.
                       'tenancy', 'appraisal', 'circulars', 'tasks',
                       'drafts', 'evidence')
    },
    'root': {'handlers': ['console'], 'level': 'WARNING'},
}

# ---------------------------------------------------------------------------
# Error tracking (Phase 11, audit finding M9). Optional: with SENTRY_DSN unset
# this block does nothing and adds no dependency at runtime.
#
# Scrubbing is not left to defaults. This system handles attendance records and
# HR documents, so PII in a stack trace is a data-protection incident of its
# own; send_default_pii stays off and the sensitive keys are stripped from every
# event before it leaves the process.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Alerting (Phase 11, §6). Email and/or a webhook — no new service, reusing the
# mail configuration that already exists. With neither set, alerts are still
# evaluated and RECORDED in the audit log; only delivery is skipped, and a
# warning is logged so a silent alerter is visible rather than assumed working.
# ---------------------------------------------------------------------------
ALERT_EMAILS = os.getenv('ALERT_EMAILS', '')
ALERT_WEBHOOK_URL = os.getenv('ALERT_WEBHOOK_URL', '').strip()

SENTRY_DSN = os.getenv('SENTRY_DSN', '').strip()
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv('SENTRY_TRACES_SAMPLE_RATE', '0.0'))
SENTRY_ENVIRONMENT = os.getenv('SENTRY_ENVIRONMENT', 'production' if not DEBUG else 'development')

_SCRUB_KEYS = {
    'password', 'passwd', 'secret', 'token', 'access', 'refresh',
    'authorization', 'api_key', 'x-api-key', 'x-signature',
    'database_password', 'django_secret_key', 'backup_encryption_key',
    'email_host_password', 'aws_secret_access_key',
}


def _scrub(event, _hint):
    """Strip credentials from an event before it leaves the process."""
    def walk(node):
        if isinstance(node, dict):
            return {
                key: ('[scrubbed]' if str(key).lower() in _SCRUB_KEYS else walk(value))
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(event)


if SENTRY_DSN and not _RUNNING_TESTS:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            integrations=[
                DjangoIntegration(),
                LoggingIntegration(level=None, event_level='ERROR'),
            ],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
            before_send=_scrub,
        )
    except ImportError:
        # Configured but not installed is a deployment mistake worth seeing, not
        # a reason to refuse to boot: error tracking is an aid, not a dependency.
        import logging as _logging

        _logging.getLogger(__name__).warning(
            'SENTRY_DSN is set but sentry-sdk is not installed; '
            'error tracking is disabled.'
        )



