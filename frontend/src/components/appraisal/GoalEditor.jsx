import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Plus, Trash2 } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { goalStatusLabel } from './appraisalLabels';

/**
 * The goal editor (Phase APM-03b).
 *
 * THE 100% RULE, AND WHERE IT IS ACTUALLY ENFORCED
 * ------------------------------------------------
 * The specification says total weight must equal 100%. The SERVER enforces
 * that: `workflow.agree_goals` refuses to advance an objective set that does not
 * total 100, and it is the only thing that can. What this component adds is the
 * running total and a warning, so somebody finds out while they are still
 * typing rather than at the moment their supervisor tries to agree the set.
 *
 * It deliberately does NOT block adding a goal that takes the total past 100.
 * Objectives are written in whatever order they come to mind, and a form that
 * refuses the fourth goal until the first three have been re-weighted forces
 * people to do arithmetic before they are allowed to finish a thought. The
 * total is shown, the gap is named, and the transition is the gate.
 *
 * ACHIEVEMENT IS THE EMPLOYEE'S COLUMN
 * ------------------------------------
 * `target` is what was agreed; `achievement` is what the person says happened.
 * They are separate fields because overwriting the target with the outcome is
 * how a year's objectives quietly become whatever was delivered.
 *
 * `canEdit` comes from the SERVER's `capabilities` block, never from reading
 * the caller's role here — the same rule every screen in this module follows.
 */
const BLANK = {
  objective: '', description: '', weight: 0, target: '', achievement: '',
  due_date: '',
};

const GoalRow = ({ goal, appraisalId, canEdit, onError, evidence = [] }) => {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(null);
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['appraisal', appraisalId] });

  const save = useMutation({
    mutationFn: (payload) =>
      appraisalService.updateGoal(appraisalId, goal.id, payload),
    onSuccess: () => { setDraft(null); invalidate(); },
    onError,
  });
  const remove = useMutation({
    mutationFn: () => appraisalService.deleteGoal(appraisalId, goal.id),
    onSuccess: invalidate,
    onError,
  });

  const editing = draft !== null;
  const value = editing ? draft : goal;
  const set = (key) => (event) =>
    setDraft({ ...value, [key]: event.target.value });

  return (
    <li className="apr-goal">
      <div className="apr-goal-head">
        {editing ? (
          <label className="apr-field apr-grow">
            <span>Goal</span>
            <input value={value.objective} onChange={set('objective')}
              required maxLength={255} />
          </label>
        ) : (
          <h4 className="apr-goal-title">{goal.objective}</h4>
        )}
        {/* Visually-hidden text rather than an aria-label: a bare "60%" beside
            an objective is ambiguous to a screen reader, but an aria-label on
            the badge would also make it answer to "weight" — and the weight
            INPUT in the edit form is the thing that should. */}
        <span className="apr-goal-meta">
          {/* Draft / Approved / Locked. The whole reason the Goal Approval
              stage exists is so somebody can tell an objective still under
              discussion from one they are being held to — which only works if
              the record says which it is. */}
          <span className={`apr-goal-status is-${goal.status || 'draft'}`}>
            {goal.status_label || goalStatusLabel(goal.status)}
          </span>
          {/* "40% of your year" rather than "Weight: 40". The number is the
              same and the governance rule behind it is untouched; what changes
              is that a person reads it as how their year is divided rather than
              as a field they have to interpret. */}
          <span className="apr-weight">
            {goal.weight}% <span className="apr-weight-of">of your year</span>
          </span>
        </span>
      </div>

      {editing ? (
        <div className="apr-goal-grid">
          <label className="apr-field">
            <span>Share of your year (%)</span>
            <input type="number" min="0" max="100" value={value.weight}
              onChange={set('weight')} />
          </label>
          <label className="apr-field">
            <span>Due date</span>
            <input type="date" value={value.due_date || ''}
              onChange={set('due_date')} />
          </label>
          <label className="apr-field apr-span">
            <span>What success looks like</span>
            <textarea rows={2} value={value.target || ''}
              onChange={set('target')} />
          </label>
          <label className="apr-field apr-span">
            <span>What happened</span>
            <textarea rows={2} value={value.achievement || ''}
              onChange={set('achievement')} />
          </label>
          <label className="apr-field">
            <span>Progress (%)</span>
            <input type="number" min="0" max="100"
              value={value.progress_percent ?? 0}
              onChange={set('progress_percent')} />
          </label>
        </div>
      ) : (
        <dl className="apr-goal-body">
          {goal.target && (<><dt>Success looks like</dt><dd>{goal.target}</dd></>)}
          {goal.achievement && (
            <><dt>What happened</dt><dd>{goal.achievement}</dd></>)}
          {goal.due_date && (<><dt>Due</dt><dd>{goal.due_date}</dd></>)}
          {/* The evidence CITED AGAINST THIS OBJECTIVE, which is the sixth
              field the specification lists for a goal. Frozen figures with the
              date they were read, so a number quoted months later can be
              checked against the day it was true. Attaching them is done on the
              evidence panel — this is where they are read. */}
          {evidence.length > 0 && (
            <>
              <dt>Evidence</dt>
              <dd>
                {evidence.map((row) => (
                  <div key={row.id} className="apr-cited-note">
                    {(row.metrics || []).map(
                      (m) => `${m.label}: ${m.value ?? '—'}`).join(' · ')
                      || row.source}
                    {' — cited '}
                    {new Date(row.captured_at).toLocaleDateString()}
                    {row.note ? ` · “${row.note}”` : ''}
                  </div>
                ))}
              </dd>
            </>
          )}
          <dt>Progress</dt>
          <dd>
            {/* The bar is a picture of the number beside it, never instead of
                it: a bar alone cannot be read by a screen reader or a printer. */}
            <span className="apr-bar" aria-hidden="true">
              <span className="apr-bar-fill"
                style={{ width: `${goal.progress_percent || 0}%` }} />
            </span>
            {goal.progress_percent || 0}%
          </dd>
        </dl>
      )}

      {canEdit && (
        <div className="apr-goal-actions">
          {editing ? (
            <>
              <button type="button" className="btn btn-primary btn-xs"
                disabled={save.isPending}
                onClick={() => save.mutate({
                  ...draft,
                  weight: Number(draft.weight) || 0,
                  progress_percent: Number(draft.progress_percent) || 0,
                  due_date: draft.due_date || null,
                })}>
                Save goal
              </button>
              <button type="button" className="btn btn-ghost btn-xs"
                onClick={() => setDraft(null)}>Cancel</button>
            </>
          ) : (
            <>
              <button type="button" className="btn btn-ghost btn-xs"
                onClick={() => setDraft({ ...goal })}>
                {/* Named, not just "Edit": a page can hold six of these. */}
                Edit “{goal.objective}”
              </button>
              <button type="button" className="btn btn-ghost btn-xs apr-danger"
                disabled={remove.isPending}
                onClick={() => remove.mutate()}>
                <Trash2 size={13} aria-hidden="true" />
                Remove “{goal.objective}”
              </button>
            </>
          )}
        </div>
      )}
    </li>
  );
};

const GoalEditor = ({ appraisal, canEdit }) => {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState(BLANK);
  const [error, setError] = useState('');

  const goals = appraisal.goals || [];
  // Read from the SERVER's total, not summed here, so the number on screen is
  // the number the transition will check.
  const total = appraisal.goal_weight_total ?? 0;
  const gap = 100 - total;

  const onError = (err) => setError(
    err?.response?.data?.detail
    || Object.values(err?.response?.data || {}).flat().join(' ')
    || 'That could not be saved.',
  );

  const add = useMutation({
    mutationFn: (payload) => appraisalService.addGoal(appraisal.id, payload),
    onSuccess: () => {
      setDraft(BLANK); setAdding(false); setError('');
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
    onError,
  });

  return (
    <section className="memo-dash-section" aria-labelledby="apr-goals-h">
      <div className="memo-dash-head">
        <h3 id="apr-goals-h">Goals</h3>
        <span className={`apr-total ${total === 100 ? 'is-ok' : 'is-off'}`}
          role="status">
          {total === 100
            ? <><CheckCircle2 size={13} aria-hidden="true" /> Your year adds up to 100%</>
            : (
              <>
                <AlertTriangle size={13} aria-hidden="true" />
                Your goals add up to {total}% —{' '}
                {gap > 0 ? `add ${gap}% more`
                  : `that is ${Math.abs(gap)}% too much`}
              </>
            )}
        </span>
      </div>

      {/* Said in words as well as shown in the badge. The badge is a colour and
          a number; this is the sentence somebody can act on. */}
      {total !== 100 && (
        <div className="task-callout is-warn" role="note">
          Your goals need to add up to 100% before you can send them to your manager.
        </div>
      )}

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      {goals.length === 0 && (
        <p className="lr-page-sub">No goals have been set yet.</p>
      )}

      <ul className="apr-goal-list">
        {goals.map((goal) => (
          <GoalRow key={goal.id} goal={goal} appraisalId={appraisal.id}
            canEdit={canEdit} onError={onError}
            evidence={(appraisal.evidence_references || [])
              .filter((row) => row.goal === goal.id)} />
        ))}
      </ul>

      {canEdit && !adding && (
        <button type="button" className="btn btn-ghost btn-sm"
          onClick={() => setAdding(true)}>
          <Plus size={14} aria-hidden="true" /> Add goal
        </button>
      )}

      {canEdit && adding && (
        <form className="apr-goal-form"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate({
              ...draft,
              weight: Number(draft.weight) || 0,
              due_date: draft.due_date || null,
            });
          }}>
          <div className="apr-goal-grid">
            {/* autoFocus, because pressing this form's trigger REMOVES the
                trigger — a keyboard user would otherwise be left with focus on
                a detached button and land back at the top of the document on
                their next Tab. */}
            <label className="apr-field apr-span">
              <span>Goal</span>
              <input value={draft.objective} required maxLength={255} autoFocus
                onChange={(e) => setDraft({ ...draft, objective: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Share of your year (%)</span>
              <input type="number" min="0" max="100" value={draft.weight}
                onChange={(e) => setDraft({ ...draft, weight: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Due date</span>
              <input type="date" value={draft.due_date}
                onChange={(e) => setDraft({ ...draft, due_date: e.target.value })} />
            </label>
            <label className="apr-field apr-span">
              <span>What success looks like</span>
              <textarea rows={2} value={draft.target}
                onChange={(e) => setDraft({ ...draft, target: e.target.value })} />
            </label>
          </div>
          <div className="apr-goal-actions">
            <button type="submit" className="btn btn-primary btn-sm"
              disabled={add.isPending}>Save goal</button>
            <button type="button" className="btn btn-ghost btn-sm"
              onClick={() => { setAdding(false); setDraft(BLANK); }}>
              Cancel
            </button>
          </div>
        </form>
      )}
    </section>
  );
};

export default GoalEditor;
