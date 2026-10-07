import React from 'react';
import { useNavigate } from 'react-router-dom';
import { MemoStatusBadge, MemoTypeBadge } from './badges';
import { AGEING_TONES, dueDaysLabel, pendingSinceLabel } from './memoLabels';
import { Check } from 'lucide-react';

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit',
}) : '—');

/**
 * Shared memo list table.
 *
 * @param {{ items:Object[], columns?:string[] }} props
 *   `columns` picks which optional columns to render, so one table serves the
 *   inbox (needs pending-since and due-days), the archive (needs approved and
 *   archived dates) and the drafts list (needs neither) without a variant per
 *   menu.
 *
 * Inbox rows carry an SLA tone on the row itself (green / amber / red) AND the
 * due-days text in the cell, so urgency is never communicated by colour alone.
 * The tone comes from the server (`ageing.state`) rather than being recomputed
 * here, so the colour and the number cannot disagree.
 */
const MemoTable = ({ items, columns = ['type', 'status', 'author', 'created'] }) => {
  const navigate = useNavigate();
  const has = (name) => columns.includes(name);

  return (
    <div className="lr-table-wrap">
      <table className="lr-table">
        <thead>
          <tr>
            <th scope="col">Memo No</th>
            <th scope="col">Subject</th>
            {has('type') && <th scope="col">Type</th>}
            {has('department') && <th scope="col">Department</th>}
            {has('author') && <th scope="col">Created By</th>}
            {has('status') && <th scope="col">Status</th>}
            {has('pending') && <th scope="col">Current Assignee</th>}
            {has('pendingSince') && <th scope="col">Pending Since</th>}
            {has('dueDays') && <th scope="col">Due Days</th>}
            {has('created') && <th scope="col">Created</th>}
            {has('approvedBy') && <th scope="col">Approved By</th>}
            {has('approved') && <th scope="col">Approved</th>}
            {has('archived') && <th scope="col">Archived</th>}
            {has('action') && <th scope="col">Action</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((memo) => {
            const ageing = memo.ageing;
            const tone = has('dueDays') && ageing?.state
              ? AGEING_TONES[ageing.state]?.tone
              : null;
            return (
              <tr key={memo.id} tabIndex={0}
                className={`lr-month-row ${tone ? `memo-sla-${tone}` : ''}`}
                onClick={() => navigate(`/memos/${memo.id}`)}
                onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/memos/${memo.id}`); }}>
                <td style={{ fontWeight: 600, whiteSpace: 'nowrap' }}>{memo.memo_number}</td>
                <td>
                  <div style={{ fontWeight: 600 }}>{memo.subject}</div>
                  {memo.to_line && (
                    <div className="lr-page-sub">To: {memo.to_line}</div>
                  )}
                </td>
                {has('type') && <td><MemoTypeBadge memo_type={memo.memo_type} /></td>}
                {has('department') && <td>{memo.department_label || '—'}</td>}
                {has('author') && <td>{memo.created_by?.full_name || '—'}</td>}
                {has('status') && <td><MemoStatusBadge status={memo.status} /></td>}
                {has('pending') && (
                  <td>
                    {memo.pending_with ? (
                      <span className="memo-pending-cell">
                        <b>{memo.pending_with.name}</b>
                        <span className="lr-page-sub">{memo.pending_with.role_label}</span>
                      </span>
                    ) : '—'}
                  </td>
                )}
                {has('pendingSince') && (
                  <td style={{ whiteSpace: 'nowrap' }}>{pendingSinceLabel(ageing)}</td>
                )}
                {has('dueDays') && (
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <span className={`memo-due ${tone ? `is-${tone}` : ''}`}>
                      {dueDaysLabel(ageing)}
                    </span>
                  </td>
                )}
                {has('created') && <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(memo.created_at)}</td>}
                {/* Phase 26: the archive must answer "who approved this" from the
                    list, not only from the memo. */}
                {has('approvedBy') && (
                  <td>
                    {memo.approved_by ? (
                      <span className="memo-approved-by">
                        <Check size={14} className="memo-approved-tick" aria-hidden="true" />
                        {memo.approved_by}
                      </span>
                    ) : '—'}
                  </td>
                )}
                {has('approved') && <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(memo.approved_at)}</td>}
                {has('archived') && <td style={{ whiteSpace: 'nowrap' }}>{fmtDate(memo.archived_at)}</td>}
                {has('action') && (
                  <td>
                    <button type="button" className="lr-btn lr-btn-ghost"
                      onClick={(e) => { e.stopPropagation(); navigate(`/memos/${memo.id}`); }}>
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

export default MemoTable;
