import React, { useState } from 'react';
import { BadgeCheck, BellRing, Clock, XCircle } from 'lucide-react';

import { ACK_TONES } from './circularLabels';

/**
 * Read tracking and acknowledgement.
 *
 * ONE definition of each word, used everywhere. The four acknowledgement states are
 * exclusive and sum to the total; `late` is shown as an OVERLAY - somebody who
 * acknowledged after the deadline is counted once as acknowledged and again as late.
 * Another module in this project shipped two summaries with two meanings of
 * "pending" side by side and reported eleven states for six people; this panel is
 * written to make that impossible.
 *
 * Read and acknowledgement are kept visually separate because they are different
 * facts: opening a circular is something the system observes, acknowledging it is a
 * statement the person makes. Merging them would let a page view stand in for a
 * signature.
 */
const Stat = ({ value, label, tone = 'muted' }) => (
  <div className={`cir-stat tone-${tone}`}>
    <b>{value}</b><span>{label}</span>
  </div>
);

const CircularAckPanel = ({
  readSummary, acknowledgement, myAcknowledgement, register,
  canAcknowledge, canRemind, busy, onAcknowledge, onRemind,
}) => {
  const [remarks, setRemarks] = useState('');
  const [declining, setDeclining] = useState(false);

  const ack = acknowledgement || {};
  const read = readSummary || {};

  return (
    <div className="memo-panel" data-testid="circular-ack-panel">
      <h3 className="memo-panel-title">
        <BadgeCheck size={15} aria-hidden="true" /> Circulation
      </h3>

      <div className="cir-stats" aria-label="Read statistics">
        <Stat value={read.total ?? 0} label="Recipients" />
        <Stat value={read.read ?? 0} label="Opened" tone="ok" />
        <Stat value={read.unread ?? 0} label="Not yet opened" />
        <Stat value={`${read.percent ?? 0}%`} label="Read" tone="info" />
      </div>
      <p className="lr-page-sub">
        Opening a circular is recorded automatically. It is not an acknowledgement.
      </p>

      {ack.required ? (
        <>
          <h4 className="memo-panel-subtitle">Acknowledgement</h4>
          <div className="cir-stats" aria-label="Acknowledgement statistics">
            <Stat value={ack.total ?? 0} label="Asked" />
            <Stat value={ack.acknowledged ?? 0} label="Acknowledged" tone="ok" />
            <Stat value={ack.pending ?? 0} label="Pending" tone="warn" />
            <Stat value={ack.declined ?? 0} label="Declined" tone="no" />
          </div>
          <p className="lr-page-sub">
            Acknowledged, Pending and Declined are exclusive and sum to Asked.
            {' '}<b>{ack.late ?? 0} late</b> is an overlay on the first two, not a
            fifth state.
            {ack.deadline && <> Due by <b>{ack.deadline}</b>.</>}
          </p>

          <div className="min-ack-bar" role="img"
            aria-label={`${ack.percent ?? 0}% acknowledged`}>
            <span className="min-ack-bar-fill"
              style={{ width: `${ack.percent ?? 0}%` }} />
          </div>

          {myAcknowledgement && (
            <p className="lr-page-sub" role="status">
              Your response:{' '}
              <span className={`min-status is-${
                ACK_TONES[myAcknowledgement.state] || 'muted'}`}>
                {myAcknowledgement.state_label}
              </span>
              {myAcknowledgement.is_late && (
                <span className="min-status is-no" style={{ marginLeft: 6 }}>
                  Late
                </span>
              )}
            </p>
          )}

          {canAcknowledge && (
            <div className="min-ack-actions">
              <label className="lr-field">
                <span>
                  Remarks{declining && ' * (required when declining)'}
                </span>
                <textarea rows={2} value={remarks} aria-label="Acknowledgement remarks"
                  onChange={(e) => setRemarks(e.target.value)} />
              </label>
              <div className="memo-matrix-actions">
                <button type="button" className="lr-btn lr-btn-primary"
                  disabled={busy}
                  onClick={() => onAcknowledge(true, remarks)}>
                  <BadgeCheck size={14} /> Acknowledge
                </button>
                <button type="button" className="lr-btn lr-btn-danger"
                  disabled={busy || (declining && remarks.trim().length < 10)}
                  onClick={() => {
                    if (!declining) { setDeclining(true); return; }
                    onAcknowledge(false, remarks);
                  }}>
                  <XCircle size={14} />
                  {declining ? ' Confirm decline' : ' Decline'}
                </button>
              </div>
              {declining && (
                <p className="lr-page-sub">
                  A decline is recorded against the circular with your reason. It is
                  a substantive response, not a refusal to engage — say why.
                </p>
              )}
            </div>
          )}

          {canRemind && (ack.pending ?? 0) > 0 && (
            <button type="button" className="lr-btn" disabled={busy}
              onClick={onRemind}>
              <BellRing size={14} /> Remind {ack.pending} pending
            </button>
          )}
        </>
      ) : (
        <p className="lr-page-sub">
          <Clock size={13} aria-hidden="true" /> This circular is information only
          and asks for no acknowledgement.
        </p>
      )}

      {register && register.length > 0 && (
        <div className="lr-table-wrap" style={{ marginTop: 14 }}>
          <table className="lr-table cir-register">
            <thead>
              <tr>
                <th>Recipient</th><th>Department</th><th>Opened</th>
                {ack.required && <><th>Acknowledgement</th><th>Responded</th>
                  <th>Remarks</th></>}
              </tr>
            </thead>
            <tbody>
              {register.map((row) => (
                <tr key={row.id}>
                  <td className="is-primary">
                    {row.name}
                    {row.designation && (
                      <span className="lr-page-sub"> · {row.designation}</span>
                    )}
                  </td>
                  <td>{row.department || '—'}</td>
                  <td>
                    <span className={`min-status is-${
                      row.state === 'read' ? 'ok' : 'muted'}`}>
                      {row.state === 'read' ? 'Opened' : 'Unread'}
                    </span>
                    {row.opened_at && (
                      <span className="lr-page-sub">
                        {' '}{new Date(row.opened_at).toLocaleDateString()}
                      </span>
                    )}
                  </td>
                  {ack.required && (
                    <>
                      <td>
                        <span className={`min-status is-${
                          ACK_TONES[row.ack_state] || 'muted'}`}>
                          {row.ack_state_label || '—'}
                        </span>
                        {row.ack_is_late && (
                          <span className="min-status is-no" style={{ marginLeft: 6 }}>
                            Late
                          </span>
                        )}
                      </td>
                      <td>
                        {row.ack_responded_at
                          ? new Date(row.ack_responded_at).toLocaleDateString()
                          : '—'}
                      </td>
                      <td>{row.ack_remarks || '—'}</td>
                    </>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default CircularAckPanel;
