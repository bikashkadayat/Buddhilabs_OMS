import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { appraisalService } from '../../services/appraisalService';
import {
  RETIRED_SECTIONS, SECTIONS, composeSelfAssessment, parseSelfAssessment,
} from './selfAssessment';

/**
 * The self-assessment (Phase APM-03b).
 *
 * FIVE PROMPTS, ONE DOCUMENT
 * --------------------------
 * Achievements, Challenges, Lessons Learned, Future Goals, Comments. They exist
 * because the hardest part of a self-assessment is the empty box: asked "write
 * about your year", most people write three lines about the last month. Asked
 * five specific questions, they write the thing their reviewer actually needs.
 *
 * The sections compose into one markdown document in the existing
 * `self_assessment` field — see selfAssessment.js for why, and for the rule that
 * text written before this form existed is preserved rather than dropped.
 *
 * EVERY SECTION IS OPTIONAL
 * -------------------------
 * The server requires that SOMETHING is written before the stage can be
 * submitted, and nothing more. A form that refuses to save until all five are
 * filled would mean somebody with nothing to say under "Challenges" either
 * invents a challenge or cannot record their year at all — and the invented
 * answer is the one that ends up in the meeting.
 *
 * SAVING IS SEPARATE FROM SUBMITTING
 * ----------------------------------
 * This is a document people come back to over days. Save keeps it; the
 * transition on the workflow bar hands it to the supervisor, and only that is
 * irreversible. Two buttons, because one that did both would make every
 * half-finished draft a submission.
 */
const SelfAssessmentForm = ({ appraisal, canWrite }) => {
  const queryClient = useQueryClient();
  const stored = parseSelfAssessment(appraisal.self_assessment);
  const [draft, setDraft] = useState(null);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  // `null` until somebody types, so a refetch shows the server's copy — but
  // once there IS a draft it wins, because a background refetch that replaced
  // half-written paragraphs would throw away work nobody can get back.
  const values = draft ?? stored;

  const save = useMutation({
    mutationFn: () => appraisalService.updateAppraisal(appraisal.id, {
      self_assessment: composeSelfAssessment(values),
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
    // The reading view. Sections with nothing in them are omitted rather than
    // rendered as an empty heading — a reviewer should not have to work out
    // whether "Challenges" with no text means "none" or "not written yet".
    const written = [...SECTIONS, ...RETIRED_SECTIONS]
      .filter((s) => values[s.key]);
    if (!values.preamble && !written.length) return null;
    return (
      <section className="memo-dash-section" aria-labelledby="apr-sa-h">
        <div className="memo-dash-head"><h3 id="apr-sa-h">Self Assessment</h3></div>
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
    <section className="memo-dash-section" aria-labelledby="apr-sa-h">
      <div className="memo-dash-head"><h3 id="apr-sa-h">Self Assessment</h3></div>
      <p className="lr-page-sub">
        Your own account of the year, in your words. Every question is
        optional — answer the ones that apply to you.
      </p>

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      <form onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
        {/* Only rendered when there IS earlier text. A self-assessment written
            against the old plain textarea must stay editable rather than
            becoming invisible the moment this form shipped. */}
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

        {SECTIONS.map((section) => (
          <label key={section.key} className="apr-field">
            <span>{section.label}</span>
            <span className="memo-tile-hint">{section.help}</span>
            <textarea rows={4} value={values[section.key]}
              onChange={set(section.key)} />
          </label>
        ))}

        {/* A section this form no longer offers, but which this record already
            has. Editable, because the alternative is somebody's words sitting
            on their appraisal with no way to correct them. Not offered when
            empty, so a new self-assessment sees only the four prompts. */}
        {RETIRED_SECTIONS.filter((section) => values[section.key])
          .map((section) => (
            <label key={section.key} className="apr-field">
              <span>{section.label}</span>
              <span className="memo-tile-hint">
                Written under a heading this form no longer offers. It is kept
                on your record and you can still edit it.
              </span>
              <textarea rows={3} value={values[section.key]}
                onChange={set(section.key)} />
            </label>
          ))}

        <div className="apr-goal-actions">
          <button type="submit" className="btn btn-primary btn-sm"
            disabled={save.isPending}>Save self assessment</button>
          {saved && !save.isPending && (
            <span className="memo-tile-hint" role="status">
              Saved. It is not with your supervisor until you submit it.
            </span>
          )}
        </div>
      </form>
    </section>
  );
};

export default SelfAssessmentForm;
