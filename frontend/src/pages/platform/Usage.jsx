import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../../components/common/PageHeader';
import DataTable from '../../components/common/DataTable';
import EmptyState from '../../components/common/EmptyState';
import { platformService, bytes } from '../../services/platformService';

/**
 * Part 1: Tenant Usage — what each customer is consuming, against what their
 * plan includes.
 *
 * EVERY FIGURE IS A MAINTAINED COUNTER, not a count. Under row-level security
 * a `COUNT(*)` over a tenant table issued from the console returns zero for
 * every tenant, silently and uniformly, because the console holds no tenant
 * context — so a usage page built the obvious way would show a platform full
 * of empty customers. `tenancy/counters.py` keeps `seat_count` and
 * `storage_bytes` on the organization row itself, which is a platform table.
 * That is also why this page needs no privilege over customer records.
 *
 * OVER-SEAT IS SHOWN, NOT ENFORCED. The platform does not meter in this
 * phase, and locking a customer out of their own HR system over a seat count
 * they were never warned about is not a platform anybody keeps. The column is
 * here so somebody can have the conversation.
 *
 * A plan with no seat limit reads "unlimited" rather than blank: an empty cell
 * cannot be told apart from a figure that failed to load.
 */
const PlatformUsage = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reload, setReload] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let alive = true;
    platformService.organizations()
      .then((response) => {
        if (!alive) return;
        const sorted = [...response.data].sort(
          (a, b) => (b.seat_count || 0) - (a.seat_count || 0));
        setRows(sorted);
        setError(null);
      })
      .catch(() => { if (alive) setError('Tenant usage could not be loaded.'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [reload]);

  // The counters are maintained by signals as users and files come and go.
  // This recomputes them from the rows themselves, which is the remedy if a
  // bulk import ever bypassed the signal.
  const refresh = () => {
    setRefreshing(true);
    platformService.refreshCounters()
      .then(() => { setLoading(true); setReload((n) => n + 1); })
      .catch(() => setError('The counters could not be recomputed.'))
      .finally(() => setRefreshing(false));
  };

  const seats = (row) => {
    if (row.seats_included == null) return `${row.seat_count} of unlimited`;
    return `${row.seat_count} of ${row.seats_included}`;
  };

  const overSeats = (row) => (
    row.seats_included != null && row.seat_count > row.seats_included);

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Platform administration"
        title="Tenant usage"
        description="Seats and storage per customer, against what their plan includes. Busiest first."
        actions={(
          <button type="button" className="btn btn-secondary" onClick={refresh}
                  disabled={refreshing}>
            {refreshing ? 'Recomputing…' : 'Recompute counters'}
          </button>
        )}
      />
      {error ? (
        <EmptyState variant="error" title="Could not load usage" body={error}
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
            { key: 'seat_count', header: 'Seats', align: 'right', render: seats },
            { key: 'over', header: 'Over plan',
              render: (row) => (overSeats(row)
                ? <span className="pf-warn-text">over by {row.seat_count - row.seats_included}</span>
                : 'No') },
            { key: 'storage_bytes', header: 'Storage', align: 'right',
              render: (row) => bytes(row.storage_bytes) },
            { key: 'status', header: 'Access',
              render: (row) => row.status_display || row.status },
          ]}
          rows={rows}
          loading={loading}
          rowKey={(row) => row.slug}
          caption="Tenant usage by seats and storage"
          empty={{ variant: 'first', title: 'No customers yet',
                   body: 'Provision an organization and its usage will appear here.' }}
        />
      )}
    </div>
  );
};

export default PlatformUsage;
