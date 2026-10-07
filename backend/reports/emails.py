from django.conf import settings
from django.core.mail import EmailMessage


def send_report_email(scheduled, content, filename, content_type):
    """Email a generated report as an attachment to the scheduled recipients."""
    recipients = scheduled.recipients or []
    if not recipients:
        return 0
    # The TENANT's name in the subject, not the platform's. These land in
    # the inbox of somebody who has never heard of the platform operator.
    from notifications.emails import powered_by, subject_prefix

    subject = f"{subject_prefix()} {scheduled.get_report_type_display()}"
    body = (
        f"Attached is your scheduled report: "
        f"{scheduled.get_report_type_display()}.\n"
        f"Generated automatically. {powered_by()}."
    )
    message = EmailMessage(subject, body, settings.DEFAULT_FROM_EMAIL, recipients)
    message.attach(filename, content, content_type)
    return message.send(fail_silently=False)
