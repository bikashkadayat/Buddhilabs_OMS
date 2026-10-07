import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { CornerUpLeft, RotateCcw } from 'lucide-react';

import { appraisalService } from '../../services/appraisalService';
import { STAGES } from './appraisalLabels';

/**
 * The workflow bar (Phase APM-03b).
 *
 * ONE BUTTON PER NAMED TRANSITION, AND ONLY WHEN THE SERVER SAYS SO
 * -----------------------------------------------------------------
 * Every button below is driven by a flag in the record's `capabilities` block,
 * computed by appraisal.permissions. Nothing here reads the caller's role, and
 * there is no generic "advance" control: a generic status setter would let the
 * client ask for a move the engine refuses, and the refusal would arrive as a
 * 403 on a button that looked available.
 *
 * A person at any moment has at most one thing to do here, so the bar is
 * usually one button. That is the point — an appraisal screen offering six
 * plausible next steps is one where nobody knows whose turn it is.
 *
 * RETURNING NEEDS A REASON, IN THE UI AS WELL AS ON THE WIRE
 * ---------------------------------------------------------
 * The server requires ten characters. The form requires them too, because
 * discovering the rule after writing nothing is discovering it too late — and
 * because the reason is the only thing the person receiving the appraisal back
 * will actually read.
 */
const WorkflowActions = ({ appraisal, onError }) => {
  const queryClient = useQueryClient();
  const [returning, setReturning] = useState(false);
  const [reopening, setReopening] = useState(false);
  const [remarks, setRemarks] = useState('');
  const [toStatus, setToStatus] = useState('');

  const caps = appraisal.capabilities || {};
  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['appraisal', appraisal.id] });

  const run = useMutation({
    mutationFn: (fn) => fn(),
    onSuccess: () => {
      setReturning(false); setReopening(false); setRemarks('');
      invalidate();
    },
    onError,
  });

  const step = (label, allowed, fn) => (allowed ? (
    <button key={label} type="button" className="btn btn-primary btn-sm"
      disabled={run.isPending}
      onClick={() => run.mutate(() => fn(appraisal.id))}>
      {label}
    </button>
  ) : null);

  const buttons = [
    step('Submit objectives for approval', caps.can_submit_goals,
      appraisalService.submitGoals),
    step('Approve objectives', caps.can_agree_goals,
      appraisalService.agreeGoals),
    step('Record mid-year review', caps.can_record_mid_year,
      appraisalService.recordMidYear),
    step('Submit self assessment', caps.can_submit_self_assessment,
      appraisalService.submitSelfAssessment),
    step('Complete supervisor review', caps.can_write_supervisor_review,
      appraisalService.recordSupervisorReview),
    step('Complete committee review', caps.can_write_committee_review,
      appraisalService.recordCommitteeReview),
    step('Complete final review', caps.can_finalise,
      appraisalService.recordFinalReview),
    step('Agree development plan', caps.can_manage_development_plan,
      appraisalService.agreeDevelopmentPlan),
    step('Agree training plan and close', caps.can_manage_training_plan,
      appraisalService.agreeTrainingPlan),
  ].filter(Boolean);

  if (!buttons.length && !caps.can_return && !caps.can_reopen) return null;

  // Only stages already passed. Returning forwards is refused by the engine,
  // so offering it would be offering a control that 400s.
  const earlier = STAGES.slice(
    0, Math.max(0, (appraisal.stage_index || 1) - 1));

  return (
    <section className="apr-actions" aria-label="Appraisal actions">
      {buttons}

      {caps.can_return && !returning && (
        <button type="button" className="btn btn-ghost btn-sm"
          onClick={() => setReturning(true)}>
          <CornerUpLeft size={14} aria-hidden="true" /> Send back a stage
        </button>
      )}
      {caps.can_reopen && !reopening && (
        <button type="button" className="btn btn-ghost btn-sm"
          onClick={() => setReopening(true)}>
          <RotateCcw size={14} aria-hidden="true" /> Reopen this appraisal
        </button>
      )}

      {returning && (
        <form className="apr-rate-form"
          onSubmit={(event) => {
            event.preventDefault();
            run.mutate(() => appraisalService.returnStage(
              appraisal.id, { to_status: toStatus, remarks }));
          }}>
          <label className="apr-field">
            <span>Send back to</span>
            <select value={toStatus} required autoFocus
              onChange={(e) => setToStatus(e.target.value)}>
              <option value="">Choose a stage</option>
              {earlier.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </label>
          <label className="apr-field apr-grow">
            <span>Reason (required — the person receiving this will read it)</span>
            <textarea rows={2} value={remarks} required minLength={10}
              onChange={(e) => setRemarks(e.target.value)} />
          </label>
          <button type="submit" className="btn btn-primary btn-sm"
            disabled={run.isPending}>Send back</button>
          <button type="button" className="btn btn-ghost btn-sm"
            onClick={() => setReturning(false)}>Cancel</button>
        </form>
      )}

      {reopening && (
        <form className="apr-rate-form"
          onSubmit={(event) => {
            event.preventDefault();
            run.mutate(() => appraisalService.reopen(
              appraisal.id, { remarks }));
          }}>
          <label className="apr-field apr-grow">
            <span>
              Reason for reopening (required — this edits a record the employee
              may already hold a copy of)
            </span>
            <textarea rows={2} value={remarks} required minLength={10} autoFocus
              onChange={(e) => setRemarks(e.target.value)} />
          </label>
          <button type="submit" className="btn btn-primary btn-sm"
            disabled={run.isPending}>Reopen</button>
          <button type="button" className="btn btn-ghost btn-sm"
            onClick={() => setReopening(false)}>Cancel</button>
        </form>
      )}
    </section>
  );
};

export default WorkflowActions;
