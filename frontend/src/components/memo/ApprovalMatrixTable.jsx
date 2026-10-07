import React from 'react';
import { Check, Clock, Users, X } from 'lucide-react';
import { MemoRoleBadge, MemoStepBadge } from './badges';

const fmt = (iso) => (iso ? new Date(iso).toLocaleString(undefined, {
  year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
}) : '—');

/**
 * The step's outcome as a mark, not only as a colour: a tick for done, a clock
 * for the step currently waiting, a cross for a rejection. Paired with the status
 * badge beside it, so the row is readable in greyscale.
 */
const StepMark = ({ status }) => {
  if (status === 'completed') {
    return <span className="memo-step-mark is-done" title="Completed"><Check size={13} /></span>;
  }
  if (status === 'active') {
    return <span className="memo-step-mark is-active" title="Awaiting action"><Clock size={13} /></span>;
  }
  if (status === 'rejected') {
    return <span className="memo-step-mark is-rejected" title="Rejected"><X size={13} /></span>;
  }
  return <span className="memo-step-mark is-pending" aria-hidden="true" />;
};

/**
 * Read-only approval matrix on the memo detail page.
 *
 * Columns are the Phase 12 set: S.N. | Employee | Designation | Department |
 * Role | Current Status | Action Date | Remarks.
 *
 * The row awaiting action is highlighted and labelled "Current", so "who is this
 * sitting with" is answerable at a glance rather than by reading down a status
 * column.
 *
 * Phase 100: every <td> carries `data-label`. Below 640px the CSS hides <thead> and
 * stacks each row into a labelled card, and `content: attr(data-label)` is what
 * re-attaches the column name to its value — without it the stacked view is a
 * column of bare values with nothing saying which is the department and which the
 * role. Keep these in step with the <th> text above.
 *
 * @param {{ steps:Array<Object>, currentUserId?:string }} props
 */
const ApprovalMatrixTable = ({ steps = [], currentUserId }) => {
  if (!steps.length) {
    return (
      <div className="memo-panel">
        <h3 className="memo-panel-title"><Users size={15} aria-hidden="true" /> Approval Workflow</h3>
        <p className="lr-page-sub">No approval workflow has been set for this memo.</p>
      </div>
    );
  }

  const done = steps.filter((step) => step.status === 'completed').length;

  return (
    <div className="memo-panel">
      <div className="memo-matrix-head">
        <h3 className="memo-panel-title" style={{ margin: 0 }}>
          <Users size={15} aria-hidden="true" /> Approval Workflow
        </h3>
        <span className="memo-matrix-count">{done} of {steps.length} complete</span>
      </div>
      <div className="lr-table-wrap">
        <table className="lr-table memo-matrix-table">
          <thead>
            <tr>
              <th scope="col" className="memo-mark-col"><span className="sr-only">Outcome</span></th>
              <th scope="col" style={{ width: 52 }}>S.N.</th>
              <th scope="col">Employee</th>
              <th scope="col">Designation</th>
              <th scope="col">Department</th>
              <th scope="col">Role</th>
              <th scope="col">Current Status</th>
              <th scope="col">Action Date</th>
              <th scope="col">Remarks</th>
            </tr>
          </thead>
          <tbody>
            {steps.map((step) => {
              const isMe = currentUserId && String(step.assignee?.id) === String(currentUserId);
              const isActive = step.status === 'active';
              return (
                <tr key={step.id} className={isActive ? 'memo-row-active' : ''}>
                  <td className="memo-mark-col" data-label="Outcome"><StepMark status={step.status} /></td>
                  <td className="memo-matrix-seq" data-label="S.N.">{step.sequence}</td>
                  <td style={{ fontWeight: 600 }} data-label="Employee">
                    {step.assignee?.full_name || '—'}
                    {isMe && <span className="memo-you-tag">You</span>}
                    {isActive && <span className="memo-current-tag">Current</span>}
                  </td>
                  <td data-label="Designation">{step.designation || step.assignee?.designation || '—'}</td>
                  <td data-label="Department">{step.department_label || '—'}</td>
                  <td data-label="Role"><MemoRoleBadge role_type={step.role_type} /></td>
                  <td data-label="Status"><MemoStepBadge status={step.status} /></td>
                  <td style={{ whiteSpace: 'nowrap' }} data-label="Action Date">{fmt(step.acted_at)}</td>
                  <td className="memo-remarks-cell" data-label="Remarks">{step.remarks || '—'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ApprovalMatrixTable;
