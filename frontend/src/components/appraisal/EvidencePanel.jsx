import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Info, Paperclip } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';

/**
 * The evidence panel (Phase APM-03b).
 *
 * CONSUMED, NEVER RECOMPUTED
 * --------------------------
 * Every figure below arrives from `/appraisals/{id}/evidence/`, which reads the
 * Phase T6 contract — the same code that answers the task module's own screens.
 * There is no arithmetic in this file. Not a percentage, not a total, not an
 * average. A second place that computes evidence is a second set of numbers
 * that disagrees with the first, and the disagreement would surface in a
 * conversation about somebody's year.
 *
 * THE EMPLOYEE CHOOSES WHAT IS CITED
 * ----------------------------------
 * Evidence is not silently harvested onto the record. Somebody presses "Cite
 * these figures", which freezes the current pack with the date it was read and
 * an optional note saying why it is relevant. That is what makes it evidence a
 * person offered rather than a measurement taken of them — and it is why the
 * citation carries a note field at all.
 *
 * WHAT IS SHOWN BESIDE EVERY NUMBER
 * ---------------------------------
 * The denominator (`basis_of`) and the definition, always. A percentage with no
 * denominator is the kind of number that gets quoted for years, and "75%" over
 * four tasks and over four hundred are not the same claim.
 */
const Figure = ({ metric }) => (
  <span className="memo-tile tone-neutral">
    <span className="memo-tile-value">
      {metric.value === null || metric.value === undefined
        ? '—' : `${metric.value}${metric.unit === 'percent' ? '%' : ''}`}
    </span>
    <span className="memo-tile-label">{metric.label}</span>
    {metric.basis_of && (
      <span className="memo-tile-hint">{metric.basis_of}</span>
    )}
    {metric.missing && (
      <span className="memo-tile-hint">Not currently tracked</span>
    )}
    {metric.definition && (
      <span className="memo-tile-hint">{metric.definition}</span>
    )}
  </span>
);

const EvidencePanel = ({ appraisal, canAttach }) => {
  const queryClient = useQueryClient();
  const [note, setNote] = useState('');
  const [goalId, setGoalId] = useState('');
  const [error, setError] = useState('');

  const { data, isLoading, isError } = useQuery({
    queryKey: ['appraisal', appraisal.id, 'evidence'],
    queryFn: () => appraisalService.getEvidence(appraisal.id),
    retry: false,
  });

  const cite = useMutation({
    mutationFn: (payload) =>
      appraisalService.attachEvidence(appraisal.id, payload),
    onSuccess: () => {
      setNote(''); setGoalId(''); setError('');
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
    onError: (err) => setError(
      err?.response?.data?.evidence
      || err?.response?.data?.detail
      || 'That could not be added.',
    ),
  });

  const cited = appraisal.evidence_references || [];

  return (
    <section className="memo-dash-section" aria-labelledby="apr-ev-h">
      <div className="memo-dash-head"><h3 id="apr-ev-h">My Work Record</h3></div>

      {/* Stated before the figures, not after them. Somebody reading their own
          record deserves to know what it is and is not before they read it. */}
      <div className="task-callout is-warn" role="note">
        <Info size={13} aria-hidden="true" /> <b>This is a record of your work,
        not a score.</b>{' '}
        {data?.note || 'Nothing in this appraisal is computed from these figures.'}
      </div>

      {isLoading && <p className="lr-page-sub">Loading your work record…</p>}
      {isError && (
        <p className="lr-page-sub">
          The evidence pack could not be loaded. The rest of this appraisal is
          unaffected.
        </p>
      )}

      {data && !data.available && (
        <p className="lr-page-sub">
          Nothing is tracked automatically yet, so this appraisal rests entirely on
          what people have written.
        </p>
      )}

      {data?.available && (
        <>
          <p className="lr-page-sub">
            {data.period_start} to {data.period_end}
          </p>

          {data.low_volume && (
            /* Shown to the employee as well as to the reviewer. Percentages
               over a handful of tasks are noise, and somebody should know that
               about their own numbers before anybody quotes them. */
            <div className="task-callout is-no" role="note">
              <AlertTriangle size={13} aria-hidden="true" /> <b>Too few records to read
              anything into.</b> The percentages below are not a meaningful
              measure over this few.
            </div>
          )}

          <div className="memo-tiles">
            {(data.headline || []).map((metric) => (
              <Figure key={metric.key} metric={metric} />
            ))}
          </div>

          {data.snapshots?.length > 0 && (
            <div className="lr-table-wrap" style={{ marginTop: 16 }}>
              <table className="lr-table">
                <caption className="sr-only">
                  What your work record said on each date
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Period</th>
                    <th scope="col">Assigned</th>
                    <th scope="col">Completed</th>
                    <th scope="col">Completion</th>
                    <th scope="col">On Time</th>
                  </tr>
                </thead>
                <tbody>
                  {data.snapshots.map((row) => (
                    <tr key={`${row.snapshot_date}-${row.period_type}`}>
                      <td style={{ whiteSpace: 'nowrap' }}>{row.snapshot_date}</td>
                      <td>{row.period_type}</td>
                      <td>{row.tasks_assigned}</td>
                      <td>{row.tasks_completed}</td>
                      <td>{row.completion_percent}%</td>
                      <td>{row.on_time_percent}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {canAttach && data?.available && (
        <form className="apr-cite"
          onSubmit={(event) => {
            event.preventDefault();
            cite.mutate({ note, goal: goalId || null });
          }}>
          <label className="apr-field">
            <span>Add against a goal (optional)</span>
            <select value={goalId} onChange={(e) => setGoalId(e.target.value)}>
              <option value="">My appraisal as a whole</option>
              {(appraisal.goals || []).map((goal) => (
                <option key={goal.id} value={goal.id}>{goal.objective}</option>
              ))}
            </select>
          </label>
          <label className="apr-field apr-grow">
            <span>Why this matters (optional)</span>
            <input value={note} maxLength={2000}
              onChange={(e) => setNote(e.target.value)} />
          </label>
          <button type="submit" className="btn btn-primary btn-sm"
            disabled={cite.isPending}>
            <Paperclip size={14} aria-hidden="true" /> Add to my appraisal
          </button>
        </form>
      )}

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      {cited.length > 0 && (
        <>
          <h4 className="apr-sub-h">Added to this appraisal</h4>
          <ul className="apr-cited">
            {cited.map((row) => (
              <li key={row.id}>
                <b>{row.source}</b> · {row.period_start} to {row.period_end} ·
                read {new Date(row.captured_at).toLocaleDateString()} ·
                added by {row.attached_by_name || '—'}
                {row.note && <div className="apr-cited-note">“{row.note}”</div>}
                {/* The frozen figures, so a number quoted months later can be
                    checked against the day it was true rather than recomputed
                    against tasks that have since moved. */}
                {Array.isArray(row.metrics) && row.metrics.length > 0 && (
                  <div className="apr-cited-note">
                    {row.metrics.map((m) => `${m.label}: ${m.value ?? '—'}`)
                      .join(' · ')}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
};

export default EvidencePanel;
