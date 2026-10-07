"""Phase S8: what a customer's administrator may submit about money.

TWO SERIALIZERS, AND NEITHER OF THEM ACCEPTS AN AMOUNT. That is the single
most important thing about this file: the price is resolved server-side from
the effective ``PlanPrice`` and written onto the payment row by
``payments.create_payment``, so there is no field here through which a client
could propose what it owes.
"""
from rest_framework import serializers


class PlanRequestSerializer(serializers.Serializer):
    """A plan code, and nothing else.

    No amount, no currency, no period. The server looks the plan up through
    `purchasable_plans()` -- so a retired or private plan cannot be requested
    by anybody who knows its code, including from an old quotation.
    """
    plan_code = serializers.SlugField(max_length=40)


class PaymentProofSerializer(serializers.Serializer):
    """The receipt. Part 4's five fields.

    `proof` is read from `request.FILES` rather than declared here: it is
    multipart, it is optional (a bank transfer reference can be enough for a
    verifier), and declaring it as a serializer field would make the error
    message for a missing file read as a validation failure rather than as
    the deliberate choice it is.
    """
    method = serializers.ChoiceField(choices=[])
    transaction_id = serializers.CharField(max_length=120, required=False,
                                           allow_blank=True, default="")
    paid_at = serializers.DateField(required=False, allow_null=True,
                                    default=None)
    payer_note = serializers.CharField(max_length=2000, required=False,
                                       allow_blank=True, default="")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Built here rather than at module level: naming the enum at import
        # time would import models while this module is still loading.
        from .models import Payment

        self.fields["method"].choices = Payment.Method.choices

    def validate_paid_at(self, value):
        """A payment cannot have been made in the future.

        Caught here rather than by the verifier, because the customer can fix
        a typed date in a second and an operator cannot fix it at all -- they
        can only reject the payment and ask.
        """
        from django.utils import timezone

        if value and value > timezone.localdate():
            raise serializers.ValidationError(
                "That date is in the future. Enter the date you actually "
                "paid.")
        return value


class BrandingSerializer(serializers.Serializer):
    """Phase S9 Part 1. Text and colours only; images have their own endpoint.

    THE COLOURS ARE NOT VALIDATED HERE but in `portal._validate_colour`,
    because the same rule has to hold for the console's branding editor as
    well -- and a hex check that lives in one of two serializers is a hex
    check that is missing from the other. What this class does is bound the
    LENGTHS, so a 10MB tagline cannot be posted.
    """
    display_name = serializers.CharField(max_length=200, required=False,
                                         allow_blank=True)
    color_primary = serializers.CharField(max_length=7, required=False,
                                          allow_blank=True)
    color_secondary = serializers.CharField(max_length=7, required=False,
                                            allow_blank=True)
    color_accent = serializers.CharField(max_length=7, required=False,
                                         allow_blank=True)
    login_tagline = serializers.CharField(max_length=200, required=False,
                                          allow_blank=True)
    dashboard_welcome = serializers.CharField(max_length=300, required=False,
                                              allow_blank=True)
    report_footer_text = serializers.CharField(max_length=300,
                                               required=False,
                                               allow_blank=True)


class DomainClaimSerializer(serializers.Serializer):
    """Phase S9 Part 3. A hostname, and how the customer prefers to prove it.

    The hostname is normalised and validated in `tenancy.domains.validate`,
    which is also what the console calls -- one set of rules about what a
    hostname may be, in the module that owns the question.
    """
    hostname = serializers.CharField(max_length=253)
    method = serializers.ChoiceField(choices=[], required=False, default=None,
                                     allow_null=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .models import TenantDomain

        self.fields["method"].choices = TenantDomain.Method.choices
