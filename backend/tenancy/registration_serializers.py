"""Phase S7 Part 1: what a registration form may say, and what it may not.

SEPARATE MODULE FROM ``tenancy/serializers.py`` on purpose. Everything in
there is read or written by an authenticated platform operator; everything
here arrives from an anonymous stranger. Keeping the two apart means the
question "what can the public submit to this platform?" has one file as its
answer.
"""
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from . import registration


class RegistrationSerializer(serializers.Serializer):
    """The nine fields Part 1 lists, plus one nobody can see.

    PASSWORD RULES COME FROM ``AUTH_PASSWORD_VALIDATORS``, not from a regex
    written here. The project already decided what a weak password is --
    minimum length, not a common one, not all digits, not too similar to the
    user's own details -- and it enforces that on every password change inside
    every tenant. A second, different standard at the front door would mean
    the one password nobody checked properly is the first administrator's.
    """

    organization_name = serializers.CharField(max_length=200)
    slug = serializers.CharField(max_length=63)
    industry = serializers.CharField(max_length=100, required=False,
                                     allow_blank=True, default="")
    country = serializers.CharField(max_length=2, required=False,
                                    allow_blank=True, default="")
    # OPTIONAL, and defaulted to the administrator's address.
    #
    # This brief's field list does not include it, and an earlier version's
    # did -- so rather than drop the column (it is the billing and
    # service-notice address on `Organization`, and an operator needs
    # somewhere to write it), the form stops asking. One fewer field on a
    # signup form is worth more than a distinction most registrants would
    # answer with the same address twice.
    organization_email = serializers.EmailField(required=False,
                                                allow_blank=True, default="")

    admin_name = serializers.CharField(max_length=150, required=False,
                                       allow_blank=True, default="")
    admin_email = serializers.EmailField()
    password = serializers.CharField(write_only=True, max_length=128)
    password_confirmation = serializers.CharField(write_only=True,
                                                  max_length=128)

    # The honeypot (Part 9's spam protection). Hidden in the form, so a human
    # never fills it and a script that posts every field it finds does.
    # Declared here rather than silently ignored so the view can read it.
    website = serializers.CharField(required=False, allow_blank=True,
                                    default="", max_length=200)

    def validate_slug(self, value):
        """FORMAT AND RESERVED WORDS ONLY. Availability is a cross-field
        question, and this is the single-field hook.

        WHY THAT SPLIT MATTERS -- it was a bug. Checking availability here
        rejected the resubmission case outright: somebody who registers,
        loses the email and fills the form in again holds their OWN subdomain
        at that moment, so they were told "that workspace address is already
        in use" about themselves, with no way to get another email. The
        collapse-and-resend path in `registration.register` existed and was
        unreachable, because this ran first.

        Deciding it in `validate()`, where `admin_email` is also known, is
        what makes "already in use -- by you" distinguishable from "already
        in use -- by somebody else".
        """
        answer = registration.slug_status(value)
        if answer["reason"] in ("reserved", "invalid", "empty", "too_short"):
            raise serializers.ValidationError(answer["detail"])
        return answer["slug"]

    def validate_organization_name(self, value):
        name = (value or "").strip()
        if len(name) < 2:
            raise serializers.ValidationError(
                "Enter the organization's name.")
        return name

    def validate_country(self, value):
        """Two letters, uppercase, or nothing.

        Not validated against a list of countries: the platform does not need
        to know, the field is informational, and a list of valid codes is a
        thing that goes out of date and starts rejecting real customers.
        """
        code = (value or "").strip().upper()
        if code and (len(code) != 2 or not code.isalpha()):
            raise serializers.ValidationError(
                "Use a two-letter country code, for example NP.")
        return code

    def validate(self, attrs):
        # The administrator's address, when none was given for the
        # organization. Not a placeholder: for a company registering itself
        # it is very often the same address, and an empty billing address is
        # worse than a duplicated one.
        if not (attrs.get("organization_email") or "").strip():
            attrs["organization_email"] = attrs["admin_email"]

        if attrs["password"] != attrs.get("password_confirmation"):
            raise serializers.ValidationError(
                {"password_confirmation": "The two passwords do not match."})

        # Availability, now that the administrator's address is known too.
        # A subdomain held by THIS registrant's own pending registration is
        # not a clash -- it is them filling the form in again, which must
        # re-send the email rather than refuse.
        answer = registration.slug_status(attrs["slug"])
        if not answer["available"] and not registration.held_by(
                attrs["slug"], attrs["admin_email"]):
            raise serializers.ValidationError({"slug": answer["detail"]})

        # Validated against the registrant's own details: the similarity
        # check is the reason to bother building a stand-in user at all.
        probe = _Probe(email=attrs["admin_email"],
                       name=attrs.get("admin_name", ""),
                       slug=attrs["slug"])
        try:
            password_validation.validate_password(attrs["password"], probe)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})

        # AND ONE RULE DJANGO'S VALIDATORS CANNOT APPLY HERE.
        # `UserAttributeSimilarityValidator` only ever compares against
        # username, first name, last name and email -- that list is fixed in
        # Django -- so "newschool-newschool" sails past it as the password for
        # the `newschool` workspace. The subdomain and the organization name
        # are the two things an attacker guessing at this account already
        # knows, which makes them the worst possible basis for its password.
        lowered = attrs["password"].lower()
        for label, value in (("workspace address", attrs["slug"]),
                             ("organization name",
                              attrs.get("organization_name", ""))):
            cleaned = (value or "").strip().lower()
            if len(cleaned) >= 4 and cleaned in lowered:
                raise serializers.ValidationError({"password": [
                    f"The password cannot contain your {label}."]})
        return attrs


class _Probe:
    """A stand-in for the user who does not exist yet.

    ``validate_password`` compares a password against the user's attributes,
    and here there is no user -- that is the whole point of Part 3. This
    carries the three things the registrant did give us so the similarity
    check has something real to work with.
    """

    def __init__(self, *, email, name, slug):
        self.email = email
        self.username = (email or "").split("@")[0]
        first, _, last = (name or "").partition(" ")
        self.first_name = first
        self.last_name = last
        self.slug = slug


class VerificationSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=128, trim_whitespace=True)

    def validate_token(self, value):
        token = (value or "").strip()
        if not token:
            raise serializers.ValidationError("The verification link is "
                                              "incomplete.")
        return token
