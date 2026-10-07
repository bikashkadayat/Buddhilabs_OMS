import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../../components/common/PageHeader';
import DataTable from '../../components/common/DataTable';
import EmptyState from '../../components/common/EmptyState';
import StatusBadge from '../../components/common/StatusBadge';
import { platformService } from '../../services/platformService';

/**
 * Part 1: Subscriptions — every customer's commercial state on one screen.
 *
 * SORTED BY WHAT RUNS OUT SOONEST, not by name. The Organizations page answers
 * "tell me about this customer"; this page answers the only question that has
 * a deadline attached — who is about to lose access, and in what order. A
 * subscription with no expiry (cancelled, or never sold one) sorts last
 * because nothing is pending for it.
 *
 * NO ACTIONS HERE, deliberately. Every lifecycle move needs its own input — a
 * suspension needs a reason, an extension needs a number of months — and they
 * live on the organization's own page where that form and its audit trail are.
 * A row here links there rather than growing a menu of verbs, which is also
 * what stops an operator suspending the wrong customer from a dense table.
 *
 * TWO STATUS COLUMNS, AND THEY ARE NOT REDUNDANT. "Subscription" is what the
 * customer has paid for; "Access" is whether they can actually get in. They
 * differ exactly when an operator has intervened — a suspension for abuse
 * leaves a perfectly valid subscription — and seeing both is how an operator
 * notices that a tenant is locked out for a reason nobody has recorded.
 */
const DAYS = (row) => {
  if (!row.subscription_expiry) return null;
  const expiry = new Date(`${row.subscription_expiry}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return Math.round((expiry - today) / 86400000);
};

const PlatformSubscriptions = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    platformService.organizations()
      .then((response) => {
        if (!alive) return;
        const sorted = [...response.data].sort((a, b) => {
          const left = a.subscription_expiry || '9999-12-31';
          const right = b.subscription_expiry || '9999-12-31';
          return left.localeCompare(right);
        });
        setRows(sorted);
        setError(null);
      })
      .catch(() => { if (alive) setError('The subscription list could not be loaded.'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [reload]);

  const remaining = (row) => {
    const days = DAYS(row);
    if (days === null) return '—';
    if (days < 0) return `${Math.abs(days)} d ago`;
    if (days === 0) return 'today';
    return `${days} d`;
  };

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Platform administration"
        title="Subscriptions"
        description="What every customer is on, and what runs out first. Soonest to expire at the top."
      />
      {error ? (
        <EmptyState variant="error" title="Could not load subscriptions" body={error}
                    onAction={() => { setLoading(true); setReload((n) => n + 1); }}
                    actionLabel="Retry" />
      ) : (
        <DataTable
          columns={[
            { key: 'name', header: 'Customer',
              render: (row) => (
                <Link to={`/platform/organizations/${row.slug}`} className="pf-link">
                  {row.name}
                </Link>
              ) },
            { key: 'plan_code', header: 'Plan',
              render: (row) => row.plan_code || 'none' },
            { key: 'subscription_status', header: 'Subscription',
              render: (row) => <StatusBadge status={row.subscription_status} /> },
            { key: 'status', header: 'Access',
              render: (row) => <StatusBadge status={row.status} /> },
            { key: 'is_admitted', header: 'Can sign in',
              render: (row) => (row.is_admitted ? 'Yes' : 'No') },
            { key: 'subscription_expiry', header: 'Expires',
              render: (row) => row.subscription_expiry || '—' },
            { key: 'days', header: 'Remaining', align: 'right',
              render: remaining },
          ]}
          rows={rows}
          loading={loading}
          rowKey={(row) => row.slug}
          caption="Customer subscriptions, soonest to expire first"
          empty={{ variant: 'first', title: 'No customers yet',
                   body: 'Provision an organization and it will appear here.' }}
        />
      )}
    </div>
  );
};

export default PlatformSubscriptions;
