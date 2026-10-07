import React, { useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { appraisalService } from '../../services/appraisalService';
import { Skeleton, ErrorState } from '../../components/leave-records/States';
import StageTracker from '../../components/appraisal/StageTracker';
import GoalEditor from '../../components/appraisal/GoalEditor';
import EvidencePanel from '../../components/appraisal/EvidencePanel';
import CompetencyRatings from '../../components/appraisal/CompetencyRatings';
import DevelopmentPlanPanel from '../../components/appraisal/DevelopmentPlanPanel';
import TrainingPlanPanel from '../../components/appraisal/TrainingPlanPanel';
import PromotionReadinessPanel from '../../components/appraisal/PromotionReadinessPanel';
import FeedbackPanel from '../../components/appraisal/FeedbackPanel';
import WrittenRecord from '../../components/appraisal/WrittenRecord';
import SelfAssessmentForm from '../../components/appraisal/SelfAssessmentForm';
import SupervisorFeedbackForm from '../../components/appraisal/SupervisorFeedbackForm';
import WorkflowActions from '../../components/appraisal/WorkflowActions';

/**
 * One appraisal, in full (Phase APM-03b).
 *
 * ONE PAGE FOR EVERY ROLE
 * -----------------------
 * The employee, the supervisor, the committee and HR all read this route. There
 * is no separate "supervisor view" of an appraisal, because two pages rendering
 * the same record is two places a permission can be got wrong, and only one of
 * them would be tested the day somebody changes a rule. What differs between
 * roles is which controls appear, and that comes from the SERVER's
 * `capabilities` block — computed by appraisal.permissions, never re-derived
 * here from the caller's role.
 *
 * The consequence worth stating: a screen with no controls is a correct screen.
 * A committee member reading an appraisal at goal-setting stage sees the record
 * and no buttons, which is exactly what they may do with it.
 *
 * SECTION ORDER IS THE CONVERSATION'S ORDER
 * -----------------------------------------
 * Objectives, then the evidence about them, then what people wrote, then what
 * happens next. Evidence sits BEFORE the assessments deliberately: it is
 * context for a judgement, and putting it after reads as justification for one
 * already made.
 */
const AppraisalDetail = () => {
  const { id } = useParams();
  const [error, setError] = useState('');

  const { data, isLoading, isError, error: loadError, refetch } = useQuery({
    queryKey: ['appraisal', id],
    queryFn: () => appraisalService.getAppraisal(id),
  });

  if (isLoading) return <div className="page"><Skeleton rows={5} /></div>;
  if (isError) {
    return (
      <div className="page">
        <ErrorState error={loadError} onRetry={refetch} />
      </div>
    );
  }

  const caps = data.capabilities || {};
  const raterRole = caps.can_rate_as_employee ? 'employee'
    : caps.can_rate_as_supervisor ? 'supervisor'
      : caps.can_rate_as_committee ? 'committee' : null;

  return (
    <div className="page memo-page">
      <div className="lr-page-head">
        <div>
          <h1 className="lr-page-title">{data.employee_name}</h1>
          <p className="lr-page-sub">
            {data.cycle_name}
            {data.designation ? ` · ${data.designation}` : ''}
            {data.department_name ? ` · ${data.department_name}` : ''}
            {data.supervisor_name ? ` · Supervisor: ${data.supervisor_name}` : ''}
          </p>
        </div>
      </div>

      {/* The SUBJECT sees five steps; anybody reviewing sees the full ladder.
          A reviewer needs to know a record is at Review Committee rather than
          Supervisor Review — that is their job. The person being appraised does
          not, and making them learn it is the complexity this phase removes. */}
      <StageTracker status={data.status} stageIndex={data.stage_index}
        simple={Boolean(caps.is_subject)} />

      {error && (
        <div className="task-callout is-no" role="alert">{error}</div>
      )}

      <WorkflowActions appraisal={data}
        onError={(err) => setError(
          err?.response?.data?.detail
          || Object.values(err?.response?.data || {}).flat().join(' ')
          || 'That step could not be completed.')} />

      <GoalEditor appraisal={data} canEdit={Boolean(caps.can_edit_goals)} />

      <EvidencePanel appraisal={data}
        canAttach={Boolean(caps.can_attach_evidence)} />

      <CompetencyRatings appraisal={data} canRate={Boolean(raterRole)}
        raterRole={raterRole} />

      {/* The written record, each block guarded by whose turn it is. A block
          nobody may write and nobody has written renders nothing at all.

          The self-assessment has its own component because it is the only one
          with sections — five prompts composed into the same single field, so
          the person writing it has somewhere to start. The other three are one
          box each, which is what they are: a reviewer writing prose. */}
      <SelfAssessmentForm appraisal={data}
        canWrite={Boolean(caps.can_write_self_assessment)} />
      <SupervisorFeedbackForm appraisal={data}
        canWrite={Boolean(caps.can_write_supervisor_review)} />
      <WrittenRecord appraisal={data} field="committee_comments"
        label="Committee feedback"
        canWrite={Boolean(caps.can_write_committee_review)} />
      <WrittenRecord appraisal={data} field="final_summary"
        label="Final feedback" canWrite={Boolean(caps.can_finalise)} />

      {/* The reading view of everything above, gathered in one place. Shown
          only to the subject, for whom the record is a thing to come back and
          read rather than a thing to fill in. */}
      {caps.is_subject && <FeedbackPanel appraisal={data} />}

      <PromotionReadinessPanel appraisal={data}
        canEdit={Boolean(caps.can_finalise)} />

      <DevelopmentPlanPanel appraisal={data}
        canManage={Boolean(caps.can_manage_development_plan)} />

      <TrainingPlanPanel appraisal={data}
        canManage={Boolean(caps.can_manage_training_plan)}
        canDecide={Boolean(caps.can_decide_training)} />

      {data.timeline?.length > 0 && (
        <section className="memo-dash-section" aria-labelledby="apr-tl-h">
          <div className="memo-dash-head"><h3 id="apr-tl-h">History</h3></div>
          <ol className="apr-timeline">
            {data.timeline.map((row) => (
              <li key={row.id}>
                <b>{row.action_label}</b> · {row.actor_name || 'System'} ·{' '}
                {new Date(row.created_at).toLocaleString()}
                {row.remarks && (
                  <div className="memo-tile-hint">{row.remarks}</div>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
};

export default AppraisalDetail;
