import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { appraisalService } from '../../services/appraisalService';

/**
 * One block of the written record (Phase APM-03b).
 *
 * WHY EACH BLOCK IS ITS OWN FORM
 * ------------------------------
 * The server guards these field by field, not request by request: a supervisor
 * may legitimately PATCH the appraisal at their own stage and must still not be
 * able to reach into the employee's self-assessment in the same call. One form
 * covering all four would send all four, so the shape of the UI would be
 * arguing with the shape of the permission. Four forms, four fields, four
 * decisions.
 *
 * The read-only rendering is not a disabled textarea. A greyed-out box that
 * looks like an input invites people to try to type in it and tells them
 * nothing about why they cannot; prose that is not yours to edit should look
 * like prose.
 */
const WrittenRecord = ({ appraisal, field, label, help, canWrite }) => {
  const queryClient = useQueryClient();
  // `null` means "nothing typed yet", so the server's value shows through every
  // refetch. Once somebody types, their draft wins until they save — which is
  // the behaviour that matters here: this is a textarea holding a paragraph
  // about somebody's year, and a background refetch that replaced it with the
  // server copy mid-sentence would throw away work that cannot be got back.
  const [draft, setDraft] = useState(null);
  const [saved, setSaved] = useState(false);
  const value = draft ?? (appraisal[field] || '');

  const save = useMutation({
    mutationFn: () =>
      appraisalService.updateAppraisal(appraisal.id, { [field]: value }),
    onSuccess: () => {
      setSaved(true);
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
  });

  const id = `apr-w-${field}`;

  if (!canWrite) {
    if (!appraisal[field]) return null;
    return (
      <section className="memo-dash-section" aria-labelledby={`${id}-h`}>
        <div className="memo-dash-head"><h3 id={`${id}-h`}>{label}</h3></div>
        <p className="apr-feedback-body">{appraisal[field]}</p>
      </section>
    );
  }

  return (
    <section className="memo-dash-section" aria-labelledby={`${id}-h`}>
      <div className="memo-dash-head"><h3 id={`${id}-h`}>{label}</h3></div>
      {help && <p className="lr-page-sub">{help}</p>}
      <form onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
        <label className="apr-field">
          <span className="sr-only">{label}</span>
          <textarea id={id} rows={6} value={value}
            onChange={(e) => { setDraft(e.target.value); setSaved(false); }} />
        </label>
        <div className="apr-goal-actions">
          <button type="submit" className="btn btn-primary btn-sm"
            disabled={save.isPending}>Save {label.toLowerCase()}</button>
          {saved && !save.isPending && (
            <span className="memo-tile-hint" role="status">Saved.</span>
          )}
        </div>
      </form>
    </section>
  );
};

export default WrittenRecord;
