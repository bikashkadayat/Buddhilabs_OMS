/**
 * Phase APM-FINAL — the Goal Approval stage, on screen.
 *
 * The stage exists so a person can tell an objective still under discussion
 * from one they are being held to. That distinction only reaches them if the
 * UI carries it, so these pin the three places it has to show: the tracker, the
 * workflow bar, and the objective's own badge.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: {
    submitGoals: vi.fn(), agreeGoals: vi.fn(), recordMidYear: vi.fn(),
    submitSelfAssessment: vi.fn(), recordSupervisorReview: vi.fn(),
    recordCommitteeReview: vi.fn(), recordFinalReview: vi.fn(),
    agreeDevelopmentPlan: vi.fn(), agreeTrainingPlan: vi.fn(),
    returnStage: vi.fn(), reopen: vi.fn(),
    addGoal: vi.fn(), updateGoal: vi.fn(), deleteGoal: vi.fn(),
  },
}));

import StageTracker from '../../components/appraisal/StageTracker';
import WorkflowActions from '../../components/appraisal/WorkflowActions';
import GoalEditor from '../../components/appraisal/GoalEditor';
import { STAGES, TOTAL_STAGES } from '../../components/appraisal/appraisalLabels';
import { appraisalService } from '../../services/appraisalService';
import { appraisal, CAPS, goal } from './testFixtures';

const renderIt = (ui) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  appraisalService.submitGoals.mockResolvedValue({});
  appraisalService.agreeGoals.mockResolvedValue({});
});

describe('the ladder', () => {
  it('has ten stages, with Goal Approval between Setting and Mid-Year', () => {
    const values = STAGES.map((s) => s.value);
    expect(values.indexOf('goal_approval'))
      .toBe(values.indexOf('goal_setting') + 1);
    expect(values.indexOf('mid_year_review'))
      .toBe(values.indexOf('goal_approval') + 1);
    expect(TOTAL_STAGES).toBe(10);
  });

  it('derives the total rather than hard-coding it', () => {
    // The stale "of 9" strings left behind when this stage was inserted are the
    // reason this is a constant and not a literal.
    expect(TOTAL_STAGES).toBe(STAGES.length);
  });

  it('shows Goal Approval as a step still to come at Goal Setting', () => {
    renderIt(<StageTracker status="goal_setting" stageIndex={1} />);
    const nav = screen.getByRole('navigation', { name: /appraisal stages/i });
    expect(within(nav).getByText('Goal Approval')).toBeInTheDocument();
    expect(within(nav).getByText('Stage 1 of 10: Goal Setting'))
      .toBeInTheDocument();
  });
});

describe('who submits and who approves', () => {
  it('offers the employee Submit, and not Approve', async () => {
    // Objectives are the employee's to PROPOSE. Accepting them is not.
    const user = userEvent.setup();
    renderIt(<WorkflowActions
      appraisal={appraisal({ status: 'goal_setting', stage_index: 1,
        capabilities: { ...CAPS, can_submit_goals: true } })}
      onError={() => {}} />);

    expect(screen.queryByRole('button', { name: /approve objectives/i }))
      .toBeNull();
    await user.click(screen.getByRole('button',
      { name: /submit objectives for approval/i }));
    expect(appraisalService.submitGoals).toHaveBeenCalledWith('a-1');
  });

  it('offers the supervisor Approve, and not Submit', async () => {
    const user = userEvent.setup();
    renderIt(<WorkflowActions
      appraisal={appraisal({ status: 'goal_approval', stage_index: 2,
        capabilities: { ...CAPS, can_agree_goals: true } })}
      onError={() => {}} />);

    expect(screen.queryByRole('button',
      { name: /submit objectives for approval/i })).toBeNull();
    await user.click(screen.getByRole('button',
      { name: /approve objectives/i }));
    expect(appraisalService.agreeGoals).toHaveBeenCalledWith('a-1');
  });

  it('lets a supervisor send objectives back to Goal Setting for rework',
    async () => {
      const user = userEvent.setup();
      appraisalService.returnStage.mockResolvedValue({});
      renderIt(<WorkflowActions
        appraisal={appraisal({ status: 'goal_approval', stage_index: 2,
          capabilities: { ...CAPS, can_return: true } })}
        onError={() => {}} />);

      await user.click(screen.getByRole('button', { name: /send back a stage/i }));
      const select = screen.getByLabelText(/send back to/i);
      const options = within(select).getAllByRole('option')
        .map((o) => o.textContent);
      // Only Goal Setting is behind it — you cannot return to where you are.
      expect(options).toEqual(['Choose a stage', 'Goal Setting']);
    });
});

describe('an objective says which state it is in', () => {
  it('marks a draft as Draft', () => {
    renderIt(<GoalEditor
      appraisal={appraisal({
        goals: [goal('Deliver the return', 100,
          { status: 'draft', status_label: 'Draft' })] })}
      canEdit />);
    expect(screen.getByText('Draft')).toBeInTheDocument();
  });

  it('marks an approved objective as Approved', () => {
    // The whole point of the stage: "we never actually agreed that" is the
    // argument this badge exists to settle.
    renderIt(<GoalEditor appraisal={appraisal()} canEdit={false} />);
    expect(screen.getAllByText('Approved').length).toBe(2);
  });

  it('marks a locked objective as Locked and offers no editing', () => {
    renderIt(<GoalEditor
      appraisal={appraisal({
        goals: [goal('Deliver the return', 100,
          { status: 'locked', status_label: 'Locked' })] })}
      canEdit={false} />);
    expect(screen.getByText('Locked')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^edit/i })).toBeNull();
  });

  it('shows the status in words, not by colour alone', () => {
    // The badge has a class per state, but the state is always spelled out —
    // a colour a screen reader cannot announce is not a status.
    const { container } = renderIt(
      <GoalEditor appraisal={appraisal()} canEdit={false} />);
    const badge = container.querySelector('.apr-goal-status');
    expect(badge.textContent.trim()).toBe('Approved');
  });
});
