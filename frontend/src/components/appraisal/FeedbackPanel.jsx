import React from 'react';

/**
 * Feedback (Phase APM-03b).
 *
 * WHY THIS IS ITS OWN PANEL
 * -------------------------
 * The APM-00 freeze recorded a gap: an employee could only read their
 * supervisor's and the committee's words by scrolling the whole record, mixed
 * in with the controls for editing it. Feedback is the part of an appraisal
 * people actually come back to read, so it is gathered in one place, in the
 * order it was written, each block named for who wrote it.
 *
 * Nothing here is editable — this is the reading view. The people who may write
 * each block do so at their own stage, guarded per field on the server.
 */
const Block = ({ title, author, body }) => {
  if (!body) return null;
  return (
    <article className="apr-feedback">
      <h4 className="apr-sub-h">{title}</h4>
      {author && <p className="memo-tile-hint">{author}</p>}
      <p className="apr-feedback-body">{body}</p>
    </article>
  );
};

const FeedbackPanel = ({ appraisal }) => {
  const has = appraisal.self_assessment || appraisal.supervisor_comments
    || appraisal.committee_comments || appraisal.final_summary;

  return (
    <section className="memo-dash-section" aria-labelledby="apr-fb-h">
      <div className="memo-dash-head"><h3 id="apr-fb-h">Feedback</h3></div>
      {!has && (
        <p className="lr-page-sub">
          Nothing has been written yet. Feedback appears here as each person adds
          theirs.
        </p>
      )}
      <Block title="What I wrote" author={appraisal.employee_name}
        body={appraisal.self_assessment} />
      <Block title="My manager's feedback" author={appraisal.supervisor_name}
        body={appraisal.supervisor_comments} />
      <Block title="Also reviewed by"
        author={(appraisal.committee_detail || [])
          .map((m) => m.full_name).join(', ')}
        body={appraisal.committee_comments} />
      <Block title="Final feedback" author={appraisal.supervisor_name}
        body={appraisal.final_summary} />
    </section>
  );
};

export default FeedbackPanel;
