import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import PageHeader from '../../components/common/PageHeader';
import DataTable from '../../components/common/DataTable';
import EmptyState from '../../components/common/EmptyState';
import { platformService } from '../../services/platformService';

/**
 * Part 9: the platform audit trail.
 *
 * READ-ONLY BY CONSTRUCTION, not by omission. The model refuses an update and
 * refuses a delete, so there is no write endpoint to leave off this page —
 * there is nothing a write endpoint could do. An audit trail somebody can edit
 * is a narrative, not evidence.
 *
 * It is also a SEPARATE trail from each tenant's own audit log, because the
 * platform's record of what it did to a customer has to outlive the customer
 * and must not be readable or hideable by them.
 */
const PlatformAuditTrail = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [action, setAction] = useState('');

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
    platformService.audit({ action: action || undefined, limit: 500 })
      .then((response) => {
        if (!alive) return;
        setRows(response.data);
        setError(null);
      })
      .catch(() => { if (alive) setError('The audit trail could not be loaded.'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [action, reload]);

  const load = useCallback(() => {
    setLoading(true);
    setReload((n) => n + 1);
  }, []);

  const actions = [...new Set(rows.map((row) => row.action))].sort();

  return (
    <div className="page">
      <PageHeader
        breadcrumb="Platform administration"
        title="Audit trail"
        description="Every action a platform operator has taken on a customer: who, when, and exactly what changed."
      />

      <div className="pf-filters">
        <label className="pf-filter">
          <span>Action</span>
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">All actions</option>
            {actions.map((value) => (
              <option key={value} value={value}>{value.replace(/_/g, ' ')}</option>
            ))}
          </select>
        </label>
      </div>

      {error ? (
        <EmptyState variant="error" title="Could not load the audit trail"
                    body={error} onAction={load} actionLabel="Retry" />
      ) : (
        <DataTable
          columns={[
            { key: 'created_at', header: 'When',
              render: (row) => new Date(row.created_at).toLocaleString() },
            { key: 'action_display', header: 'Action' },
            { key: 'organization_slug', header: 'Customer',
              render: (row) => (row.organization_slug
                ? (
                  <Link to={`/platform/organizations/${row.organization_slug}`}
                        className="pf-link">
                    {row.organization_name || row.organization_slug}
                  </Link>
                )
                // The customer may have been removed; the entry still names them.
                : <span className="pf-sub">—</span>) },
            { key: 'actor_email', header: 'Operator',
              render: (row) => row.actor_email || 'system' },
            { key: 'changes', header: 'Changed',
              render: (row) => (row.changes
                ? <code className="pf-code">{JSON.stringify(row.changes)}</code>
                : '—') },
            { key: 'note', header: 'Note' },
            { key: 'ip_address', header: 'From',
              render: (row) => row.ip_address || '—' },
          ]}
          rows={rows}
          loading={loading}
          caption="Platform audit entries"
          empty={{ variant: 'first', title: 'Nothing recorded yet',
                   body: 'Provisioning, suspensions, plan changes and branding '
                       + 'edits all appear here.' }}
        />
      )}
    </div>
  );
};

export default PlatformAuditTrail;
