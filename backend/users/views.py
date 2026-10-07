from io import BytesIO

from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework.response import Response
from django.core.files.base import ContentFile
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from .serializers import UserSerializer, ChangePasswordSerializer, SelfProfileSerializer
from .models import User

# Registration removed in Phase 2.5. Users are created by Admin via
# User Management (POST /api/v1/users/admin/users/).


class CurrentUserView(generics.RetrieveAPIView):
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class ChangePasswordView(APIView):
    """First-login (and general) password change. Clears must_change_password.

    ITS OWN THROTTLE SCOPE (Phase S6.75 Part 7). With none it inherited
    `user` -- 200 requests a minute -- and this endpoint verifies
    `current_password` before accepting a new one, which turns it into an
    oracle for the current password at 200 guesses a minute to whoever holds
    a stolen session or an unattended laptop. A person changing their
    password does it once.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_change"

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data["current_password"]):
            raise ValidationError({"current_password": "Your current password isn’t right. Check it and try again."})

        # Was this the forced first-login change? Read BEFORE it is cleared.
        completing_handover = bool(user.must_change_password)

        user.set_password(serializer.validated_data["new_password"])
        user.must_change_password = False
        user.last_password_change = timezone.now()
        user.save(update_fields=["password", "must_change_password", "last_password_change"])

        log_action(user, AuditLog.Action.UPDATE, instance=user,
                   changes={"event": "PASSWORD_CHANGED"}, request=request)
        if completing_handover:
            # A new client administrator just got in with the details they
            # were sent. Tell the platform, which otherwise could only guess
            # from silence. Never raises.
            from tenancy.handover import record_first_sign_in

            record_first_sign_in(user, request=request)
        return Response({"detail": "Password changed successfully."})


def _manager_of(user):
    """Who this person reports to, for their own profile page. Never raises.

    The same answer attendance corrections are routed by -- the department's
    head, else a department head in the same department -- so "my manager" on
    the profile and "who approves my correction" cannot disagree. On this one
    endpoint only: putting it on UserSerializer would add a query per row to
    every user list in the product.
    """
    try:
        from attendance.workforce.routing import department_head_for

        head = department_head_for(user)
    except Exception:  # noqa: BLE001 - a profile must load without it
        return None
    if head is None:
        return None
    return {"name": head.get_full_name() or head.username,
            "designation": head.designation or "", "email": head.email}


class ProfileMeView(APIView):
    """
    GET   /api/v1/profile/me/  - the caller's own full profile.
    PATCH /api/v1/profile/me/  - edit own editable fields ONLY (self-scoped).

    Always operates on request.user; there is no user_id override, and the write
    goes through SelfProfileSerializer, whose field list is the allowlist — so a
    payload carrying role/department/email/etc. cannot change protected fields.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = UserSerializer(request.user, context={'request': request}).data
        data['manager'] = _manager_of(request.user)
        return Response(data)

    def patch(self, request):
        serializer = SelfProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        log_action(request.user, AuditLog.Action.UPDATE, instance=request.user,
                   changes={'event': 'PROFILE_UPDATED', 'fields': sorted(serializer.validated_data.keys())},
                   request=request)
        return Response(UserSerializer(request.user, context={'request': request}).data)


class ProfilePhotoView(APIView):
    """
    POST   /api/v1/profile/me/photo/  (multipart 'photo') - upload/replace.
    DELETE /api/v1/profile/me/photo/                      - remove.

    Validates type (jpeg/png/webp) + size (<=2 MB), then centre-crops to a square
    and resizes to 256x256 (stored as JPEG). Self-only.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    MAX_BYTES = 2 * 1024 * 1024
    ALLOWED_TYPES = {'image/jpeg', 'image/png', 'image/webp'}

    def post(self, request):
        upload = request.FILES.get('photo')
        if upload is None:
            return Response({'detail': 'Please choose a photo to upload.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if (upload.content_type or '') not in self.ALLOWED_TYPES:
            return Response({'detail': 'That type of image isn’t supported. Please use a JPG, PNG or WEBP file.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > self.MAX_BYTES:
            return Response({'detail': 'That image is too large. Please choose one under 2 MB.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            from PIL import Image, ImageOps
            img = Image.open(upload)
            img = ImageOps.exif_transpose(img).convert('RGB')
            w, h = img.size
            side = min(w, h)
            left, top = (w - side) // 2, (h - side) // 2
            img = img.crop((left, top, left + side, top + side)).resize((256, 256), Image.LANCZOS)
        except Exception:
            return Response({'detail': 'We couldn’t read that image. Try a different JPG, PNG or WEBP file.'},
                            status=status.HTTP_400_BAD_REQUEST)

        buffer = BytesIO()
        img.save(buffer, format='JPEG', quality=85)
        user = request.user
        if user.profile_photo:
            user.profile_photo.delete(save=False)  # avoid orphaned old file
        # Unique filename per upload so a new photo gets a NEW URL — otherwise a
        # fixed name (user_<id>.jpg) reuses the same URL and the browser serves
        # the cached OLD image even though the file was replaced.
        import uuid
        filename = f'user_{user.id}_{uuid.uuid4().hex[:12]}.jpg'
        user.profile_photo.save(filename, ContentFile(buffer.getvalue()), save=True)
        log_action(user, AuditLog.Action.UPDATE, instance=user,
                   changes={'event': 'PROFILE_PHOTO_UPDATED'}, request=request)
        return Response(UserSerializer(user, context={'request': request}).data)

    def delete(self, request):
        user = request.user
        if user.profile_photo:
            user.profile_photo.delete(save=True)
            log_action(user, AuditLog.Action.UPDATE, instance=user,
                       changes={'event': 'PROFILE_PHOTO_REMOVED'}, request=request)
        return Response(UserSerializer(user, context={'request': request}).data)


class UserListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        users = User.objects.filter(is_active=True)
        user_list = []
        for user in users:
            user_list.append({
                'id': str(user.id),
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'role': user.role,
            })
        return Response(user_list)



class TourStateView(APIView):
    """POST {tour}: mark a guided tour finished for this person, on every device.

    Only a known tour name is stored, and only into this person's own row:
    the field is presentation state, not a place for arbitrary data.
    """
    permission_classes = [IsAuthenticated]
    TOURS = {"employee", "manager", "admin", "platform"}

    def post(self, request):
        tour = (request.data.get("tour") or "").strip()
        if tour not in self.TOURS:
            raise ValidationError({"tour": "Unknown tour."})
        user = request.user
        state = dict(user.ui_state or {})
        done = set(state.get("tours_done") or [])
        if request.data.get("reset"):
            done.discard(tour)
        else:
            done.add(tour)
        state["tours_done"] = sorted(done)
        user.ui_state = state
        user.save(update_fields=["ui_state"])
        return Response({"tours_done": state["tours_done"]})
