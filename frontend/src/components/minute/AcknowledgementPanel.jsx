import React, { useState } from 'react';
import { BadgeCheck, BellRing, Clock, Minus, UserCheck } from 'lucide-react';

const fmt = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
}) : '—');

const STATUS = {
  acknowledged: { label: 'Acknowledged', tone: 'ok', Icon: BadgeCheck },
  pending: { label: 'Pending', tone: 'wait', Icon: Clock },
  not_required: { label: 'Not required', tone: 'muted', Icon: Minus },
};

/**
 * The acknowledgement register (E-minute-manual pp. 8-10).
 *
 * WHO IS ASKED IS DECIDED BY ATTENDANCE. "The minute will be assigned to the members
 * present in the meeting" (p.8), so the tally counts members present and nobody else;
 * an absent member appears in the register stamped ABSENT and is not chased. When the
 * last present member acknowledges, the server archives the minute (p.10) - this panel
 * reports that, it does not decide it.
 *
 * There is no OTP step: the manual emails a one-time password first (p.9), and that is
 * excluded here by instruction. Acknowledging is one click plus an optional remark.
 *
 * @param {{ summary:Object, deadline:Object, participants:Array,
 *           canAcknowledge:boolean, canRemind:boolean, myRow:Object|null,
 *           busy:boolean, onAcknowledge:(remarks:string)=>void,
 *           onRemind:()=>void }} props
 */
const AcknowledgementPanel = ({
  summary, deadline = null, participants = [], canAcknowledge = false,
  canRemind = false, myRow = null, busy = false, onAcknowledge, onRemind,
}) => {
  const [remarks, setRemarks] = useState('');

  if (!participants.length) return null;

  return (
    <div className="memo-panel">
      <h3 className="memo-panel-title">
        <UserCheck size={15} aria-hidden="true" /> Acknowledgement
      </h3>

      {/* Each figure carries its own icon and word, so the states are
          distinguishable without colour. */}
      <div className="min-ack-tally" aria-label="Acknowledgement summary">
        <span className="min-ack-total">
          <b>{summary?.total ?? 0}</b> member
          {(summary?.total ?? 0) === 1 ? '' : 's'} present
        </span>
        <span className="min-ack-chip is-ok">
          <BadgeCheck size={14} aria-hidden="true" /> {summary?.acknowledged ?? 0}
          {' '}acknowledged
        </span>
        <span className="min-ack-chip is-wait">
          <Clock size={14} aria-hidden="true" /> {summary?.pending ?? 0} pending
        </span>
      </div>

      {summary?.total > 0 && (
        <div className="min-ack-bar" role="img"
          aria-label={`${summary.percent}% acknowledged`}>
          <span className="min-ack-bar-fill" style={{ width: `${summary.percent}%` }} />
        </div>
      )}

      {deadline?.due_date && (
        <p className={`min-ack-deadline ${deadline.is_overdue ? 'is-overdue' : ''}`}>
          Due by {deadline.due_date}
          {deadline.is_overdue && ` · ${deadline.late} outstanding past the deadline`}
        </p>
      )}

      {!summary?.is_open && summary?.is_complete && (
        <p className="lr-page-sub">
          Every member present has acknowledged, so the minute is archived.
        </p>
      )}

      <div className="lr-table-wrap">
        <table className="lr-table min-register is-narrow">
          <thead>
            <tr>
              <th scope="col">Member</th>
              <th scope="col">Department</th>
              <th scope="col">Attendance</th>
              <th scope="col">Acknowledgement</th>
              <th scope="col">When</th>
            </tr>
          </thead>
          <tbody>
            {participants.map((row) => {
              const state = STATUS[row.ack_status] || STATUS.not_required;
              const { Icon } = state;
              return (
                <tr key={row.id}>
                  <td style={{ fontWeight: 600 }}>
                    {row.name}
                    {row.designation && (
                      <div className="lr-page-sub">{row.designation}</div>
                    )}
                  </td>
                  <td>{row.department_label || '—'}</td>
                  <td>
                    <span className={`min-status is-${
                      row.attendance === 'absent' ? 'no'
                        : row.attendance === 'invitee' ? 'info' : 'ok'}`}>
                      {row.attendance_label}
                    </span>
                  </td>
                  <td>
                    <span className={`min-status is-${state.tone}`}>
                      <Icon size={12} aria-hidden="true" /> {state.label}
                    </span>
                    {row.is_late && (
                      <span className="min-overdue-tag">Late</span>
                    )}
                    {row.remarks && (
                      <div className="lr-page-sub">“{row.remarks}”</div>
                    )}
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {row.acknowledged_at ? fmt(row.acknowledged_at) : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {canAcknowledge && (
        <div className="min-ack-actions">
          <label className="lr-field">
            <span>Remarks (optional)</span>
            <textarea rows={2} value={remarks} aria-label="Acknowledgement remarks"
              onChange={(e) => setRemarks(e.target.value)} />
          </label>
          <button type="button" className="lr-btn lr-btn-primary" disabled={busy}
            onClick={() => onAcknowledge(remarks)}>
            <BadgeCheck size={14} /> {busy ? 'Recording…' : 'Acknowledge'}
          </button>
        </div>
      )}

      {myRow?.ack_status === 'acknowledged' && (
        <p className="min-ack-mine">
          You acknowledged this minute on {fmt(myRow.acknowledged_at)}.
          {myRow.remarks && <> “{myRow.remarks}”</>}
        </p>
      )}

      {canRemind && (
        <button type="button" className="lr-btn" disabled={busy} onClick={onRemind}>
          <BellRing size={14} /> Remind {summary?.pending ?? 0} outstanding
        </button>
      )}
    </div>
  );
};

export default AcknowledgementPanel;
