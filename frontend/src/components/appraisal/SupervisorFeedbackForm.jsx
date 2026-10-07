import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { appraisalService } from '../../services/appraisalService';
import {
  FEEDBACK_SECTIONS, composeFeedback, parseFeedback,
} from './supervisorFeedback';

/**
 * Structured supervisor feedback (Phase APM-UX).
 *
 * Four prompts composed into the existing `supervisor_comments` field — no
 * model change, no migration. See supervisorFeedback.js for why the prompts
 * exist and why nothing written earlier is ever lost.
 *
 * Read-only for everybody but the supervisor at their own stage: the guard is
 * the server's `can_write_supervisor_review`, passed in as `canWrite`, exactly
 * as the plain textarea did.
 */
const SupervisorFeedbackForm = ({ appraisal, canWrite }) => {
  const queryClient = useQueryClient();
  const stored = parseFeedback(appraisal.supervisor_comments);
  const [draft, setDraft] = useState(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  // `null` until somebody types, so a refetch shows the server's copy — but a
  // draft, once started, wins: a background refetch that replaced half-written
  // feedback would throw away work nobody can get back.
  const values = draft ?? stored;

  const save = useMutation({
    mutationFn: () => appraisalService.updateAppraisal(appraisal.id, {
      supervisor_comments: composeFeedback(values),
    }),
    onSuccess: () => {
      setSaved(true); setDraft(null); setError('');
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
    onError: (err) => setError(
      err?.response?.data?.detail || 'That could not be saved.'),
  });

  const set = (key) => (event) => {
    setDraft({ ...values, [key]: event.target.value });
    setSaved(false);
  };

  if (!canWrite) {
    const written = FEEDBACK_SECTIONS.filter((s) => values[s.key]);
    if (!values.preamble && !written.length) return null;
    return (
      <section className="memo-dash-section" aria-labelledby="apr-sf-h">
        <div className="memo-dash-head">
          <h3 id="apr-sf-h">Manager Feedback</h3>
        </div>
        {values.preamble && (
          <p className="apr-feedback-body">{values.preamble}</p>
        )}
        {written.map((section) => (
          <article key={section.key} className="apr-feedback">
            <h4 className="apr-sub-h">{section.label}</h4>
            <p className="apr-feedback-body">{values[section.key]}</p>
          </article>
        ))}
      </section>
    );
  }

  return (
    <section className="memo-dash-section" aria-labelledby="apr-sf-h">
      <div className="memo-dash-head">
        <h3 id="apr-sf-h">Manager Feedback</h3>
      </div>
      <p className="lr-page-sub">
        This is what they will read. Every question is optional.
      </p>

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      <form onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
        {values.preamble && (
          <label className="apr-field">
            <span>Written earlier</span>
            <span className="memo-tile-hint">
              Written before this form had sections. Edit it here or move it
              into a section below — it is kept either way.
            </span>
            <textarea rows={4} value={values.preamble}
              onChange={set('preamble')} />
          </label>
        )}

        {FEEDBACK_SECTIONS.map((section) => (
          <label key={section.key} className="apr-field">
            <span>{section.label}</span>
            <span className="memo-tile-hint">{section.help}</span>
            <textarea rows={3} value={values[section.key]}
              onChange={set(section.key)} />
          </label>
        ))}

        <div className="apr-goal-actions">
          <button type="submit" className="btn btn-primary btn-sm"
            disabled={save.isPending}>Save feedback</button>
          {saved && !save.isPending && (
            <span className="memo-tile-hint" role="status">
              Saved. It reaches them when you complete your review.
            </span>
          )}
        </div>
      </form>
    </section>
  );
};

export default SupervisorFeedbackForm;
