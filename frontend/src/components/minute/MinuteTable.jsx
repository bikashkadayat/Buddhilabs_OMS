import React from 'react';
import { useNavigate } from 'react-router-dom';

import { ACK_TONES, MINUTE_STATUS_TONES, sinceLabel } from './minuteLabels';

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit',
}) : '—');

/**
 * Shared minute list table.
 *
 * The default columns are the ones the manual's archive list carries (p.10): Minute
 * Number, Reference No., Date, Type, Initiated By. `columns` picks which optional ones
 * render, so one table serves every menu without a variant per menu.
 *
 * @param {{ items:Object[], columns?:string[] }} props
 */
const MinuteTable = ({
  items = [],
  columns = ['type', 'status', 'author', 'date'],
}) => {
  const navigate = useNavigate();
  const has = (name) => columns.includes(name);

  return (
    <div className="lr-table-wrap">
      <table className="lr-table min-register">
        <thead>
          <tr>
            <th scope="col">Minute No</th>
            {has('reference') && <th scope="col">Reference No.</th>}
            <th scope="col">Subject</th>
            {has('type') && <th scope="col">Type</th>}
            {has('date') && <th scope="col">Date</th>}
            {has('author') && <th scope="col">Initiated By</th>}
            {has('status') && <th scope="col">Status</th>}
            {has('pending') && <th scope="col">Pending With</th>}
            {has('acknowledgement') && <th scope="col">Acknowledgement</th>}
            {has('since') && <th scope="col">Waiting</th>}
            {has('archived') && <th scope="col">Archived</th>}
            {has('action') && <th scope="col">Action</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((minute) => {
            const ack = minute.acknowledgement;
            return (
              <tr key={minute.id} tabIndex={0} className="lr-month-row"
                onClick={() => navigate(`/minutes/${minute.id}`)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') navigate(`/minutes/${minute.id}`);
                }}>
                <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>
                  {minute.minute_number}
                </td>
                {has('reference') && (
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {minute.reference_number || '—'}
                  </td>
                )}
                <td>
                  <div style={{ fontWeight: 600 }}>{minute.title}</div>
                </td>
                {has('type') && <td>{minute.type_label || '—'}</td>}
                {has('date') && (
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {fmtDate(minute.meeting_date)}
                  </td>
                )}
                {has('author') && <td>{minute.created_by?.full_name || '—'}</td>}
                {has('status') && (
                  <td>
                    <span className={`min-status is-${
                      MINUTE_STATUS_TONES[minute.status] || 'muted'}`}>
                      {minute.status_label}
                    </span>
                  </td>
                )}
                {has('pending') && (
                  <td>
                    {minute.pending_with ? (
                      <span className="memo-pending-cell">
                        <b>{minute.pending_with.name}</b>
                        <span className="lr-page-sub">
                          {minute.pending_with.role_label}
                        </span>
                      </span>
                    ) : '—'}
                  </td>
                )}
                {has('acknowledgement') && (
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {ack?.total ? (
                      <span className={`min-status is-${
                        ack.is_complete ? 'ok' : ACK_TONES.pending}`}>
                        {ack.acknowledged}/{ack.total}
                      </span>
                    ) : '—'}
                  </td>
                )}
                {has('since') && (
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {sinceLabel(minute.acknowledgement?.opened_at
                      || minute.created_at)}
                  </td>
                )}
                {has('archived') && (
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {fmtDate(minute.archived_at)}
                  </td>
                )}
                {has('action') && (
                  <td>
                    <button type="button" className="lr-btn lr-btn-ghost"
                      onClick={(e) => {
                        e.stopPropagation(); navigate(`/minutes/${minute.id}`);
                      }}>
                      Open
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default MinuteTable;
