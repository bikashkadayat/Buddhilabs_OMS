import React, { useState } from 'react';
import { ChevronDown, ChevronUp, GripVertical, Trash2, Users } from 'lucide-react';
import EmployeeSelector from './EmployeeSelector';
import { MemoRoleBadge } from './badges';
import { ROLE_TYPES, roleTypeLabel } from './memoLabels';

/**
 * The editable approval matrix (Phase 4 / Phase 12 item 3).
 *
 * Sequence is the row's position in the array — the server reads list order and
 * numbers the steps itself, so reordering never has to renumber anything here and
 * there is no way for the two to disagree.
 *
 * Reordering is offered twice on purpose: pointer drag for speed, and up/down
 * buttons because drag-and-drop is unusable with a keyboard or a screen reader.
 * Both drive the same `move()`.
 *
 * @param {{ rows:Array<Object>, onChange:(rows:Array<Object>)=>void,
 *           readOnly?:boolean, error?:string }} props
 *   Each row: { assignee_id, full_name, designation, department, role_type }
 */
/**
 * @param {{ roles?:Array<{code:string,label:string}>, roleKey?:string }} props
 *   `roles` overrides the memo role vocabulary and `roleKey` the property each row
 *   stores it under ('role_type' for memos, 'role' for minutes, whose roles are
 *   database rows rather than an enum). Defaulted so existing memo callers are
 *   unchanged - this component is shared with the minute module, not copied into it.
 */
const ApprovalMatrixEditor = ({
  rows = [], onChange, readOnly = false, error,
  roles: roleOptions, roleKey = 'role_type',
}) => {
  const options = roleOptions
    || ROLE_TYPES.map((code) => ({ code, label: roleTypeLabel(code) }));
  const [dragIndex, setDragIndex] = useState(null);
  const [overIndex, setOverIndex] = useState(null);

  const move = (from, to) => {
    if (to < 0 || to >= rows.length || from === to) return;
    const next = [...rows];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };

  const nudge = (index, delta) => move(index, index + delta);
  const remove = (index) => onChange(rows.filter((_, i) => i !== index));
  const setRole = (index, value) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, [roleKey]: value } : row)));

  const add = (employee) => onChange([...rows, {
    assignee_id: String(employee.id),
    full_name: employee.full_name,
    designation: employee.designation || '',
    department: employee.department || '',
    // Reviewer first is the common case and the only role the spec marks as
    // comment-required, so it is the safest default to land on.
    [roleKey]: options[0]?.code || 'reviewer',
  }]);

  const onDrop = (index) => {
    if (dragIndex !== null) move(dragIndex, index);
    setDragIndex(null);
    setOverIndex(null);
  };

  // Mirrors the server's structural rules so the user sees the problem before
  // they submit. The server re-validates regardless — this is guidance, not the
  // authority.
  const lastIsApprover = rows.length > 0 && rows[rows.length - 1][roleKey] === 'approver';
  const approverNotLast = rows.some((row, i) => row[roleKey] === 'approver' && i !== rows.length - 1);
  const hint = !rows.length
    ? 'Add at least one approver. The final step must be an Approver.'
    : !lastIsApprover
      ? 'The final step must be an Approver — that is the step that approves the memo.'
      : approverNotLast
        ? 'An Approver can only be the last step. Move it to the end or change its role type.'
        : null;

  return (
    <section className="memo-matrix" aria-labelledby="matrix-heading">
      <div className="memo-matrix-head">
        <h3 id="matrix-heading"><Users size={15} aria-hidden="true" /> Approval Workflow</h3>
        {!readOnly && (
          <span className="memo-matrix-count">
            {rows.length} {rows.length === 1 ? 'step' : 'steps'}
          </span>
        )}
      </div>

      <div className="lr-table-wrap">
        <table className="lr-table memo-matrix-table">
          <thead>
            <tr>
              {!readOnly && <th scope="col" className="memo-grip-col"><span className="sr-only">Reorder</span></th>}
              <th scope="col" style={{ width: 56 }}>S.N.</th>
              <th scope="col">Employee</th>
              <th scope="col">Designation</th>
              <th scope="col">Department</th>
              <th scope="col" style={{ width: 160 }}>Role</th>
              {!readOnly && <th scope="col" style={{ width: 104 }}>Actions</th>}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={readOnly ? 5 : 7} className="memo-matrix-empty">
                  No approvers added yet. Search for an employee below to build the workflow.
                </td>
              </tr>
            )}
            {rows.map((row, index) => (
              <tr
                key={row.assignee_id}
                className={overIndex === index && dragIndex !== index ? 'memo-row-dragover' : ''}
                draggable={!readOnly}
                onDragStart={() => setDragIndex(index)}
                onDragEnd={() => { setDragIndex(null); setOverIndex(null); }}
                onDragOver={(e) => { if (!readOnly) { e.preventDefault(); setOverIndex(index); } }}
                onDrop={() => !readOnly && onDrop(index)}
              >
                {!readOnly && (
                  <td className="memo-grip-col">
                    <span className="memo-grip" aria-hidden="true" title="Drag to reorder">
                      <GripVertical size={14} />
                    </span>
                  </td>
                )}
                <td className="memo-matrix-seq">{index + 1}</td>
                <td style={{ fontWeight: 600 }}>{row.full_name}</td>
                <td>{row.designation || '—'}</td>
                <td>{row.department || '—'}</td>
                <td>
                  {readOnly ? (
                    <MemoRoleBadge role_type={row[roleKey]} />
                  ) : (
                    <select
                      value={row[roleKey]}
                      aria-label={`Role type for ${row.full_name}`}
                      onChange={(e) => setRole(index, e.target.value)}
                    >
                      {options.map((role) => (
                        <option key={role.code} value={role.code}>{role.label}</option>
                      ))}
                    </select>
                  )}
                </td>
                {!readOnly && (
                  <td>
                    <div className="memo-matrix-actions">
                      <button type="button" className="memo-icon-btn" disabled={index === 0}
                        aria-label={`Move ${row.full_name} up`} onClick={() => nudge(index, -1)}>
                        <ChevronUp size={14} />
                      </button>
                      <button type="button" className="memo-icon-btn" disabled={index === rows.length - 1}
                        aria-label={`Move ${row.full_name} down`} onClick={() => nudge(index, 1)}>
                        <ChevronDown size={14} />
                      </button>
                      <button type="button" className="memo-icon-btn is-danger"
                        aria-label={`Remove ${row.full_name}`} onClick={() => remove(index)}>
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!readOnly && (
        <>
          <div className="memo-matrix-add">
            <EmployeeSelector
              onSelect={add}
              exclude={rows.map((row) => row.assignee_id)}
            />
          </div>
          {error && <p role="alert" className="memo-matrix-error">{error}</p>}
          {!error && hint && <p className="memo-matrix-hint">{hint}</p>}
          {!error && !hint && (
            <p className="memo-matrix-hint">
              Drag a row, or use the arrows, to change the approval order.
            </p>
          )}
        </>
      )}
    </section>
  );
};

export default ApprovalMatrixEditor;
