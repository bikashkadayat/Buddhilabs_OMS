import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import PageHeader from '../../components/common/PageHeader';
import DataTable from '../../components/common/DataTable';
import EmptyState from '../../components/common/EmptyState';
import StatusBadge from '../../components/common/StatusBadge';
import NewOrganization from '../../components/platform/NewOrganization';
import {
  platformService, bytes, ORG_STATUSES,
} from '../../services/platformService';

/**
 * Part 2: the organization list, and the one-click create.
 *
 * TWO STATUS COLUMNS, and they are not redundant. "Workspace" is
 * Organization.status — whether anybody can get in. "Billing" is the
 * subscription mirror — whether they have paid. They agree almost always, and
 * the cases where they differ are exactly the ones an operator is looking for:
 * a fully paid-up tenant that is suspended for abuse, or a tenant in grace
 * who is still working normally and does not know yet.
 */
const PlatformOrganizations = () => {
  const [params, setParams] = useSearchParams();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [creating, setCreating] = useState(params.get('new') === '1');

  const status = params.get('status') || '';
  const subscriptionStatus = params.get('subscription_status') || '';
  const search = params.get('search') || '';

  // THE FETCH LIVES IN THE EFFECT AND SETS STATE ONLY IN ITS CALLBACKS.
  //
  // The obvious shape -- a `load` callback that flips `loading` on and an
  // effect that calls it -- sets state synchronously in the effect body, which
  // cascades a render before the request has even been made. `reload` is a
  // counter an event handler bumps, so a retry is a user action rather than
  // an effect re-entering itself.
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    platformService.organizations({
      status: status || undefined,
      subscription_status: subscriptionStatus || undefined,
      search: search || undefined,
    })
      .then((response) => {
        if (!alive) return;
        setRows(response.data);
        setError(null);
      })
      .catch(() => {
        if (alive) setError('The organization list could not be loaded.');
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [status, subscriptionStatus, search, reload]);

  const load = useCallback(() => {
    setLoading(true);
    setReload((n) => n + 1);
  }, []);

  const setFilter = (key, value) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    next.delete('new');
    setParams(next);
  };

  const columns = useMemo(() => [
    {
      key: 'name',
      header: 'Organization',
      render: (row) => (
        <Link to={`/platform/organizations/${row.slug}`} className="pf-link">
          <b>{row.name}</b>
          <span className="pf-sub">{row.slug}</span>
        </Link>
      ),
    },
    {
      key: 'status',
      header: 'Workspace',
      render: (row) => (
        <StatusBadge status={row.status} />
      ),
    },
    {
      key: 'subscription_status',
      header: 'Billing',
      render: (row) => (
        <StatusBadge status={row.subscription_status} />
      ),
    },
    { key: 'plan_code', header: 'Plan', render: (row) => row.plan_code || '—' },
    {
      key: 'subscription_expiry',
      header: 'Expires',
      render: (row) => row.subscription_expiry || '—',
    },
    { key: 'seat_count', header: 'Users', align: 'right' },
    {
      key: 'storage_bytes',
      header: 'Storage',
      align: 'right',
      render: (row) => bytes(row.storage_bytes),
    },
  ], []);

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Platform administration"
        title="Organizations"
        description="Every customer on the platform. A tenant exists because an operator created it."
        actions={(
          <button type="button" className="btn btn-primary"
                  onClick={() => setCreating(true)}>
            New organization
          </button>
        )}
      />

      <div className="pf-filters">
        <label className="pf-filter">
          <span>Workspace</span>
          <select value={status} onChange={(e) => setFilter('status', e.target.value)}>
            <option value="">All</option>
            {ORG_STATUSES.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </label>
        <label className="pf-filter">
          <span>Search</span>
          <input
            type="search"
            defaultValue={search}
            placeholder="Name, address or email"
            onKeyDown={(e) => {
              if (e.key === 'Enter') setFilter('search', e.target.value.trim());
            }}
          />
        </label>
      </div>

      {error ? (
        <EmptyState variant="error" title="Could not load organizations"
                    body={error} onAction={load} actionLabel="Retry" />
      ) : (
        <DataTable
          columns={columns}
          rows={rows}
          loading={loading}
          caption="Organizations"
          /* `empty` is a PROPS OBJECT that DataTable spreads into EmptyState,
             not a rendered node. Passing an element renders an empty shell. */
          empty={(status || search)
            ? {
              variant: 'filtered',
              title: 'No organization matches',
              body: 'Clear the filter to see every customer.',
              onAction: () => setParams(new URLSearchParams()),
              actionLabel: 'Clear filters',
            }
            : {
              variant: 'first',
              title: 'No organizations yet',
              body: 'Create your first customer. You will get their sign-in details to send them as soon as it is done.',
              onAction: () => setCreating(true),
              actionLabel: 'New organization',
            }}
        />
      )}

      {creating && (
        <NewOrganization
          onClose={() => setCreating(false)}
          onCreated={() => { setCreating(false); load(); }}
        />
      )}
    </div>
  );
};

export default PlatformOrganizations;
