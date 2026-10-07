import React from 'react';
import { Link } from 'react-router-dom';

import { CIRCULAR_STATUS_TONES, PRIORITY_TONES } from './circularLabels';

/**
 * The circular list table.
 *
 * Column sets differ per menu because the question each menu answers differs: a
 * broadcast queue needs "how many received it and how many have read it", a draft
 * queue needs "who is it with", and an archive needs "who issued it and when". So
 * the caller passes `columns` and this renders what it is handed - one table
 * implementation rather than nine.
 *
 * Inside `.lr-table-wrap`, which scrolls horizontally: the reach column set is wide
 * and clipping it would hide the figures the page exists to show.
 */
const Cell = ({ children }) => <td>{children ?? '—'}</td>;

const Reach = ({ reach }) => {
  if (!reach) return <>—</>;
  return (
    <span className="cir-reach">
      <b>{reach.read}</b>/{reach.recipients}
      <span className="cir-reach-pct"> · {reach.percent}% read</span>
    </span>
  );
};

const CircularTable = ({ items = [], columns = [] }) => {
  const has = (name) => columns.includes(name);
  return (
    <div className="lr-table-wrap">
      <table className="lr-table cir-register">
        <thead>
          <tr>
            <th>Circular No</th>
            <th>Subject</th>
            {has('category') && <th>Category</th>}
            {has('classification') && <th>Classification</th>}
            {has('department') && <th>Department</th>}
            {has('status') && <th>Status</th>}
            {has('pending') && <th>Pending With</th>}
            {has('issued') && <th>Issued</th>}
            {has('reach') && <th>Reach</th>}
            {has('acknowledgement') && <th>Acknowledgement</th>}
            {has('archived') && <th>Archived</th>}
          </tr>
        </thead>
        <tbody>
          {items.map((row) => (
            <tr key={row.id}>
              <td>
                <Link to={`/circulars/${row.id}`} className="cir-ref">
                  {row.circular_number}
                </Link>
              </td>
              <td className="is-primary">
                <Link to={`/circulars/${row.id}`} className="cir-subject">
                  {row.subject}
                </Link>
                {row.external_reference && (
                  <span className="lr-page-sub"> · {row.external_reference}</span>
                )}
              </td>
              {has('category') && <Cell>{row.category_label}</Cell>}
              {has('classification') && (
                <td>
                  <span className={`min-status is-${
                    row.classification === 'internal' ? 'muted' : 'no'}`}>
                    {row.classification_label}
                  </span>
                </td>
              )}
              {has('department') && <Cell>{row.department_label}</Cell>}
              {has('status') && (
                <td>
                  <span className={`min-status is-${
                    CIRCULAR_STATUS_TONES[row.status] || 'muted'}`}>
                    {row.status_label}
                  </span>
                  {row.priority !== 'normal' && (
                    <span className={`min-status is-${
                      PRIORITY_TONES[row.priority] || 'muted'}`}
                    style={{ marginLeft: 6 }}>
                      {row.priority_label}
                    </span>
                  )}
                </td>
              )}
              {has('pending') && (
                <Cell>
                  {row.pending_with
                    ? `${row.pending_with.name} · ${row.pending_with.role_label}`
                    : null}
                </Cell>
              )}
              {has('issued') && (
                <Cell>
                  {row.issued_by_name && (
                    <>
                      {row.issued_by_name}
                      <span className="lr-page-sub">
                        {' '}· {row.issue_date || '—'}
                      </span>
                    </>
                  )}
                </Cell>
              )}
              {has('reach') && <td><Reach reach={row.reach} /></td>}
              {has('acknowledgement') && (
                <Cell>
                  {row.acknowledgement_required ? 'Required' : 'Not required'}
                </Cell>
              )}
              {has('archived') && (
                <Cell>
                  {row.archived_at
                    ? new Date(row.archived_at).toLocaleDateString()
                    : null}
                </Cell>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export default CircularTable;
