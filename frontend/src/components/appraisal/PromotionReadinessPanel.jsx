import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { appraisalService } from '../../services/appraisalService';
import { NOT_CONSIDERED, READINESS, readinessLabel } from './appraisalLabels';

/**
 * Promotion readiness (Phase APM-03b).
 *
 * FOUR STATES, THREE OF WHICH CAN BE SUBMITTED
 * --------------------------------------------
 * Ready, Ready With Development, Development Required — and "Not Considered",
 * which is the ABSENCE of an answer rather than a fourth answer. It is offered
 * in the selector because somebody has to be able to see, and to return to, the
 * state of not having reached the question. It is submitted as an empty string,
 * so it can never be counted, charted or filtered for beside the three real
 * answers. "Not considered" and "development required" are different things,
 * and a control that collapsed them would put a negative on every record that
 * simply never got there.
 *
 * THE RATIONALE IS MANDATORY IN ALL THREE DIRECTIONS
 * --------------------------------------------------
 * The server refuses to complete the final review if any readiness is set
 * without one. Enforced here too — not because the client is trusted, but
 * because finding out at the moment you try to close the appraisal is finding
 * out too late. The direction that most needs a reason is Development Required:
 * a no with no stated development is a verdict nobody can act on or appeal.
 *
 * NO SCORE, NO RANK, NO ORDERING
 * ------------------------------
 * The three answers are radio buttons in the model's order and nothing else.
 * There is no numeric equivalent, no progress bar, and no "readiness level"
 * comparing this person with anybody.
 */
const PromotionReadinessPanel = ({ appraisal, canEdit }) => {
  const queryClient = useQueryClient();
  const [readiness, setReadiness] = useState(
    appraisal.promotion_readiness || '');
  const [rationale, setRationale] = useState(
    appraisal.promotion_rationale || '');
  const [successor, setSuccessor] = useState(appraisal.successor_for || '');
  const [error, setError] = useState('');

  const save = useMutation({
    mutationFn: (payload) =>
      appraisalService.updateAppraisal(appraisal.id, payload),
    onSuccess: () => {
      setError('');
      queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });
    },
    onError: (err) => setError(
      err?.response?.data?.detail
      || 'That recommendation could not be saved.',
    ),
  });

  if (!canEdit) {
    return (
      <section className="memo-dash-section" aria-labelledby="apr-pro-h">
        <div className="memo-dash-head">
          <h3 id="apr-pro-h">Readiness For More Responsibility</h3>
        </div>
        <p className="apr-readiness-value">
          {appraisal.promotion_readiness_label
            || readinessLabel(appraisal.promotion_readiness)}
        </p>
        {appraisal.promotion_rationale && (
          <p className="lr-page-sub">{appraisal.promotion_rationale}</p>
        )}
        {appraisal.successor_for && (
          <p className="lr-page-sub">
            Being developed towards: {appraisal.successor_for}
          </p>
        )}
      </section>
    );
  }

  return (
    <section className="memo-dash-section" aria-labelledby="apr-pro-h">
      <div className="memo-dash-head">
        <h3 id="apr-pro-h">Readiness For More Responsibility</h3>
      </div>
      <p className="lr-page-sub">
        A judgement you record and can be held to. It is never calculated,
        never scored, and never compared with anybody else's.
      </p>

      {error && <div className="task-callout is-no" role="alert">{error}</div>}

      <form className="apr-goal-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (readiness && !rationale.trim()) {
            setError('A recommendation needs its rationale recorded.');
            return;
          }
          save.mutate({
            promotion_readiness: readiness,
            promotion_rationale: rationale,
            successor_for: successor,
          });
        }}>
        <fieldset className="apr-fieldset">
          <legend>Recommendation</legend>
          {[...READINESS, { value: '', label: NOT_CONSIDERED }].map((option) => (
            <label key={option.value || 'none'} className="apr-radio">
              <input type="radio" name="promotion_readiness"
                value={option.value}
                checked={readiness === option.value}
                onChange={() => setReadiness(option.value)} />
              <span>{option.label}</span>
            </label>
          ))}
        </fieldset>

        <label className="apr-field">
          <span>
            Rationale{readiness ? ' (required)' : ''}
          </span>
          <textarea rows={3} value={rationale} required={Boolean(readiness)}
            onChange={(e) => setRationale(e.target.value)} />
        </label>

        <label className="apr-field">
          <span>Successor for (optional)</span>
          <input value={successor} maxLength={150}
            placeholder="A role this person is being developed towards"
            onChange={(e) => setSuccessor(e.target.value)} />
        </label>

        <div className="apr-goal-actions">
          <button type="submit" className="btn btn-primary btn-sm"
            disabled={save.isPending}>Save recommendation</button>
        </div>
      </form>
    </section>
  );
};

export default PromotionReadinessPanel;
