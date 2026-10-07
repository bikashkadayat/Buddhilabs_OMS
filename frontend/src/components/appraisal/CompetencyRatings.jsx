import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { appraisalService } from '../../services/appraisalService';
import { LEVELS, levelLabel } from './appraisalLabels';

/**
 * Competency ratings (Phase APM-03b).
 *
 * WORDS, NEVER NUMBERS, AND NEVER AN AVERAGE
 * ------------------------------------------
 * Levels are stored as words on the server for a reason: as integers, somebody
 * averages them within a month and an "overall competency score of 3.4" is
 * exactly the composite this module is forbidden to produce. This component
 * keeps that promise on the client — the select renders words, and there is no
 * summary row, no count by level, and no colour ramp running red to green,
 * which is a score with the digits filed off.
 *
 * SELF AND SUPERVISOR SIT SIDE BY SIDE
 * ------------------------------------
 * The whole value of a competency review is in the DIFFERENCE between how
 * somebody sees their own work and how their supervisor does. Rendered as
 * columns so the disagreements are visible; deliberately not reconciled into a
 * single "agreed" level, because agreeing them is the conversation, not the
 * form's job.
 *
 * EVERY RATING CARRIES ITS COMMENT
 * --------------------------------
 * The server requires ten characters of reasoning. A level with no reasoning is
 * a number in disguise, so the comment is shown wherever the level is — never
 * behind a tooltip or a details toggle.
 */
const CompetencyRatings = ({ appraisal, canRate, raterRole }) => {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(null);
  const [level, setLevel] = useState('');
  const [comment, setComment] = useState('');
  const [error, setError] = useState('');

  const { data: competencies = [] } = useQuery({
    queryKey: ['competencies'],
    queryFn: appraisalService.getCompetencies,
    staleTime: 300_000,
  });

  const rate = useMutation({
    mutationFn: (payload) => appraisalService.rate(appraisal.id, payload),
    onSuccess: () => {
      setOpen(null); setLevel(''); setComment(''); setError('');
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
    onError: (err) => setError(
      err?.response?.data?.detail
      || Object.values(err?.response?.data || {}).flat().join(' ')
      || 'That rating could not be saved.',
    ),
  });

  // Falls back to whatever has already been rated, so the panel still renders
  // if the competency list will not load — an appraisal that shows nothing is
  // worse than one that shows what it has.
  //
  // DEDUPLICATED by competency: the three rater roles each write their own row,
  // so a straight map over the ratings gives one TABLE row per RATING and shows
  // the same competency two or three times, once per person who rated it.
  const fallback = [...new Map((appraisal.competency_ratings || []).map(
    (r) => [r.competency, { id: r.competency, name: r.competency_name }])
  ).values()];
  const rows = competencies.length ? competencies : fallback;

  const ratingFor = (competencyId, role) =>
    (appraisal.competency_ratings || []).find(
      (r) => r.competency === competencyId && r.rated_by_role === role);

  const Cell = ({ rating }) => (rating ? (
    <>
      <b>{rating.level_label || levelLabel(rating.level)}</b>
      <div className="apr-rating-comment">{rating.comment}</div>
      <div className="memo-tile-hint">{rating.rated_by_name}</div>
    </>
  ) : <span className="memo-tile-hint">Not rated</span>);

  return (
    <section className="memo-dash-section" aria-labelledby="apr-comp-h">
      <div className="memo-dash-head"><h3 id="apr-comp-h">Competencies</h3></div>
      <p className="lr-page-sub">
        Levels are recorded in words and each carries its reasoning. They are
        never averaged, totalled or converted to a score.
      </p>

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      <div className="lr-table-wrap">
        <table className="lr-table">
          <caption className="sr-only">
            Competency levels recorded by the employee and by their reviewers
          </caption>
          <thead>
            <tr>
              <th scope="col">Competency</th>
              <th scope="col">Self</th>
              <th scope="col">Supervisor</th>
              <th scope="col">Committee</th>
              {canRate && <th scope="col">Record</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((competency) => (
              <React.Fragment key={competency.id}>
                <tr>
                  <th scope="row">{competency.name}</th>
                  <td><Cell rating={ratingFor(competency.id, 'employee')} /></td>
                  <td><Cell rating={ratingFor(competency.id, 'supervisor')} /></td>
                  <td><Cell rating={ratingFor(competency.id, 'committee')} /></td>
                  {canRate && (
                    <td>
                      <button type="button" className="btn btn-ghost btn-xs"
                        aria-expanded={open === competency.id}
                        onClick={() => {
                          setOpen(open === competency.id ? null : competency.id);
                          setLevel(''); setComment('');
                        }}>
                        Rate {competency.name}
                      </button>
                    </td>
                  )}
                </tr>
                {canRate && open === competency.id && (
                  <tr>
                    <td colSpan={5}>
                      <form className="apr-rate-form"
                        onSubmit={(event) => {
                          event.preventDefault();
                          rate.mutate({
                            competency: competency.id, level, comment,
                            role: raterRole,
                          });
                        }}>
                        <label className="apr-field">
                          <span>Level for {competency.name}</span>
                          <select value={level} required
                            onChange={(e) => setLevel(e.target.value)}>
                            <option value="">Choose a level</option>
                            {LEVELS.map((l) => (
                              <option key={l.value} value={l.value}>
                                {l.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="apr-field apr-grow">
                          <span>Reasoning (required)</span>
                          <textarea rows={2} value={comment} required
                            minLength={10}
                            onChange={(e) => setComment(e.target.value)} />
                        </label>
                        <button type="submit" className="btn btn-primary btn-xs"
                          disabled={rate.isPending}>
                          Save rating
                        </button>
                      </form>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default CompetencyRatings;
