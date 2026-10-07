import React, { useState } from 'react';
import { num, days } from '../../utils/leaveFormat';
import { X } from 'lucide-react';

/**
 * Balance card for one leave type. Shows a segmented progress bar
 * (used / pending / available) with a text legend, and available prominently.
 * A "Details" link opens a small modal breakdown.
 *
 * @param {{
 *   leaveType:string, color?:string, entitled:number|string, used:number|string,
 *   pending:number|string, available:number|string, carriedForward?:number|string,
 *   compact?:boolean
 * }} props
 */
const BalanceCard = ({
  leaveType,
  color = '#2563EB',
  entitled,
  used,
  pending,
  available,
  carriedForward = 0,
  compact = false,
}) => {
  const [open, setOpen] = useState(false);
  const total = num(entitled) + num(carriedForward);
  const usedN = num(used);
  const pendingN = num(pending);
  const availableN = num(available);
  const pct = (v) => (total > 0 ? Math.min(100, (num(v) / total) * 100) : 0);

  return (
    <div className={`lr-balance-card${compact ? ' lr-balance-compact' : ''}`}>
      <div className="lr-bc-head">
        <span className="lr-bc-swatch" style={{ background: color }} aria-hidden="true" />
        <span className="lr-bc-type">{leaveType}</span>
      </div>

      <div className="lr-bc-available">
        <span className="lr-bc-available-num">{days(availableN)}</span>
        <span className="lr-bc-available-lbl">days available</span>
      </div>

      <div
        className="lr-bc-bar"
        role="img"
        aria-label={`${leaveType}: ${days(usedN)} used, ${days(pendingN)} pending, ${days(availableN)} available of ${days(total)} entitled`}
      >
        <span className="lr-bc-seg lr-seg-used" style={{ width: `${pct(usedN)}%`, background: color }} />
        <span className="lr-bc-seg lr-seg-pending" style={{ width: `${pct(pendingN)}%` }} />
      </div>

      {!compact && (
        <ul className="lr-bc-legend">
          <li><span className="lr-dot lr-dot-used" style={{ background: color }} />Used <b>{days(usedN)}</b></li>
          <li><span className="lr-dot lr-dot-pending" />Pending <b>{days(pendingN)}</b></li>
          <li><span className="lr-dot lr-dot-avail" />Available <b>{days(availableN)}</b></li>
        </ul>
      )}

      {!compact && (
        <button type="button" className="lr-bc-details" onClick={() => setOpen(true)} aria-haspopup="dialog">
          Details
        </button>
      )}

      {open && (
        <div className="lr-modal-overlay" role="dialog" aria-modal="true" aria-label={`${leaveType} breakdown`} onClick={() => setOpen(false)}>
          <div className="lr-modal" onClick={(e) => e.stopPropagation()}>
            <div className="lr-modal-head">
              <h3>{leaveType} breakdown</h3>
              <button type="button" className="lr-modal-close" aria-label="Close" onClick={() => setOpen(false)}><X size={18} aria-hidden="true" /></button>
            </div>
            <dl className="lr-modal-grid">
              <div><dt>Entitled</dt><dd>{days(entitled)}</dd></div>
              <div><dt>Carried forward</dt><dd>{days(carriedForward)}</dd></div>
              <div><dt>Used</dt><dd>{days(usedN)}</dd></div>
              <div><dt>Pending</dt><dd>{days(pendingN)}</dd></div>
              <div><dt>Available</dt><dd><b>{days(availableN)}</b></dd></div>
            </dl>
          </div>
        </div>
      )}
    </div>
  );
};

export default BalanceCard;
