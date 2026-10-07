import secrets
import uuid

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .services import generate_employee_id

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Current-user / general profile serializer (used by /auth/user/)."""
    full_name = serializers.SerializerMethodField()
    department_name = serializers.ReadOnlyField()
    role_display = serializers.CharField(source='get_role_display', read_only=True)
    employee_type_display = serializers.CharField(source='get_employee_type_display', read_only=True)
    # Category engine (read-only): resolved on read so it auto-promotes with service.
    employment_type_display = serializers.CharField(source='get_employment_type_display', read_only=True)
    leave_category = serializers.SerializerMethodField()
    leave_category_display = serializers.SerializerMethodField()
    organization_slug = serializers.CharField(source='organization.slug',
                                              read_only=True, default=None)
    category_flag = serializers.SerializerMethodField()
    service_label = serializers.SerializerMethodField()
    # Signed, expiring URL (documents.protected_media) — never a raw /media/ path.
    # Header-free so it works in an <img>; only issued to authenticated requests
    # (this serializer runs behind IsAuthenticated views). Null when there's no photo.
    profile_photo = serializers.SerializerMethodField()

    def get_profile_photo(self, obj):
        if not obj.profile_photo:
            return None
        from documents.protected_media import signed_media_url
        # Phase 11 (M7): bound to the requesting user, so a leaked link is
        # useless to anyone else. `request` is None only when a serializer is
        # used outside a request (email delivery), where an unbound link is
        # the deliberate fallback.
        viewer = getattr(self.context.get("request"), "user", None)
        return signed_media_url(obj.profile_photo.name, user=viewer)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'employee_id', 'first_name', 'last_name', 'email',
            'role', 'role_display', 'employee_type', 'employee_type_display',
            'employment_type', 'employment_type_display', 'gender',
            'maternity_eligible', 'paternity_eligible',
            'leave_category', 'leave_category_display', 'category_flag', 'service_label',
            'full_name', 'department', 'department_ref', 'department_name',
            'designation', 'phone', 'date_of_joining', 'is_active', 'biometric_id',
            'must_change_password', 'last_login', 'date_joined', 'profile_photo',
            # Self-service personal fields (read here; edited via SelfProfileSerializer).
            'address', 'emergency_contact_name', 'emergency_contact_number',
            'date_of_birth', 'bio',
            # Phase S6. The frontend needs to know which application to show:
            # a platform operator belongs to no organization and has no tenant
            # role, so routing them by `role` would land them in a workspace
            # that is not theirs. Both read-only -- the pair is a database
            # constraint (user_platform_staff_has_no_org), not a preference.
            'is_platform_staff', 'organization_slug',
            # Which guided tours this person has finished (customer success).
            'ui_state',
        ]
        read_only_fields = ['id', 'employee_id', 'must_change_password', 'last_login',
                            'date_joined', 'is_platform_staff', 'organization_slug',
                            'ui_state']

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username

    def _category(self, obj):
        # Resolve+cache on read so login reflects any service-threshold crossing.
        #
        # SKIPPED FOR A PLATFORM ACCOUNT (Phase S6). The leave category is
        # tenant configuration -- resolved from EntitlementRule rows belonging
        # to one organization -- and a platform operator belongs to none.
        # Resolving it would be meaningless at best, and under row-level
        # security it reads tables the operator's connection returns nothing
        # from, which would make /auth/user/ fail for the one account that has
        # to be able to sign in and fix things.
        if getattr(obj, "is_platform_staff", False):
            return obj
        from leaves import category_engine
        category_engine.resolve_and_cache(obj)
        return obj

    def get_leave_category(self, obj):
        return self._category(obj).leave_category

    def get_leave_category_display(self, obj):
        obj = self._category(obj)
        return obj.get_leave_category_display() if obj.leave_category else None

    def get_category_flag(self, obj):
        return self._category(obj).category_flag

    def get_service_label(self, obj):
        if getattr(obj, "is_platform_staff", False):
            return None
        from leaves import category_engine
        return category_engine.service_label(obj.date_of_joining)


class SelfProfileSerializer(serializers.ModelSerializer):
    """
    Employee self-service edit serializer. The field list IS the server-side
    allowlist: only these are ever writable via PATCH /profile/me/. Protected
    fields (email, username, role, department, employment_type, category,
    is_active, date_of_joining, entitlements) are absent, so any attempt to send
    them is silently ignored — they cannot be changed by the employee.
    """
    class Meta:
        model = User
        fields = [
            'first_name', 'last_name', 'phone', 'address',
            'emergency_contact_name', 'emergency_contact_number',
            'date_of_birth', 'gender', 'bio',
        ]

    def validate_first_name(self, value):
        if not (value or '').strip():
            raise serializers.ValidationError('First name is required.')
        return value.strip()

    def validate_phone(self, value):
        value = (value or '').strip()
        if value and not all(c.isdigit() or c in '+-() ' for c in value):
            raise serializers.ValidationError('Phone may contain only digits and + - ( ) spaces.')
        return value

    def validate_emergency_contact_number(self, value):
        value = (value or '').strip()
        if value and not all(c.isdigit() or c in '+-() ' for c in value):
            raise serializers.ValidationError('Emergency number may contain only digits and + - ( ) spaces.')
        return value

    def validate_date_of_birth(self, value):
        from django.utils import timezone
        if value and value > timezone.localdate():
            raise serializers.ValidationError('Date of birth cannot be in the future.')
        return value


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        if attrs['current_password'] == attrs['new_password']:
            raise serializers.ValidationError({'new_password': 'Must differ from the current password.'})
        return attrs


class AdminUserCreateSerializer(serializers.ModelSerializer):
    # Password is optional; if omitted an initial one is generated and returned.
    password = serializers.CharField(write_only=True, min_length=8, required=False)
    generated_password = serializers.SerializerMethodField()

    # Read-only computed category preview so the form can show the resolved tier.
    leave_category = serializers.CharField(source='get_leave_category_display', read_only=True)
    category_flag = serializers.CharField(read_only=True)
    service_label = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'employee_id', 'email', 'first_name', 'last_name', 'username',
            'password', 'generated_password', 'role', 'employee_type', 'department', 'department_ref',
            'designation', 'phone', 'date_of_joining', 'is_active', 'biometric_id',
            'employment_type', 'gender', 'maternity_eligible', 'paternity_eligible',
            'leave_category', 'category_flag', 'service_label',
        ]
        read_only_fields = ['id', 'employee_id', 'username', 'generated_password',
                            'leave_category', 'category_flag', 'service_label']

    def get_generated_password(self, obj):
        # Surfaced once (on create) so the admin can share initial credentials.
        return getattr(obj, '_generated_password', None)

    def get_service_label(self, obj):
        from leaves import category_engine
        return category_engine.service_label(obj.date_of_joining)

    def _apply_eligibility_defaults(self, user, validated_data):
        """Default maternity/paternity eligibility from gender unless HR set them."""
        from leaves import category_engine
        mat, pat = category_engine.default_eligibility(user.gender)
        if 'maternity_eligible' not in validated_data:
            user.maternity_eligible = mat
        if 'paternity_eligible' not in validated_data:
            user.paternity_eligible = pat

    def create(self, validated_data):
        request = self.context.get('request')
        password = validated_data.pop('password', None) or secrets.token_urlsafe(9)
        email = validated_data['email']
        username = validated_data.get('username') or (email.split('@')[0] + '_' + str(uuid.uuid4())[:6])
        validated_data['username'] = username

        user = User(**validated_data)
        user.employee_id = generate_employee_id()
        user.must_change_password = True  # forced on first login
        if request is not None and getattr(request.user, 'is_authenticated', False):
            user.created_by = request.user
        self._apply_eligibility_defaults(user, validated_data)
        user.set_password(password)
        user.save()
        # Resolve the leave category and auto-assign this year's category-driven
        # entitlements (annual/sick/maternity/paternity per the seeded matrix).
        from django.utils import timezone
        from leaves import category_engine
        category_engine.ensure_category_balances(user, timezone.localdate().year)
        user._generated_password = password
        return user

    def update(self, instance, validated_data):
        gender_changed = 'gender' in validated_data
        user = super().update(instance, validated_data)
        # If gender changed and HR didn't set eligibility explicitly, re-default it.
        if gender_changed:
            self._apply_eligibility_defaults(user, validated_data)
            user.save(update_fields=['maternity_eligible', 'paternity_eligible'])
        # Employment type / joining date changes re-resolve category + rebuild balances.
        from django.utils import timezone
        from leaves import category_engine
        category_engine.ensure_category_balances(user, timezone.localdate().year)
        return user
