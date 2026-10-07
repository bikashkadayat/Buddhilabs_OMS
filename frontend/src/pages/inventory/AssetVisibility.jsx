import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Building2, Eye, Lock } from 'lucide-react';

import { assetLifecycle, inventoryService } from '../../services/inventoryService';
import { Skeleton, ErrorState, EmptyState } from '../../components/leave-records/States';

/**
 * Asset Visibility - the whole organisation's assets, read-only
 * (Phase ASSET-VISIBILITY-AND-CUSTODY-DASHBOARD).
 *
 * WHY A SEPARATE PAGE FROM THE REGISTER
 * -------------------------------------
 * The register is where the store WORKS: add, edit, assign, return. A Department
 * Head is a reader there, and the register now hides its write controls from them
 * - but it is still a workbench with the tools taken off it. This page is built
 * for the reading: every department's holdings side by side, then every asset
 * with its owner, filtered by the five things the brief names.
 *
 * READ-ONLY BY CONSTRUCTION, not by permission check. There is no write control on
 * this page for anyone, including an administrator who could write elsewhere. A
 * page that hides buttons per role is a page where the next role change shows the
 * wrong ones - which is exactly what happened on the register last phase.
 *
 * EVERYTHING IS FILTERED ON THE SERVER, for the reason the register gives: the
 * page holds one filter's rows, and narrowing them in the browser would report
 * fewer matches than exist. The department summary above the table is the
 * server's own report, so its counts and the table can never disagree.
 */
const BLANK = { department: '', owner: '', asset_type: '', condition: '', status: '', search: '' };

const Select = ({ label, value, onChange, options, all }) => (
  <label className="lr-field">
    <span>{label}</span>
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{all}</option>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  </label>
);

const AssetVisibility = () => {
  const [filters, setFilters] = useState(BLANK);
  const set = (key) => (value) => setFilters((f) => ({ ...f, [key]: value }));

  const { data: options, isError: optionsRefused, error: optionsError } = useQuery({
    queryKey: ['inventory', 'visibility-options'],
    queryFn: assetLifecycle.visibilityOptions,
    staleTime: 5 * 60 * 1000,
    retry: false,
  });

  const params = useMemo(() => Object.fromEntries(
    Object.entries(filters).filter(([, v]) => v)), [filters]);
  const { data: rows = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'visibility', params],
    queryFn: () => inventoryService.items(params),
    enabled: Boolean(options),
  });
  const { data: summary } = useQuery({
    queryKey: ['inventory', 'report', 'department_assets'],
    queryFn: () => assetLifecycle.report('department_assets'),
    enabled: Boolean(options),
  });

  if (optionsRefused) {
    const refused = optionsError?.response?.status === 403;
    return (
      <div className="page memo-page">
        <h1 className="lr-page-title">Asset Visibility</h1>
        {refused ? (
          <div className="memo-notice is-warn" role="status">
            <Lock size={15} aria-hidden="true" />
            {optionsError.response.data?.detail
              || 'Asset Visibility is for Department Heads, HR, administrators and the store.'}
            {' '}Your own assets are under <Link to="/inventory/my-assets">My assets</Link>.
          </div>
        ) : <ErrorState error={optionsError} />}
      </div>
    );
  }

  const filtered = Object.values(filters).some(Boolean);

  return (
    <div className="page memo-page" data-testid="asset-visibility">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Asset Visibility</h1>
          <p className="lr-page-sub">
            Every asset the organisation owns, in every department, with who holds
            it. <strong>Read-only</strong> — ownership changes go through HR on the
            Asset Transfer page.
          </p>
        </div>
        <span className="min-status is-muted inv-readonly-badge">
          <Eye size={13} aria-hidden="true" /> Read-only
        </span>
      </div>

      {/* --- Every department at once ---------------------------------------- */}
      {summary?.rows?.length > 0 && (
        <div className="memo-panel">
          <h3 className="memo-panel-title">
            <Building2 size={15} aria-hidden="true" /> By department
          </h3>
          <div className="lr-table-wrap">
            <table className="lr-table inv-register" data-testid="visibility-departments">
              <thead>
                <tr>
                  <th scope="col">Department</th>
                  <th scope="col">Assets owned</th>
                  <th scope="col">Assigned</th>
                  <th scope="col">Unassigned</th>
                  <th scope="col">In maintenance</th>
                  <th scope="col">Held by its staff</th>
                </tr>
              </thead>
              <tbody>
                {summary.rows.map((d) => (
                  <tr key={d.department}>
                    <td className="is-primary">{d.department}</td>
                    <td>{d.assets_owned}</td>
                    <td>{d.assigned}</td>
                    <td>{d.unassigned}</td>
                    <td>{d.in_maintenance}</td>
                    <td>{d.held_by_staff}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* --- The five filters ---------------------------------------------- */}
      <div className="memo-panel">
        <div className="inv-facets" role="group" aria-label="Filter assets">
          <Select label="Department" value={filters.department} onChange={set('department')}
            options={options?.departments || []} all="All departments" />
          <label className="lr-field">
            <span>Owner</span>
            <select value={filters.owner} onChange={(e) => set('owner')(e.target.value)}>
              <option value="">Anyone</option>
              {/* "Nobody" is a real answer - an asset nobody is accountable for is
                  exactly what a head is looking for. */}
              <option value="unassigned">Nobody — unassigned</option>
              {(options?.owners || []).map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </label>
          <Select label="Asset type" value={filters.asset_type} onChange={set('asset_type')}
            options={options?.asset_types || []} all="All types" />
          <Select label="Condition" value={filters.condition} onChange={set('condition')}
            options={options?.conditions || []} all="Any condition" />
          <Select label="Status" value={filters.status} onChange={set('status')}
            options={options?.statuses || []} all="Any status" />
          <label className="lr-field">
            <span>Search</span>
            <input type="search" value={filters.search}
              placeholder="Name, code, serial, owner…"
              onChange={(e) => set('search')(e.target.value)} />
          </label>
        </div>
        {filtered && (
          <button type="button" className="lr-btn lr-btn-ghost"
            onClick={() => setFilters(BLANK)}>
            Clear filters
          </button>
        )}
      </div>

      {/* --- Every asset --------------------------------------------------- */}
      {(!options || isLoading) && <Skeleton rows={4} />}
      {isError && <ErrorState error={error} onRetry={refetch} />}
      {options && !isLoading && !isError && rows.length === 0 && (
        <EmptyState ctaTo={null}
          message={filtered ? 'No asset matches these filters.' : 'The register is empty.'} />
      )}
      {rows.length > 0 && (
        <div className="memo-panel">
          <p className="lr-page-sub" aria-live="polite">
            {rows.length} asset{rows.length === 1 ? '' : 's'}
            {filtered ? ' match' : ' on record'}
          </p>
          <div className="lr-table-wrap">
            <table className="lr-table inv-register" data-testid="visibility-assets">
              <thead>
                <tr>
                  <th scope="col">Asset</th>
                  <th scope="col">Category</th>
                  <th scope="col">Department</th>
                  <th scope="col">Owner</th>
                  <th scope="col">Condition</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id}>
                    <td className="is-primary">
                      <Link to={`/inventory/items/${a.id}`}>{a.asset_code}</Link>
                      <div className="lr-page-sub">{a.name}</div>
                    </td>
                    <td>{a.category_name || '—'}</td>
                    <td>{a.department_name || '—'}</td>
                    <td>{a.current_holder || <span className="lr-page-sub">Unassigned</span>}</td>
                    <td>{a.condition_display}</td>
                    <td>{a.status_display}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};

export default AssetVisibility;
