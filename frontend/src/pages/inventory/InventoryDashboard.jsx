import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  AlertTriangle, Archive, Boxes, Building2, CalendarClock, CheckCircle2,
  ClipboardList, HandCoins, PackageOpen, Plus, Repeat, ShieldCheck,
  ShoppingCart, Trash2, Truck, Undo2, UserX, Wrench,
} from 'lucide-react';

import { assetLifecycle } from '../../services/inventoryService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

const Tile = ({ label, value, to, icon, tone = 'neutral', hint }) => (
  <Link to={to} className={`memo-tile tone-${tone}`}>
    <span className="memo-tile-ico" aria-hidden="true">{icon}</span>
    <span className="memo-tile-value">{value ?? '—'}</span>
    <span className="memo-tile-label">{label}</span>
    {hint && <span className="memo-tile-hint">{hint}</span>}
  </Link>
);

/**
 * Inventory dashboard (Phase 70.9, 70.14).
 *
 * The server decides what this page IS. `scope` comes back as `organisation` for
 * anyone who can see the register and `self` for everybody else, and the two are
 * genuinely different pages rather than the same page with things hidden. An
 * employee gets their own assets; a 403 for most of the organisation would be a
 * worse answer than a smaller room.
 *
 * Every figure is a COUNT query on the server. Nothing here adds anything up.
 */
const AssetRow = ({ row }) => (
  <tr>
    <td className="is-primary">
      <Link to={`/inventory/items/${row.item_id}`}>{row.code}</Link>
    </td>
    <td>{row.name}</td>
    <td>{row.assigned_date || '—'}</td>
    <td>{row.condition || '—'}</td>
    <td>{row.accessories || '—'}</td>
  </tr>
);

const MyAssets = ({ data }) => (
  <div className="page memo-page">
    <div className="lr-page-head">
      <div>
        <h1 className="lr-page-title">My Assets</h1>
        <p className="lr-page-sub">
          What you currently hold, and what you have held before
        </p>
      </div>
      <Link to="/inventory/requests" className="lr-btn lr-btn-primary">
        <Plus size={14} /> Request an asset
      </Link>
    </div>

    <div className="memo-panel">
      <h3 className="memo-panel-title">Currently Assigned</h3>
      {data.assigned.length === 0 ? (
        <p className="lr-page-sub">You are not holding any assets.</p>
      ) : (
        <div className="lr-table-wrap">
          <table className="lr-table inv-register">
            <thead>
              <tr><th>Asset Code</th><th>Item</th><th>Assigned</th>
                <th>Condition</th><th>Accessories</th></tr>
            </thead>
            <tbody>
              {data.assigned.map((row) => <AssetRow key={row.id} row={row} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>

    {data.history.length > 0 && (
      <div className="memo-panel">
        <h3 className="memo-panel-title">Previously Held</h3>
        <div className="lr-table-wrap">
          <table className="lr-table inv-register">
            <thead>
              <tr><th>Asset Code</th><th>Item</th><th>Assigned</th>
                <th>Returned</th><th>Condition on return</th></tr>
            </thead>
            <tbody>
              {data.history.map((row) => (
                <tr key={row.id}>
                  <td className="is-primary">{row.code}</td>
                  <td>{row.name}</td>
                  <td>{row.assigned_date || '—'}</td>
                  <td>{row.returned_at
                    ? new Date(row.returned_at).toLocaleDateString() : '—'}</td>
                  <td>{row.return_condition || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )}
  </div>
);

const monthLabel = (key) => {
  const [year, month] = key.split('-').map(Number);
  return new Date(year, month - 1, 1).toLocaleString('en-GB', { month: 'short' });
};

/**
 * Completed transfers per month, last six months (Phase ASSET-CUSTODY-TRANSFER).
 *
 * One series, so no legend - the heading names it. Every month is drawn, zeros
 * included; the server guarantees the months are contiguous, because a chart that
 * skipped a quiet month would draw it as steady. Only the latest month carries a
 * value label; the rest are read from the axis, the hover tooltip or the table
 * underneath, so a value is never reachable by hover alone.
 */
const TransferTrend = ({ trend = [] }) => {
  const [hover, setHover] = useState(null);
  if (trend.length === 0) return null;

  const W = 420; const H = 150; const LEFT = 26; const RIGHT = 8;
  const TOP = 18; const AXIS = 22;
  const plotH = H - TOP - AXIS;
  const peak = Math.max(...trend.map((m) => m.completed));
  // Whole numbers only: a transfer count of 1.5 is not a thing.
  const yMax = Math.max(peak <= 4 ? peak : Math.ceil(peak / 5) * 5, 1);
  const ticks = yMax <= 4 ? Array.from({ length: yMax + 1 }, (_, i) => i) : [0, yMax / 2, yMax];
  const band = (W - LEFT - RIGHT) / trend.length;
  const barW = Math.min(24, band * 0.5);
  const y = (v) => TOP + plotH - (v / yMax) * plotH;
  const last = trend.length - 1;

  return (
    <div className="memo-panel inv-trend" data-testid="transfer-trend">
      <h3 className="memo-panel-title">Transfers completed per month</h3>
      <div className="inv-trend-plot">
        <svg viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={`Completed transfers, last ${trend.length} months: ${trend
            .map((m) => `${monthLabel(m.month)} ${m.completed}`).join(', ')}`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={LEFT} x2={W - RIGHT} y1={y(t)} y2={y(t)} className="inv-trend-grid" />
              <text x={LEFT - 6} y={y(t)} dy="0.32em" textAnchor="end"
                className="inv-trend-tick">{t}</text>
            </g>
          ))}
          {trend.map((m, i) => {
            const cx = LEFT + band * i + band / 2;
            const top = y(m.completed);
            const h = TOP + plotH - top;
            const r = Math.min(4, h);
            const x0 = cx - barW / 2;
            const base = TOP + plotH;
            return (
              <g key={m.month}>
                {m.completed > 0 && (
                  <path className={`inv-trend-bar${hover === i ? ' is-hover' : ''}`}
                    d={`M${x0},${base} V${top + r} Q${x0},${top} ${x0 + r},${top} `
                      + `H${x0 + barW - r} Q${x0 + barW},${top} ${x0 + barW},${top + r} V${base} Z`} />
                )}
                {i === last && (
                  <text x={cx} y={top - 5} textAnchor="middle" className="inv-trend-value">
                    {m.completed}
                  </text>
                )}
                <text x={cx} y={H - 6} textAnchor="middle" className="inv-trend-tick">
                  {monthLabel(m.month)}
                </text>
                {/* The whole band is the hover target, not the bar - a zero month
                    has no bar at all and must still answer. */}
                <rect x={LEFT + band * i} y={TOP} width={band} height={plotH}
                  className="inv-trend-hit" tabIndex={0}
                  aria-label={`${monthLabel(m.month)}: ${m.completed} completed`}
                  onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(i)} onBlur={() => setHover(null)} />
              </g>
            );
          })}
        </svg>
        {hover !== null && (
          <div className="inv-trend-tip" role="status"
            style={{ left: `${((LEFT + band * hover + band / 2) / W) * 100}%` }}>
            <strong>{trend[hover].completed}</strong> completed
            <span>{monthLabel(trend[hover].month)} {trend[hover].month.slice(0, 4)}</span>
          </div>
        )}
      </div>
      <details className="inv-trend-table">
        <summary>View as table</summary>
        {/* .lr-table carries min-width: 640px, so on a phone it is wider than the
            screen and must sit in the scroll container every other table here
            uses. Without it the table simply escaped the <details> - which the
            responsive probe only saw once the fixture had enough months in it to
            make the table wide. */}
        <div className="lr-table-wrap">
          <table className="lr-table">
            <thead><tr><th scope="col">Month</th><th scope="col">Completed</th></tr></thead>
            <tbody>
              {trend.map((m) => (
                <tr key={m.month}><td>{m.month}</td><td>{m.completed}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
};

const InventoryDashboard = () => {
  const navigate = useNavigate();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['inventory', 'dashboard'],
    queryFn: assetLifecycle.dashboard,
  });

  if (isLoading) return <div className="page"><Skeleton rows={4} /></div>;
  if (isError) {
    return <div className="page"><ErrorState error={error} onRetry={refetch} /></div>;
  }
  if (data.scope === 'self') return <MyAssets data={data} />;

  const counts = data.counts;
  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Inventory Dashboard</h1>
          <p className="lr-page-sub">
            Asset lifecycle, custody and stock across the organisation
          </p>
        </div>
        <button type="button" className="lr-btn lr-btn-primary"
          onClick={() => navigate('/inventory')}>
          <Plus size={14} /> Asset register
        </button>
      </div>

      {/* The eight cards Phase 70.14 names. */}
      <div className="memo-tiles" data-testid="inventory-tiles">
        <Tile label="Total Assets" value={counts.total_assets} to="/inventory"
          icon={<Boxes size={18} />} tone="neutral"
          hint="On the books — excludes disposed" />
        <Tile label="Assigned" value={counts.assigned} to="/inventory?status=assigned"
          icon={<CheckCircle2 size={18} />} tone="info" />
        <Tile label="Available" value={counts.available} to="/inventory?status=available"
          icon={<PackageOpen size={18} />} tone="ok" hint="In stock, ready to issue" />
        <Tile label="Maintenance" value={counts.maintenance}
          to="/inventory/maintenance" icon={<Wrench size={18} />}
          tone={counts.maintenance ? 'warn' : 'neutral'} />
        <Tile label="Taken Out" value={counts.taken_out} to="/inventory?status=out"
          icon={<Truck size={18} />} tone="info" />
        <Tile label="Pending Requests" value={counts.pending_requests}
          to="/inventory/requests" icon={<ClipboardList size={18} />}
          tone={counts.pending_requests ? 'warn' : 'neutral'} />
        <Tile label="Overdue Returns" value={counts.overdue_returns}
          to="/inventory/reports/unreturned" icon={<AlertTriangle size={18} />}
          tone={counts.overdue_returns ? 'danger' : 'neutral'}
          hint="Past their return date" />
        <Tile label="Disposed" value={counts.disposed}
          to="/inventory/reports/disposed" icon={<Archive size={18} />} tone="ok" />
      </div>

      <h2 className="memo-section-head">Stock</h2>
      <div className="memo-tiles" data-testid="inventory-stock-tiles">
        <Tile label="On Order" value={counts.on_order} to="/inventory?status=procurement"
          icon={<ShoppingCart size={18} />} tone="neutral"
          hint="Ordered, not yet delivered" />
        <Tile label="Awaiting Check-In" value={counts.awaiting_check_in}
          to="/inventory?status=received" icon={<PackageOpen size={18} />}
          tone={counts.awaiting_check_in ? 'warn' : 'neutral'}
          hint="Delivered but not yet on the shelf" />
        <Tile label="Retired" value={counts.retired} to="/inventory?status=retired"
          icon={<Archive size={18} />} tone="neutral" />
        <Tile label="Warranty Expiring" value={counts.warranty_expiring}
          to="/inventory/reports/warranty_expiring" icon={<AlertTriangle size={18} />}
          tone={counts.warranty_expiring ? 'warn' : 'neutral'}
          hint="Within 60 days" />
      </div>

      {/*
        * Phase ASSET-TRANSFER-GOVERNANCE.
        *
        * The brief lists five figures for the head's dashboard: Total, Department,
        * Assigned, Unassigned and Transferred assets. THREE OF THEM ARE ALREADY ON
        * THIS PAGE and are not repeated here - "Total Assets" and "Assigned" are in
        * the first block, and "Unassigned" is the Custody block's "Assets With No
        * Owner", which counts the same thing by the same rule (no active custody
        * row). Adding second copies would have put two tiles reading "Total Assets"
        * on one screen, which answers the brief's list and makes the page worse.
        *
        * What is genuinely new is here: the viewer's own department, which nothing
        * on this page showed before, and the all-time transferred count next to the
        * existing this-month one.
        */}
      <h2 className="memo-section-head">Organisation</h2>
      <div className="memo-tiles" data-testid="inventory-governance-tiles">
        <Tile label="Department Assets"
          value={counts.governance_department_assets ?? '—'}
          to="/inventory" icon={<Building2 size={18} />} tone="neutral"
          hint={counts.governance_department_name
            ? `Owned by ${counts.governance_department_name}`
            : 'No department recorded against your account'} />
        <Tile label="Transferred Assets" value={counts.governance_transferred_assets}
          to="/inventory/reports/transfer_visibility" icon={<Repeat size={18} />}
          tone="neutral" hint="Assets that have changed hands at least once" />
      </div>

      {/* Phase ASSET-CUSTODY-TRANSFER. */}
      <h2 className="memo-section-head">Custody</h2>
      <div className="memo-tiles" data-testid="inventory-custody-tiles">
        <Tile label="Pending Transfers" value={counts.pending_transfers}
          to="/inventory/transfers" icon={<Repeat size={18} />}
          tone={counts.pending_transfers ? 'warn' : 'neutral'}
          hint="Awaiting a department head, HR or admin" />
        <Tile label="Assets Awaiting Return" value={counts.assets_awaiting_return}
          to="/inventory/returns" icon={<Undo2 size={18} />}
          tone={counts.assets_awaiting_return ? 'warn' : 'neutral'}
          hint="Raised, not yet accepted by the store" />
        <Tile label="Assets With No Owner" value={counts.assets_with_no_owner}
          to="/inventory/reports/ownership" icon={<PackageOpen size={18} />}
          tone="neutral" hint="In service with nobody accountable" />
        <Tile label="Transferred This Month" value={counts.transferred_this_month}
          to="/inventory/reports/transfers" icon={<CheckCircle2 size={18} />}
          tone="ok" />
        <Tile label="Held by Former Staff" value={counts.assets_held_by_inactive_employees}
          to="/inventory/reports/exit_clearance" icon={<UserX size={18} />}
          tone={counts.assets_held_by_inactive_employees ? 'danger' : 'neutral'}
          hint="Inactive accounts still holding assets" />
      </div>
      {counts.assigned_without_custody_record > 0 && (
        <div className="memo-notice is-warn" role="status">
          <AlertTriangle size={14} aria-hidden="true" />
          {counts.assigned_without_custody_record} asset(s) are marked assigned or taken out
          but have no active custody record. The register and custody disagree.
        </div>
      )}
      {/*
        * Phase ASSET-LIFECYCLE-DISPOSAL.
        *
        * Book value is a money tile among counts, so it says so in its hint
        * rather than looking like a number of assets. It is the server's figure
        * verbatim - no arithmetic happens in this file.
        */}
      <h2 className="memo-section-head">Lifecycle &amp; Value</h2>
      <div className="memo-tiles" data-testid="inventory-lifecycle-tiles">
        <Tile label="Net Book Value" value={`Rs. ${Number(counts.book_value || 0).toLocaleString('en-IN')}`}
          to="/inventory/reports/depreciation" icon={<HandCoins size={18} />}
          tone="neutral"
          hint={`Across ${counts.book_value_assets} asset(s) on the books`} />
        <Tile label="Pending Disposals" value={counts.pending_disposals}
          to="/inventory/disposals" icon={<Trash2 size={18} />}
          tone={counts.pending_disposals ? 'warn' : 'neutral'}
          hint="Awaiting a department head or admin" />
        <Tile label="AMC Expiring" value={counts.amc_expiring}
          to="/inventory/reports/amc_expiring" icon={<ShieldCheck size={18} />}
          tone={counts.amc_expiring ? 'warn' : 'neutral'}
          hint="Contract ended or ending within 60 days" />
        <Tile label="Past End of Life" value={counts.end_of_life_past}
          to="/inventory/reports/lifecycle" icon={<CalendarClock size={18} />}
          tone={counts.end_of_life_past ? 'danger' : 'neutral'}
          hint={`${counts.end_of_life_approaching} more within 90 days`} />
        <Tile label="Written Off This Year" value={`Rs. ${Number(counts.written_off_this_year || 0).toLocaleString('en-IN')}`}
          to="/inventory/reports/write_off" icon={<Archive size={18} />}
          tone="neutral"
          hint={`${counts.disposed_this_year} disposal(s), ${counts.lost_this_year} lost`} />
      </div>
      {counts.assets_missing_depreciation_data > 0 && (
        <div className="memo-notice is-warn" role="status">
          <AlertTriangle size={14} aria-hidden="true" />
          {counts.assets_missing_depreciation_data} asset(s) are missing a purchase
          cost, purchase date or useful life, so they are not in the net book value
          above. The depreciation report names them.
        </div>
      )}

      <TransferTrend trend={counts.transfer_trends} />

      {data.low_stock?.length > 0 && (
        <div className="memo-panel">
          <h3 className="memo-panel-title">
            <AlertTriangle size={15} aria-hidden="true" /> Low Stock
          </h3>
          <p className="lr-page-sub">
            Counted over what is <b>available to issue</b>, not what exists. A
            category whose assets are all assigned has nothing to give out.
          </p>
          <div className="lr-table-wrap">
            <table className="lr-table inv-register">
              <thead>
                <tr><th>Category</th><th>Available</th><th>Total</th></tr>
              </thead>
              <tbody>
                {data.low_stock.map((row) => (
                  <tr key={row.id}>
                    <td className="is-primary">{row.category}</td>
                    <td><b>{row.available}</b></td>
                    <td>{row.total}</td>
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

export default InventoryDashboard;
