import React from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Activity } from 'lucide-react';

import { assetLifecycle } from '../../services/inventoryService';

/**
 * One asset's whole life: what it is worth, what cover it has, and where it has
 * been (Phase ASSET-LIFECYCLE-DISPOSAL).
 *
 * NOTHING HERE IS CALCULATED IN THE BROWSER. Book value, accumulated
 * depreciation, the percentage written down, the warranty/AMC/end-of-life states
 * and the six phases all arrive decided by the server. They are accounting and
 * compliance figures that have to agree with the reports an auditor reads, and a
 * second derivation here would be a second opinion nobody asked for.
 *
 * This panel supersedes the old Warranty and Disposal cards on the asset page:
 * both showed fields recorded on the asset, where this shows the position
 * derived from them, which is right the instant a date passes.
 *
 * Renders nothing for a reader the endpoint refuses, as Asset History does.
 */
const STATE_LABELS = {
  active: 'Active', expiring: 'Expiring soon', expired: 'Expired',
  in_life: 'Within useful life', approaching: 'Approaching end of life',
  past: 'Past end of life',
};
const STATE_TONES = {
  active: 'ok', in_life: 'ok',
  expiring: 'warn', approaching: 'warn',
  expired: 'no', past: 'no',
};

const money = (value) => (value === null || value === undefined || value === ''
  ? '—'
  : `Rs. ${Number(value).toLocaleString('en-IN', { minimumFractionDigits: 2 })}`);

const day = (value) => (value ? new Date(value).toLocaleDateString('en-GB') : '—');

const Row = ({ k, v }) => (
  <div><dt>{k}</dt><dd>{v ?? '—'}</dd></div>
);

const StateChip = ({ state }) => (state ? (
  <span className={`min-status is-${STATE_TONES[state] || 'muted'}`}>
    {STATE_LABELS[state] || state}
  </span>
) : <span className="lr-page-sub">Not recorded</span>);

const AssetLifecycle = ({ itemId }) => {
  const { data, isError } = useQuery({
    queryKey: ['inventory', 'lifecycle-summary', itemId],
    queryFn: () => assetLifecycle.lifecycleSummary(itemId),
    enabled: Boolean(itemId),
    retry: false,
  });

  if (isError || !data) return null;
  const { depreciation: dep, warranty, amc, end_of_life: eol, disposal } = data;

  return (
    <div className="memo-panel" data-testid="asset-lifecycle">
      <div className="memo-matrix-head">
        <h3 className="memo-panel-title" style={{ margin: 0 }}>
          <Activity size={15} aria-hidden="true" /> Lifecycle &amp; Value
        </h3>
        {data.can_request_disposal && (
          <Link className="lr-btn lr-btn-ghost"
            to={`/inventory/disposals?item=${itemId}`}>
            Request disposal
          </Link>
        )}
      </div>

      {/* --- Value ------------------------------------------------------- */}
      <h4 className="memo-panel-title">Value</h4>
      {dep.depreciable ? (
        <>
          <dl className="memo-doc-meta">
            <Row k="Purchase cost" v={money(dep.purchase_cost)} />
            <Row k="Purchased" v={day(dep.purchase_date)} />
            <Row k="Method" v="Straight line" />
            <Row k="Useful life" v={`${dep.useful_life_months} months (from the ${dep.useful_life_source})`} />
            <Row k="Salvage value" v={money(dep.salvage_value)} />
            <Row k="Monthly charge" v={money(dep.monthly_charge)} />
            <Row k="In service" v={`${dep.months_elapsed} months`} />
            <Row k="Accumulated depreciation" v={money(dep.accumulated)} />
            <Row k="Book value" v={<strong>{money(dep.book_value)}</strong>} />
          </dl>
          <div className="inv-dep-bar" role="img"
            aria-label={`${dep.percent_depreciated}% depreciated`}>
            <span style={{ width: `${dep.percent_depreciated}%` }} />
          </div>
          <p className="lr-page-sub">
            {dep.percent_depreciated}% written down
            {dep.fully_depreciated && ' — fully depreciated'}
            {dep.frozen_at_disposal && ' — frozen at the disposal date'}
          </p>
        </>
      ) : (
        <p className="lr-page-sub">{dep.reason}</p>
      )}

      {/* --- Cover ------------------------------------------------------- */}
      <h4 className="memo-panel-title">Cover</h4>
      <dl className="memo-doc-meta">
        <Row k="Warranty" v={<StateChip state={warranty.state} />} />
        <Row k="Warranty period" v={warranty.start || warranty.end
          ? `${day(warranty.start)} – ${day(warranty.end)}` : '—'} />
        <Row k="AMC" v={<StateChip state={amc.state} />} />
        <Row k="AMC provider" v={amc.provider} />
        <Row k="AMC contract" v={amc.contract} />
        <Row k="AMC period" v={amc.start || amc.end
          ? `${day(amc.start)} – ${day(amc.end)}` : '—'} />
        <Row k="AMC cost" v={amc.cost ? money(amc.cost) : '—'} />
        <Row k="End of life" v={<StateChip state={eol.state} />} />
        <Row k="Expected end of life" v={day(eol.date)} />
      </dl>

      {/* --- Where it has been ------------------------------------------- */}
      {/* The custody timeline: Purchased, Assigned, Transferred, Returned,
          Maintained, Disposed - in that order, as the server sends them. */}
      <h4 className="memo-panel-title">Custody Timeline</h4>
      <ol className="inv-phase-track">
        {data.phases.map((phase) => (
          <li key={phase.key}
            className={`inv-phase${phase.count || phase.first_at ? ' is-done' : ''}`}>
            <b>{phase.label}</b>
            <span className="lr-page-sub">
              {phase.first_at
                ? <>{day(phase.first_at)}
                  {phase.derived
                    ? ' · from the purchase date'
                    : ` · ${phase.count} event${phase.count === 1 ? '' : 's'}`}</>
                : 'Not yet'}
            </span>
          </li>
        ))}
      </ol>

      {/* --- The disposal request, if there is one ------------------------ */}
      {disposal && (
        <>
          <h4 className="memo-panel-title">Disposal</h4>
          <dl className="memo-doc-meta">
            <Row k="Request" v={
              <Link to={`/inventory/disposals?open=${disposal.id}`}>
                {disposal.disposal_number}
              </Link>} />
            <Row k="Type" v={disposal.disposal_type_label} />
            <Row k="Stage" v={disposal.status_label} />
            <Row k="Requested by" v={disposal.requested_by} />
            {disposal.figures && (
              <>
                <Row k="Book value at disposal" v={money(disposal.figures.book_value)} />
                <Row k="Proceeds" v={money(disposal.figures.proceeds)} />
                <Row k="Written off" v={money(disposal.figures.written_off)} />
              </>
            )}
          </dl>
          <p className="lr-page-sub">{disposal.reason}</p>
        </>
      )}
    </div>
  );
};

export default AssetLifecycle;
