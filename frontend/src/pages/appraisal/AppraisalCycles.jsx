import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';

/**
 * Appraisal cycles (Phase APM-03b) — HR's calendar for a round.
 *
 * A CYCLE OWNS THE CALENDAR, NOT THE OUTCOME
 * ------------------------------------------
 * It says when goal setting opens and when the round closes; every appraisal
 * inside it moves through its own workflow independently. The three deadlines
 * are ADVISORY: the workflow does not refuse a late self-assessment, because a
 * system that locks somebody out of their own appraisal for being three days
 * late creates an HR problem rather than solving one. They drive reminders and
 * the "overdue" columns, and this form says so rather than leaving people to
 * discover it.
 *
 * CLOSED, NEVER DELETED
 * ---------------------
 * A cycle holding appraisals cannot be deleted — the server refuses it — and
 * this page offers Close rather than Delete for exactly one reason: a closed
 * appraisal is a record somebody may have to produce years later. Delete is
 * offered only for an empty cycle, which is the one raised by mistake.
 */
const BLANK = {
  name: '', description: '', period_start: '', period_end: '',
  goal_setting_deadline: '', self_assessment_deadline: '', review_deadline: '',
};

const AppraisalCycles = () => {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState(BLANK);
  const [error, setError] = useState('');

  const { data, isLoading, isError, error: loadError, refetch } = useQuery({
    queryKey: ['appraisal', 'cycles'],
    queryFn: () => appraisalService.getCycles(),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['appraisal', 'cycles'] });
  const onError = (err) => setError(
    err?.response?.data?.detail
    || Object.values(err?.response?.data || {}).flat().join(' ')
    || 'That could not be saved.',
  );

  const create = useMutation({
    mutationFn: (payload) => appraisalService.createCycle(payload),
    onSuccess: () => {
      setDraft(BLANK); setAdding(false); setError(''); invalidate();
    },
    onError,
  });
  const activate = useMutation({
    mutationFn: (id) => appraisalService.activateCycle(id),
    onSuccess: invalidate, onError,
  });
  const close = useMutation({
    mutationFn: (id) => appraisalService.closeCycle(id),
    onSuccess: invalidate, onError,
  });

  if (isLoading) return <div className="page"><Skeleton rows={3} /></div>;
  if (isError) {
    return (
      <div className="page">
        <ErrorState error={loadError} onRetry={refetch} />
      </div>
    );
  }

  const rows = data?.results || data || [];

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">Appraisal Cycles</h1>
          <p className="lr-page-sub">
            Deadlines here are advisory: they drive reminders, and never lock
            anybody out of their own appraisal.
          </p>
        </div>
        {!adding && (
          <button type="button" className="btn btn-primary btn-sm"
            onClick={() => setAdding(true)}>
            <Plus size={14} aria-hidden="true" /> New cycle
          </button>
        )}
      </div>

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      {adding && (
        <form className="apr-goal-form"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate({
              ...draft,
              goal_setting_deadline: draft.goal_setting_deadline || null,
              self_assessment_deadline: draft.self_assessment_deadline || null,
              review_deadline: draft.review_deadline || null,
            });
          }}>
          <div className="apr-goal-grid">
            <label className="apr-field apr-span">
              <span>Cycle name</span>
              <input value={draft.name} required maxLength={150}
                placeholder="FY 2083/84 Annual"
                onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Period start</span>
              <input type="date" required value={draft.period_start}
                onChange={(e) => setDraft({ ...draft, period_start: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Period end</span>
              <input type="date" required value={draft.period_end}
                onChange={(e) => setDraft({ ...draft, period_end: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Goal setting by (advisory)</span>
              <input type="date" value={draft.goal_setting_deadline}
                onChange={(e) => setDraft({ ...draft, goal_setting_deadline: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Self assessment by (advisory)</span>
              <input type="date" value={draft.self_assessment_deadline}
                onChange={(e) => setDraft({ ...draft, self_assessment_deadline: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Reviews by (advisory)</span>
              <input type="date" value={draft.review_deadline}
                onChange={(e) => setDraft({ ...draft, review_deadline: e.target.value })} />
            </label>
            <label className="apr-field apr-span">
              <span>Description</span>
              <textarea rows={2} value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            </label>
          </div>
          <div className="apr-goal-actions">
            <button type="submit" className="btn btn-primary btn-sm"
              disabled={create.isPending}>Save cycle</button>
            <button type="button" className="btn btn-ghost btn-sm"
              onClick={() => { setAdding(false); setDraft(BLANK); }}>Cancel</button>
          </div>
        </form>
      )}

      {rows.length === 0 ? (
        <p className="lr-page-sub">No cycles yet.</p>
      ) : (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">Appraisal cycles, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Cycle</th>
                <th scope="col">Period</th>
                <th scope="col">Status</th>
                <th scope="col">Appraisals</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <th scope="row">
                    {row.name}
                    {row.description && (
                      <div className="memo-tile-hint">{row.description}</div>
                    )}
                  </th>
                  <td>{row.period_start} — {row.period_end}</td>
                  <td>{row.status_label || row.status}</td>
                  <td>{row.appraisal_count ?? 0}</td>
                  <td>
                    {row.status !== 'active' && row.status !== 'closed' && (
                      <button type="button" className="btn btn-ghost btn-xs"
                        disabled={activate.isPending}
                        onClick={() => activate.mutate(row.id)}>
                        Activate {row.name}
                      </button>
                    )}
                    {row.status === 'active' && (
                      <button type="button" className="btn btn-ghost btn-xs"
                        disabled={close.isPending}
                        onClick={() => close.mutate(row.id)}>
                        Close {row.name}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

export default AppraisalCycles;
