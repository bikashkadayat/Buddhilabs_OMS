import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CheckCircle2, ShieldAlert, UserX } from 'lucide-react';

import { useAuth } from '../../hooks/useAuth';
import { assetLifecycle, inventoryService } from '../../services/inventoryService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

// The Board may check anyone's clearance, read-only (Phase BOD).
const MANAGER_ROLES = ['checker', 'approver', 'bod', 'admin'];

const RESOLUTION = {
  unresolved: { label: 'Needs a transfer or return', tone: 'no' },
  transfer_in_progress: { label: 'Transfer in progress', tone: 'warn' },
  return_in_progress: { label: 'Return in progress', tone: 'warn' },
};

/**
 * Exit clearance (Phase ASSET-CUSTODY-TRANSFER).
 *
 * CLEAR MEANS FINISHED, NOT RAISED. A transfer awaiting HR can still be
 * rejected and a return under inspection can still be refused, so the server
 * keeps an employee BLOCKED until the last custody row has actually closed. This
 * page shows how each asset is being resolved so HR can see what is left to
 * chase, but it never turns an in-progress item green.
 */
const ExitClearance = () => {
  const { role } = useAuth();
  const isManager = MANAGER_ROLES.includes(role);
  const [employeeId, setEmployeeId] = useState('');

  const { data: employees = [] } = useQuery({
    queryKey: ['inventory', 'employees'],
    queryFn: inventoryService.employees,
    enabled: isManager,
    staleTime: 5 * 60 * 1000,
  });

  const target = isManager ? employeeId : 'me';
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'exit-clearance', target],
    queryFn: () => assetLifecycle.exitClearance(isManager ? employeeId : undefined),
    enabled: Boolean(target),
  });

  const blocked = data?.status === 'BLOCKED';

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Exit Clearance</h1>
          <p className="lr-page-sub">
            An employee cannot be cleared to leave while any organisation asset is
            still in their name. Each one must be transferred to someone else or
            returned to the store — and the transfer or return must be completed.
          </p>
        </div>
      </div>

      {isManager && (
        <div className="memo-panel">
          <label className="lr-field">
            <span>Employee</span>
            <select value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}>
              <option value="">— choose an employee to check —</option>
              {employees.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.full_name}{person.department_name ? ` — ${person.department_name}` : ''}
                </option>
              ))}
            </select>
          </label>
          <p className="lr-page-sub">
            To see everyone who still holds assets, open the{' '}
            <Link to="/inventory/reports/exit_clearance">Exit Clearance Assets report</Link>.
          </p>
        </div>
      )}

      {target && isLoading && <Skeleton rows={2} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}

      {data && (
        <div className="memo-panel" data-testid="exit-clearance">
          <div className="memo-matrix-head">
            <h3 className="memo-panel-title" style={{ margin: 0 }}>
              {data.employee}
              {data.department && <span className="lr-page-sub"> · {data.department}</span>}
            </h3>
            <span className={`min-status is-${blocked ? 'no' : 'ok'}`}>
              {blocked ? 'BLOCKED' : 'CLEAR'}
            </span>
          </div>

          <div className={`memo-notice is-${blocked ? 'warn' : 'ok'}`} role="status">
            {blocked
              ? <ShieldAlert size={15} aria-hidden="true" />
              : <CheckCircle2 size={15} aria-hidden="true" />}
            {data.message}
          </div>

          {!data.is_active && blocked && (
            <div className="memo-notice is-warn" role="status">
              <UserX size={15} aria-hidden="true" />
              This account is already inactive, and assets are still assigned to it.
            </div>
          )}

          {/*
            * Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD: the four figures are shown
            * for a CLEAR employee too. They used to appear only when blocked, so a
            * cleared employee's panel said "CLEAR" and nothing else - and "0 assigned,
            * 0 pending" is the evidence for that verdict, not an absence of news.
            */}
          <ul className="inv-clearance-figures" data-testid="clearance-figures">
            <li><span className="inv-clearance-n">{data.assets_held}</span>
              <span className="inv-clearance-l">Assigned assets</span></li>
            <li><span className="inv-clearance-n">{data.in_transfer}</span>
              <span className="inv-clearance-l">Pending transfers</span></li>
            <li><span className="inv-clearance-n">{data.in_return}</span>
              <span className="inv-clearance-l">Pending returns</span></li>
            <li className={blocked ? 'is-blocked' : 'is-clear'}>
              <span className="inv-clearance-n">{blocked ? 'BLOCKED' : 'CLEAR'}</span>
              <span className="inv-clearance-l">Clearance status</span></li>
          </ul>
          {blocked && data.unresolved > 0 && (
            <p className="lr-page-sub">
              {data.unresolved} asset{data.unresolved === 1 ? ' has' : 's have'} no
              transfer or return started yet.
            </p>
          )}

          {blocked && (
            <>

              <div className="lr-table-wrap">
                <table className="lr-table">
                  <thead>
                    <tr>
                      <th scope="col">Asset</th>
                      <th scope="col">Category</th>
                      <th scope="col">Held since</th>
                      <th scope="col">Resolution</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.assets.map((asset) => {
                      const r = RESOLUTION[asset.resolution];
                      return (
                        <tr key={asset.asset_code}>
                          <td>
                            {asset.item_id && isManager
                              ? <Link to={`/inventory/items/${asset.item_id}`}>{asset.asset_code}</Link>
                              : asset.asset_code}
                            {' '}· {asset.name}
                          </td>
                          <td>{asset.category || '—'}</td>
                          <td>{asset.assigned_date || '—'}</td>
                          <td>
                            <span className={`min-status is-${r.tone}`}>{r.label}</span>
                            {asset.transfer && (
                              <div className="lr-page-sub">
                                <Link to={`/inventory/transfers?open=${asset.transfer.id}`}>
                                  {asset.transfer.number}
                                </Link>{' '}
                                → {asset.transfer.to} · {asset.transfer.status_label}
                              </div>
                            )}
                            {asset.return && (
                              <div className="lr-page-sub">
                                {asset.return.reference} · {asset.return.status_label}
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {isManager && data.unresolved > 0 && (
                <div className="memo-matrix-actions">
                  <Link className="lr-btn lr-btn-primary" to="/inventory/transfers">
                    Raise a transfer
                  </Link>
                  <Link className="lr-btn lr-btn-ghost" to="/inventory/returns">
                    Go to Asset Return
                  </Link>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
};

export default ExitClearance;
