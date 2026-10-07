from django.conf import settings
from django.contrib import admin
from django.urls import path, include, re_path
from rest_framework.routers import DefaultRouter

from documents.protected_media import ProtectedMediaView

from leaves.views import LeaveViewSet, LeaveBalanceView, LeaveCalendarView
from users.views import CurrentUserView, UserListView, ChangePasswordView, ProfileMeView, ProfilePhotoView, TourStateView
from users.token_serializers import EmailLoginView, LogoutView, SafeTokenRefreshView
from users.admin_views import AdminUserViewSet, AdminLeaveViewSet, AdminBalanceViewSet, AdminStatsView
from config.health_views import HealthView, DetailedHealthView

# Automated routing for ViewSets
router = DefaultRouter()
router.register(r'leaves', LeaveViewSet, basename='leave')

# Admin ViewSets
admin_router = DefaultRouter()
admin_router.register(r'admin/users', AdminUserViewSet, basename='admin-user')
admin_router.register(r'admin/leaves', AdminLeaveViewSet, basename='admin-leave')
admin_router.register(r'admin/balances', AdminBalanceViewSet, basename='admin-balance')
# Phase 2.5: spec path for admin User Management.
admin_router.register(r'users/admin/users', AdminUserViewSet, basename='usermgmt-user')

from users.session_views import EndOtherSessionsView, SessionListView
from users.password_reset import PasswordResetConfirmView, PasswordResetRequestView

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Auth APIs (JWT Authentication)
    path('api/v1/auth/login/', EmailLoginView.as_view(), name='token_obtain_pair'),
    path('api/v1/auth/refresh/', SafeTokenRefreshView.as_view(), name='token_refresh'),
    path('api/v1/auth/logout/', LogoutView.as_view(), name='token_logout'),
    # Registration removed in Phase 2.5 (admin-created accounts only).
    path('api/v1/auth/user/', CurrentUserView.as_view(), name='token_user'),
    path('api/v1/auth/change-password/', ChangePasswordView.as_view(), name='change-password'),
    path('api/v1/auth/sessions/', SessionListView.as_view(), name='auth-sessions'),
    path('api/v1/auth/password-reset/', PasswordResetRequestView.as_view(), name='password-reset'),
    path('api/v1/auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    path('api/v1/auth/sessions/end-others/', EndOtherSessionsView.as_view(), name='auth-sessions-end-others'),

    # Self-service profile (read + edit own editable fields + photo)
    path('api/v1/profile/me/', ProfileMeView.as_view(), name='profile-me'),
    path('api/v1/profile/me/photo/', ProfilePhotoView.as_view(), name='profile-me-photo'),
    path('api/v1/profile/me/tour/', TourStateView.as_view(), name='profile-me-tour'),

    # User list API
    path('api/v1/users/', UserListView.as_view(), name='user-list'),
    
    # Phase 4 Enterprise Leave Records (must precede the /leaves/<pk>/ router
    # below so the custom /leaves/my-history/ etc. paths resolve first)
    path('api/v1/', include('leaves.urls')),

    # Workflow & Leaves APIs
    path('api/v1/', include(router.urls)),
    
    # Admin APIs
    path('api/v1/', include(admin_router.urls)),
    path('api/v1/admin/stats/', AdminStatsView.as_view(), name='admin-stats'),
    
    # Custom Leave Read API routes
    path('api/v1/leaves/balance', LeaveBalanceView.as_view(), name='leave-balance'),
    path('api/v1/leaves/calendar', LeaveCalendarView.as_view(), name='leave-calendar'),

    # Memos APIs (router mounts /api/v1/memos/ and /api/v1/memo-templates/)
    path('api/v1/', include('memos.urls')),
    # Minute APIs (router mounts /api/v1/minutes/)
    path('api/v1/', include('minutes.urls')),
    # Circular APIs (router mounts /api/v1/circulars/)
    path('api/v1/', include('circulars.urls')),

    # Document autosave drafts (Phase 111), shared by memo/minute/circular
    path('api/v1/', include('drafts.urls')),

    # Audit Log APIs (admin-only, read-only)
    path('api/v1/audit/', include('audit.urls')),

    # Reports & Analytics (Phase 8, admin-only)
    path('api/v1/', include('reports.urls')),

    # Notifications (Phase 9)
    path('api/v1/', include('notifications.urls')),

    # Public document verification (Phase 10)
    path('api/v1/', include('documents.urls')),

    # Attendance (check-in/out, calendar, HR management)
    path('api/v1/', include('attendance.urls')),

    # Inventory (items, assignments, take-out workflow)
    path('api/v1/', include('inventory.urls')),

    # Biometric attendance (devices, employee mapping, raw punches)
    path('api/v1/', include('biometric.urls')),

    # ZKTeco iClock / PUSH endpoints (Phase 12). Mounted at the web root, with
    # no version prefix, because the terminal's firmware hardcodes these paths
    # and cannot be told to use others.
    path('', include('biometric.push_urls')),

    # Executive analytics (Phase 10, read-only; gated by ANALYTICS_ENABLED)
    path('api/v1/', include('analytics.urls')),

    # Monitoring & operations (Phase 11, HR/Admin only)
    path('api/v1/', include('monitoring.urls')),

    # Task Management (Phase T1). Router mounts /api/v1/tasks/.
    path('api/v1/', include('tasks.urls')),

    # Performance Appraisal (Phase APM-02). Consumes the task evidence layer.
    path('api/v1/', include('appraisal.urls')),

    # Platform Admin Console (Phase S6). Platform-staff only, and once
    # TENANCY_ENABLED is on, served only on a platform host -- the console is
    # deliberately not reachable through any tenant workspace.
    path('api/v1/', include('tenancy.urls')),

    # Health checks (Phase 5)
    path('api/v1/health/', HealthView.as_view(), name='health'),
    path('api/v1/health/detailed/', DetailedHealthView.as_view(), name='health-detailed'),
]

# Uploaded media (memo attachments/vouchers, profile photos, report files) is
# served ONLY through signed, expiring URLs (documents.protected_media). The old
# unauthenticated `serve` catch-all was removed — every file now requires a valid
# server-issued signature, so there is no public /media/ access in dev or prod.
if not settings.USE_S3:
    urlpatterns += [
        path('api/v1/media/', ProtectedMediaView.as_view(), name='protected-media'),
    ]
