from config.client_ip import client_ip

from .models import AuditLog


def log_action(actor, action, instance=None, changes=None, request=None,
               organization=None):
    """
    Write an immutable AuditLog entry. Call this from perform_create/
    perform_update/perform_destroy or custom workflow actions in other apps -
    never mutate an AuditLog row afterward.

    Phase 11 (audit finding M1): the recorded address now comes from
    ``config.client_ip``, which walks X-Forwarded-For as far as our own proxies
    and no further. This previously read REMOTE_ADDR, which behind nginx is the
    PROXY's address -- so every row in the audit log carried the same internal
    IP and the field was worse than useless: it looked like evidence.
    """
    # Phase S5. Which tenant's history this event belongs to.
    #
    # THE SUBJECT BEFORE THE ACTOR, deliberately: "platform staff suspended
    # tenant X" is an event in tenant X's history, and an impersonating
    # platform operator must not file it under their own (nonexistent)
    # organization. The full order is in tenancy.ownership.resolve_for_audit.
    #
    # None is a legitimate answer -- a failed login for an address belonging to
    # nobody, or a genuine platform action -- which is why the column is
    # nullable. Inventing a tenant for a probe would hide it.
    from tenancy.ownership import resolve_for_audit

    organization_id = resolve_for_audit(actor=actor, instance=instance,
                                        organization=organization)

    ip_address = None
    user_agent = ""
    if request is not None:
        ip_address = client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:512]

    # BUILT THEN SAVED, rather than `objects.create`, so the resolution above
    # can be marked as DECIDED (Phase S6).
    #
    # `resolve_for_audit` returning None is an answer, not a gap -- a failed
    # login for an address belonging to nobody, or a platform operator's own
    # action. The tenancy pre_save receiver used to treat that None as "not
    # filled in yet" and stamp the ambient context over it, which while
    # TENANCY_ENABLED is False is the single-tenant default: platform logins
    # were landing in NIF's audit trail. The flag tells the receiver the
    # question has already been answered.
    entry = AuditLog(
        organization_id=organization_id,
        actor=actor if actor is not None and getattr(actor, "is_authenticated", False) else None,
        action=action,
        content_object=instance,
        object_repr=str(instance) if instance is not None else "",
        changes=changes or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    entry._organization_decided = True

    if organization_id is None:
        # A ROW THAT BELONGS TO NO TENANT IS WRITTEN IN PLATFORM SCOPE
        # (Phase S6), because under row-level security that is the only scope
        # in which it CAN be written: the policy admits an unowned row only on
        # a connection that has declared it.
        #
        # Both cases that produce None need this. A failed login for an
        # address belonging to nobody is one. The other is a platform
        # operator signing in on a SINGLE-HOST deployment -- where no console
        # hostname is configured, so the middleware cannot tell the request
        # apart from a tenant's and binds the tenant instead. Without this the
        # operator's own LOGIN event is refused and the request 500s: the
        # platform console would be unreachable on exactly the deployment
        # shape NIF runs today.
        from tenancy.context import no_tenant

        with no_tenant():
            entry.save()
        return entry

    entry.save()
    return entry
