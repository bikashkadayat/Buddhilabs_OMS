import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Megaphone, Users } from 'lucide-react';

import { circularService } from '../../services/circularService';

/**
 * Define an audience and send.
 *
 * THE PREVIEW IS THE POINT. Broadcasting is irreversible - people are told - so the
 * recipient count is shown BEFORE the send button is usable, from the same payload
 * the send will use. A count offered afterwards would be a report, not a control.
 *
 * The four audience kinds are the brief's. Department, unit and sub-unit are one
 * choice here rather than three because leaves.Department is self-nesting: a unit IS
 * a department row with a parent, and "include sub-units" covers the tree.
 */
const BroadcastPanel = ({ circular, onBroadcast, busy }) => {
  const [audience, setAudience] = useState('organisation');
  const [departmentIds, setDepartmentIds] = useState([]);
  const [groupIds, setGroupIds] = useState([]);
  const [includeChildren, setIncludeChildren] = useState(true);
  const [remarks, setRemarks] = useState('');
  const [preview, setPreview] = useState(null);

  const alreadySent = (circular.broadcasts || []).length > 0;

  const { data: options } = useQuery({
    queryKey: ['circulars', 'audiences'],
    queryFn: circularService.getAudiences,
    staleTime: 10 * 60 * 1000,
  });

  const payload = () => ({
    audience,
    department_ids: audience === 'departments' ? departmentIds : [],
    group_ids: audience === 'groups' ? groupIds : [],
    employee_ids: [],
    include_children: includeChildren,
    remarks,
  });

  const runPreview = useMutation({
    mutationFn: () => circularService.previewAudience(circular.id, payload()),
    onSuccess: setPreview,
    onError: () => setPreview(null),
  });

  // Any change to the audience invalidates the preview, so the count on screen can
  // never describe a different audience than the one the button would send to.
  const change = (fn) => (...args) => { setPreview(null); fn(...args); };

  const toggle = (list, setList) => (value) => setList(
    list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

  return (
    <div className="memo-panel" data-testid="broadcast-panel">
      {/* The panel serves two situations and must not describe the wrong one: a
          first send, and an EXTENSION to people who were missed. The copy is
          conditional because "choose who receives it" on a circular that has
          already gone out reads as though the first send had not happened. */}
      <h3 className="memo-panel-title">
        <Megaphone size={15} aria-hidden="true" />
        {alreadySent ? ' Extend the audience' : ' Broadcast'}
      </h3>
      <p className="lr-page-sub">
        {alreadySent ? (
          <>
            This circular has already gone out. Naming a further audience adds the
            people who do not yet have it and leaves everybody else untouched —
            their acknowledgement deadline does not restart.
          </>
        ) : (
          <>
            This circular is issued and official. Choose who receives it.
            Broadcasting cannot be undone — an incorrect circular is corrected by
            issuing an amended one.
          </>
        )}
      </p>

      <label className="lr-field">
        <span>Audience</span>
        <select value={audience} aria-label="Audience"
          onChange={change((e) => setAudience(e.target.value))}>
          <option value="organisation">Entire organisation</option>
          <option value="departments">Selected departments</option>
          <option value="groups">Employee groups</option>
        </select>
      </label>

      {audience === 'departments' && (
        <>
          <div className="cir-picker" role="group" aria-label="Departments">
            {(options?.departments || []).map((row) => (
              <label key={row.id} className="cir-picker-item">
                <input type="checkbox" checked={departmentIds.includes(row.id)}
                  onChange={change(toggle(departmentIds, setDepartmentIds))
                    .bind(null, row.id)} />
                <span>{row.name}</span>
              </label>
            ))}
          </div>
          <label className="lr-field lr-field-inline">
            <input type="checkbox" checked={includeChildren}
              onChange={change((e) => setIncludeChildren(e.target.checked))} />
            <span>Include sub-units</span>
          </label>
        </>
      )}

      {audience === 'groups' && (
        <div className="cir-picker" role="group" aria-label="Employee groups">
          {(options?.groups || []).map((row) => (
            <label key={row.id} className="cir-picker-item">
              <input type="checkbox" checked={groupIds.includes(row.id)}
                onChange={change(toggle(groupIds, setGroupIds)).bind(null, row.id)} />
              <span>{row.name}</span>
            </label>
          ))}
          {!(options?.groups || []).length && (
            <p className="lr-page-sub">No employee groups have been defined.</p>
          )}
        </div>
      )}

      <label className="lr-field">
        <span>Remarks (optional)</span>
        <input value={remarks} maxLength={300}
          onChange={(e) => setRemarks(e.target.value)}
          placeholder="Noted against this broadcast in the record" />
      </label>

      <div className="memo-matrix-actions">
        <button type="button" className="lr-btn"
          disabled={runPreview.isPending} onClick={() => runPreview.mutate()}>
          <Users size={14} /> {runPreview.isPending ? 'Counting…' : 'Preview audience'}
        </button>
        <button type="button" className="lr-btn lr-btn-primary"
          // Deliberately gated on a preview having been run for THIS audience.
          disabled={busy || !preview || !preview.new}
          onClick={() => onBroadcast(payload())}>
          <Megaphone size={14} />
          {busy ? ' Broadcasting…'
            : preview ? ` Send to ${preview.new}`
              : alreadySent ? ' Extend' : ' Broadcast'}
        </button>
      </div>

      {preview && (
        <p className="lr-page-sub" role="status">
          <b>{preview.total}</b> in this audience
          {preview.already_present > 0 && (
            <> · <b>{preview.already_present}</b> already hold this circular</>
          )}
          {' '}· <b>{preview.new}</b> will be added.
          {preview.new === 0 && ' Nothing to send.'}
        </p>
      )}
      {!preview && (
        <p className="lr-page-sub">
          Preview the audience to see how many people it reaches before sending.
        </p>
      )}
    </div>
  );
};

export default BroadcastPanel;
