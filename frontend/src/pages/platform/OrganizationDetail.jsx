import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import PageHeader from '../../components/common/PageHeader';
import StatusBadge from '../../components/common/StatusBadge';
import Skeleton from '../../components/common/Skeleton';
import EmptyState from '../../components/common/EmptyState';
import DataTable from '../../components/common/DataTable';
import LifecycleActions from '../../components/platform/LifecycleActions';
import AttendancePanel from '../../components/platform/AttendancePanel';
import BrandingPanel from '../../components/platform/BrandingPanel';
import EditOrganization from '../../components/platform/EditOrganization';
import PortabilityPanel from '../../components/platform/PortabilityPanel';
import HandoverCard from '../../components/platform/HandoverCard';
import Timeline from '../../components/platform/success/Timeline';
import SuccessTasks from '../../components/platform/success/Tasks';
import {
  platformService, money, bytes, statusLabel,
} from '../../services/platformService';

/**
 * Parts 2, 5, 6, 7 and 9 for one tenant, on one page.
 *
 * ORDERED BY WHAT AN OPERATOR OPENED IT FOR. Health first, because the only
 * reason to open a specific customer's page is usually that something is wrong
 * with them; then the lifecycle actions; then subscription, usage, branding;
 * then the two trails. A page that opened with address details would make the
 * operator scroll past them every time.
 */
const PlatformOrganizationDetail = () => {
  const { slug } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [editing, setEditing] = useState(false);
  // The customer's way in, fetched on its own so a slow administrator
  // lookup never holds up the rest of the page.
  const [access, setAccess] = useState(null);

  const load = useCallback(() => {
    platformService.organization(slug)
      .then((response) => { setData(response.data); setError(null); })
      .catch((err) => setError(err.response?.status === 404
        ? `No workspace with the address “${slug}”.`
        : 'This organization could not be loaded.'));
  }, [slug]);

  useEffect(load, [load]);

  const loadAccess = useCallback(() => {
    platformService.access(slug)
      .then((response) => setAccess(response.data))
      .catch(() => setAccess(null));
  }, [slug]);

  useEffect(loadAccess, [loadAccess]);

  const repair = async () => {
    setNotice(null);
    try {
      const response = await platformService.repair(slug);
      const created = Object.values(response.data.created || {})
        .reduce((total, n) => total + n, 0);
      setNotice(created
        ? `Repaired: ${created} configuration row(s) created.`
        : 'Nothing was missing — no changes made.');
      load();
    } catch {
      setNotice('The repair could not be run.');
    }
  };

  if (error) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Organizations" title={slug} />
        <EmptyState variant="error" title="Not available" body={error} />
        <Link to="/platform/organizations" className="pf-link">
          ← All organizations
        </Link>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="page">
        <PageHeader breadcrumb="Organizations" title={slug} />
        <Skeleton rows={5} height={48} label="Loading the organization" />
      </div>
    );
  }

  const health = data.health || {};
  const usage = data.usage || {};
  const subscription = data.subscription;
  const gaps = Object.entries(health.configuration_gaps || {});

  return (
    <div className="page">
      <PageHeader
        breadcrumb={<Link to="/platform/organizations">Organizations</Link>}
        title={data.name}
        description={`${data.slug} · ${data.email} · documents numbered ${data.document_prefix}-…`}
        actions={(
          <button type="button" className="btn btn-ghost"
                  onClick={() => setEditing(true)}>
            Edit details
          </button>
        )}
      >
        <div className="pf-badges">
          <StatusBadge status={data.status} label={`Workspace: ${statusLabel(data.status)}`} />
          <StatusBadge status={data.subscription_status} label={`Billing: ${statusLabel(data.subscription_status)}`} />
          {data.is_admitted
            ? <StatusBadge status="approved" label="Users can sign in" />
            : <StatusBadge status="rejected" label="Locked out" />}
        </div>
      </PageHeader>

      {notice && <p className="pf-note" role="status">{notice}</p>}

      {/* First on the page, because "how do they get in?" is the question
          an operator is most often answering when they open a customer. */}
      <HandoverCard access={access} slug={slug} onSent={loadAccess} />

      {/* Tenant health. The check that would have caught a provisioned
          tenant with no leave types. */}
      <section className="pf-panel" aria-label="Tenant health">
        <h2 className="pf-panel-title">Workspace health</h2>
        {/* The verdict in the server's own words, so the console and the
            health endpoint cannot drift into saying different things. The
            reason follows it, because a verdict with no detail is not
            actionable. */}
        {(health.ready ?? health.usable) ? (
          <p className="pf-ok">
            {health.verdict || 'Tenant Ready'}. Configuration is complete and
            users can sign in.
          </p>
        ) : (
          <div className="pf-alert" role="alert">
            <strong>{health.verdict || 'Provisioning Incomplete'}.</strong>
            {!health.is_admitted && (
              <p>Nobody can sign in: the workspace is {statusLabel(data.status)}.</p>
            )}
            {gaps.length > 0 && (
              <>
                <p>Configuration is missing:</p>
                <ul className="pf-alert-list">
                  {gaps.map(([area, missing]) => (
                    <li key={area}>
                      <b>{area.replace(/_/g, ' ')}</b>: {missing.join(', ')}
                    </li>
                  ))}
                </ul>
                <button type="button" className="btn btn-ghost" onClick={repair}>
                  Repair configuration
                </button>
              </>
            )}
          </div>
        )}
        {data.mirror_drift && (
          <p className="pf-err" role="alert">
            Subscription mirror drift: {data.mirror_drift}
          </p>
        )}
      </section>

      <LifecycleActions
        slug={slug}
        organization={data}
        subscription={subscription}
        onChanged={() => { setNotice(null); load(); }}
      />

      <div className="pf-cols">
        <section className="pf-panel" aria-label="Subscription">
          <h2 className="pf-panel-title">Subscription</h2>
          {subscription ? (
            <ul className="pf-kv">
              <li><span>Plan</span><b>{subscription.plan_name}</b></li>
              <li><span>Status</span><b>{subscription.status_display}</b></li>
              <li>
                <span>Price</span>
                <b>{money(subscription.amount_minor, subscription.currency || 'NPR')}</b>
              </li>
              <li><span>Term</span><b>{subscription.interval_months} month(s)</b></li>
              <li>
                <span>Current period</span>
                <b>{subscription.current_period_start} → {subscription.current_period_end}</b>
              </li>
              <li><span>Grace until</span><b>{subscription.grace_until || '—'}</b></li>
              <li>
                <span>Days to expiry</span>
                <b>{subscription.days_until_expiry ?? '—'}</b>
              </li>
            </ul>
          ) : (
            <p className="pf-err">
              This organization has no subscription, which should be impossible —
              provisioning creates one. Report it.
            </p>
          )}
        </section>

        <section className="pf-panel" aria-label="Usage">
          <h2 className="pf-panel-title">Usage</h2>
          <ul className="pf-kv">
            <li><span>Users</span><b>{usage.seats_used}</b></li>
            <li>
              <span>Seats included</span>
              <b>{usage.seats_included ?? 'Unlimited'}</b>
            </li>
            <li><span>Storage</span><b>{bytes(usage.storage_bytes)}</b></li>
            <li><span>Billing mode</span><b>{usage.billing_mode || '—'}</b></li>
          </ul>
          {usage.over_seats && (
            <p className="pf-note">
              Over the included seat count. Reported, not enforced — nobody is
              locked out for this.
            </p>
          )}
        </section>
      </div>

      <PortabilityPanel
        slug={slug}
        organization={data}
        health={health}
        onChanged={() => { setNotice(null); load(); }}
      />

      <BrandingPanel slug={slug} />
      <AttendancePanel slug={slug} />

      {editing && (
        <EditOrganization
          organization={data}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            setNotice('Organization details saved.');
            load();
          }}
        />
      )}

      {/* Customer Success 2.0: the whole story, and what we owe them next. */}
      <section className="pf-panel cs-org" id="success" aria-label="Customer success">
        <h2 className="pf-panel-title">Customer timeline</h2>
        <Timeline slug={slug} />
        <h2 className="pf-panel-title">Customer success tasks</h2>
        <SuccessTasks organization={slug} />
      </section>

      <section className="pf-panel" aria-label="Subscription history">
        <h2 className="pf-panel-title">Subscription history</h2>
        <DataTable
          columns={[
            { key: 'created_at', header: 'When',
              render: (row) => new Date(row.created_at).toLocaleString() },
            { key: 'event_display', header: 'Event' },
            { key: 'from_status', header: 'From', render: (r) => r.from_status || '—' },
            { key: 'to_status', header: 'To' },
            { key: 'actor_email', header: 'By', render: (r) => r.actor_email || 'system' },
            { key: 'note', header: 'Note' },
          ]}
          rows={data.events || []}
          caption="Subscription events"
          empty={{ variant: 'first', title: 'No events yet',
                   body: 'Lifecycle changes appear here.' }}
        />
      </section>

      <section className="pf-panel" aria-label="Platform audit">
        <h2 className="pf-panel-title">Platform actions on this customer</h2>
        <DataTable
          columns={[
            { key: 'created_at', header: 'When',
              render: (row) => new Date(row.created_at).toLocaleString() },
            { key: 'action_display', header: 'Action' },
            { key: 'actor_email', header: 'Operator',
              render: (r) => r.actor_email || 'system' },
            { key: 'note', header: 'Note' },
          ]}
          rows={data.audit || []}
          caption="Platform audit entries"
          empty={{ variant: 'first', title: 'No platform actions yet',
                   body: 'Everything an operator does here is recorded.' }}
        />
      </section>
    </div>
  );
};

export default PlatformOrganizationDetail;
