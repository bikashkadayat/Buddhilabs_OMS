import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import PersonPicker from './PersonPicker';
import {
  PRIORITIES, TRAINING_KINDS, TRAINING_STATUSES, trainingKindLabel,
} from './appraisalLabels';

/**
 * The training plan (Phase APM-03b).
 *
 * FOUR KINDS, ONE MODEL
 * ---------------------
 * Training, Certification, Mentorship and On-the-job are four different
 * requests with four different owners, and only the first two need a budget
 * line. Before `TrainingPlan.kind` existed they were all "training", so a
 * mentoring pairing could only be expressed by writing the word in the title —
 * and HR's list of training needs silently included things nobody had to fund.
 * The Type column is the whole reason this panel differs from a plain table.
 *
 * THE MENTOR FIELD APPEARS ONLY FOR A MENTORSHIP
 * ----------------------------------------------
 * The server refuses a mentor on any other kind, so offering the field there
 * would be offering a control that produces a 400. It is revealed by the Type
 * selector rather than always shown and sometimes ignored.
 *
 * WHO DECIDES
 * -----------
 * A supervisor RAISES a need; HR APPROVES, SCHEDULES or DECLINES it — training
 * costs money and a calendar slot, which a supervisor cannot commit on the
 * organisation's behalf. The decision carries a name and a note, because a
 * declined request that nobody owns is how training needs quietly disappear.
 */
const BLANK = {
  title: '', kind: 'training', justification: '', priority: 'medium',
  target_period: '', mentor: '',
};

const Decision = ({ appraisal, row }) => {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState(row.status);
  const [note, setNote] = useState('');

  const decide = useMutation({
    mutationFn: (payload) =>
      appraisalService.decideTraining(appraisal.id, row.id, payload),
    onSuccess: () => {
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
  });

  if (!open) {
    return (
      <button type="button" className="btn btn-ghost btn-xs"
        aria-expanded={false} onClick={() => setOpen(true)}>
        Decide on “{row.title}”
      </button>
    );
  }
  return (
    <form className="apr-rate-form"
      onSubmit={(event) => {
        event.preventDefault();
        decide.mutate({ status, decision_note: note });
      }}>
      <label className="apr-field">
        <span>Decision on “{row.title}”</span>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          {TRAINING_STATUSES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </label>
      <label className="apr-field apr-grow">
        <span>Note</span>
        <input value={note} maxLength={2000}
          onChange={(e) => setNote(e.target.value)} />
      </label>
      <button type="submit" className="btn btn-primary btn-xs"
        disabled={decide.isPending}>Record decision</button>
      <button type="button" className="btn btn-ghost btn-xs"
        onClick={() => setOpen(false)}>Cancel</button>
    </form>
  );
};

const TrainingPlanPanel = ({ appraisal, canManage, canDecide }) => {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState(BLANK);
  const [mentorName, setMentorName] = useState('');
  const [error, setError] = useState('');

  const rows = appraisal.training_plans || [];

  const add = useMutation({
    mutationFn: (payload) =>
      appraisalService.addTrainingNeed(appraisal.id, payload),
    onSuccess: () => {
      setDraft(BLANK); setMentorName(''); setAdding(false); setError('');
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
    onError: (err) => setError(
      err?.response?.data?.mentor
      || err?.response?.data?.detail
      || Object.values(err?.response?.data || {}).flat().join(' ')
      || 'That training need could not be saved.',
    ),
  });

  return (
    <section className="memo-dash-section" aria-labelledby="apr-tra-h">
      <div className="memo-dash-head"><h3 id="apr-tra-h">Training Plan</h3></div>

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      {rows.length === 0 ? (
        <p className="lr-page-sub">No training needs recorded yet.</p>
      ) : (
        <div className="lr-table-wrap">
          <table className="lr-table">
            <caption className="sr-only">Training and development needs</caption>
            <thead>
              <tr>
                <th scope="col">Need</th>
                <th scope="col">Type</th>
                <th scope="col">Priority</th>
                <th scope="col">Wanted</th>
                <th scope="col">Status</th>
                {/* The specification calls this "Assigned By". It is the person
                    who APPROVED, scheduled or declined the request — the only
                    name attached to a training line, and the one somebody
                    chases. */}
                <th scope="col">Decided By</th>
                {canDecide && <th scope="col">Record</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <th scope="row">
                    {row.title}
                    {row.justification && (
                      <div className="memo-tile-hint">{row.justification}</div>
                    )}
                  </th>
                  <td>
                    {row.kind_label || trainingKindLabel(row.kind)}
                    {row.mentor_name && (
                      <div className="memo-tile-hint">
                        Mentor: {row.mentor_name}
                      </div>
                    )}
                  </td>
                  <td>{row.priority_label || row.priority}</td>
                  <td>{row.target_period || '—'}</td>
                  <td>{row.status_label || row.status}</td>
                  <td>
                    {row.decided_by_name || '—'}
                    {row.decision_note && (
                      <div className="memo-tile-hint">{row.decision_note}</div>
                    )}
                  </td>
                  {canDecide && (
                    <td><Decision appraisal={appraisal} row={row} /></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {canManage && !adding && (
        <button type="button" className="btn btn-ghost btn-sm"
          onClick={() => setAdding(true)}>
          <Plus size={14} aria-hidden="true" /> Add training need
        </button>
      )}

      {canManage && adding && (
        <form className="apr-goal-form"
          onSubmit={(event) => {
            event.preventDefault();
            add.mutate({
              ...draft,
              // Sent only when it means something. The server refuses a mentor
              // on anything but a mentorship, and an empty string is not null.
              mentor: draft.kind === 'mentorship' && draft.mentor
                ? draft.mentor : null,
            });
          }}>
          <div className="apr-goal-grid">
            {/* autoFocus, because pressing this form's trigger REMOVES the
                trigger — a keyboard user would otherwise be left with focus on
                a detached button and land back at the top of the document on
                their next Tab. */}
            <label className="apr-field apr-span">
              <span>What is needed</span>
              <input value={draft.title} required maxLength={200} autoFocus
                onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            </label>
            <label className="apr-field">
              <span>Type</span>
              <select value={draft.kind}
                onChange={(e) => setDraft({ ...draft, kind: e.target.value })}>
                {TRAINING_KINDS.map((k) => (
                  <option key={k.value} value={k.value}>{k.label}</option>
                ))}
              </select>
            </label>
            <label className="apr-field">
              <span>Priority</span>
              <select value={draft.priority}
                onChange={(e) => setDraft({ ...draft, priority: e.target.value })}>
                {PRIORITIES.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </label>
            <label className="apr-field">
              <span>When it is wanted</span>
              <input value={draft.target_period} maxLength={100}
                placeholder="e.g. Q2 2083"
                onChange={(e) => setDraft({ ...draft, target_period: e.target.value })} />
            </label>
            {/* Revealed by the Type selector rather than always shown: the
                server refuses a mentor on any other kind, so offering the
                field there would be offering a control that 400s.

                A search box rather than a dropdown of everybody: the directory
                behind it is gated at two characters, capped and throttled
                precisely so it cannot be used to walk the roster, and a
                pre-loaded list would defeat all three protections at once. It
                is also optional — a pairing with no named mentor is the state a
                mentoring scheme is in for most of its life, and requiring one
                would mean the need could not be recorded until somebody had
                already volunteered. */}
            {draft.kind === 'mentorship' && (
              <div className="apr-span">
                <PersonPicker id="apr-mentor" label="Mentor (optional)"
                  help="Leave blank if nobody has been identified yet."
                  value={draft.mentor} valueName={mentorName}
                  onChange={(person) => {
                    setDraft({ ...draft, mentor: person.id });
                    setMentorName(person.full_name);
                  }} />
              </div>
            )}
            <label className="apr-field apr-span">
              <span>Justification</span>
              <textarea rows={2} value={draft.justification}
                onChange={(e) => setDraft({ ...draft, justification: e.target.value })} />
            </label>
          </div>
          <div className="apr-goal-actions">
            <button type="submit" className="btn btn-primary btn-sm"
              disabled={add.isPending}>Save training need</button>
            <button type="button" className="btn btn-ghost btn-sm"
              onClick={() => {
                setAdding(false); setDraft(BLANK); setMentorName('');
              }}>Cancel</button>
          </div>
        </form>
      )}
    </section>
  );
};

export default TrainingPlanPanel;
