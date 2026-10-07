import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';

/**
 * The development plan (Phase APM-03b).
 *
 * A PLAN, NOT A LIST OF WEAKNESSES
 * --------------------------------
 * `action` is required and the server refuses anything under five characters,
 * because a development plan that lists areas with no actions is an assessment
 * wearing a plan's clothes. This form puts Action beside Area for the same
 * reason — filling in the second without the first should feel unfinished.
 *
 * `support_required` is here because most development actions fail for want of
 * something the organisation has to provide, and a plan with no place to say so
 * quietly makes every failure the employee's.
 */
const BLANK = { area: '', action: '', support_required: '', target_date: '' };

const DevelopmentPlanPanel = ({ appraisal, canManage }) => {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState(BLANK);
  const [error, setError] = useState('');

  const rows = appraisal.development_plans || [];

  const add = useMutation({
    mutationFn: (payload) =>
      appraisalService.addDevelopmentAction(appraisal.id, payload),
    onSuccess: () => {
      setDraft(BLANK); setAdding(false); setError('');
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
    onError: (err) => setError(
      err?.response?.data?.detail
      || Object.values(err?.response?.data || {}).flat().join(' ')
      || 'That action could not be saved.',
    ),
  });

  return (
    <section className="memo-dash-section" aria-labelledby="apr-dev-h">
      <div className="memo-dash-head">
        <h3 id="apr-dev-h">Growth Plan</h3>
      </div>

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      {rows.length === 0 ? (
        <p className="lr-page-sub">Nothing agreed yet.</p>
      ) : (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">Your growth plan</caption>
            <thead>
              <tr>
                <th scope="col">Skill to improve</th>
                <th scope="col">What you will do</th>
                <th scope="col">Support</th>
                <th scope="col">Target date</th>
                <th scope="col">Status</th>
                <th scope="col">Owner</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <th scope="row">{row.area}</th>
                  <td>{row.action}</td>
                  <td>{row.support_required || '—'}</td>
                  <td>{row.target_date || '—'}</td>
                  <td>{row.status_label || row.status}</td>
                  <td>{row.accountable_name || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {canManage && !adding && (
        <button type="button" className="btn btn-ghost btn-sm"
          onClick={() => setAdding(true)}>
          <Plus size={14} aria-hidden="true" /> Add something to work on
        </button>
      )}

      {canManage && adding && (
        <form className="apr-goal-form"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate({ ...draft, target_date: draft.target_date || null });
          }}>
          <div className="apr-goal-grid">
            {/* autoFocus, because pressing this form's trigger REMOVES the
                trigger — a keyboard user would otherwise be left with focus on
                a detached button and land back at the top of the document on
                their next Tab. */}
            <label className="apr-field">
              <span>Skill to improve</span>
              <input value={draft.area} required maxLength={150} autoFocus
                onChange={(e) => setDraft({ ...draft, area: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Target date</span>
              <input type="date" value={draft.target_date}
                onChange={(e) => setDraft({ ...draft, target_date: e.target.value })} />
            </label>
            <label className="apr-field apr-span">
              <span>What you will do</span>
              <textarea rows={2} value={draft.action} required minLength={5}
                onChange={(e) => setDraft({ ...draft, action: e.target.value })} />
            </label>
            <label className="apr-field apr-span">
              <span>How we can help</span>
              <textarea rows={2} value={draft.support_required}
                onChange={(e) => setDraft({ ...draft, support_required: e.target.value })} />
            </label>
          </div>
          <div className="apr-goal-actions">
            <button type="submit" className="btn btn-primary btn-sm"
              disabled={add.isPending}>Save</button>
            <button type="button" className="btn btn-ghost btn-sm"
              onClick={() => { setAdding(false); setDraft(BLANK); }}>Cancel</button>
          </div>
        </form>
      )}
    </section>
  );
};

export default DevelopmentPlanPanel;
