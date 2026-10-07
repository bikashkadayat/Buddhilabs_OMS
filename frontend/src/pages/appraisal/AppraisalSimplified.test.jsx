/**
 * Phase APM-UX — the simplified employee experience.
 *
 * The claim this phase makes is "ten stages become five, and nothing is
 * hidden that somebody needs". Both halves need pinning: the first is easy to
 * achieve by accidentally doing the second.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: {
    getAppraisal: vi.fn(), getEvidence: vi.fn(), getCompetencies: vi.fn(),
    getDashboard: vi.fn(), updateAppraisal: vi.fn(), rate: vi.fn(),
    addGoal: vi.fn(), updateGoal: vi.fn(), deleteGoal: vi.fn(),
    addDevelopmentAction: vi.fn(), addTrainingNeed: vi.fn(),
    decideTraining: vi.fn(), attachEvidence: vi.fn(), searchPeople: vi.fn(),
    recordFinalReview: vi.fn(), returnStage: vi.fn(), reopen: vi.fn(),
    submitGoals: vi.fn(), agreeGoals: vi.fn(), recordMidYear: vi.fn(),
    submitSelfAssessment: vi.fn(), recordSupervisorReview: vi.fn(),
    recordCommitteeReview: vi.fn(), agreeDevelopmentPlan: vi.fn(),
    agreeTrainingPlan: vi.fn(),
  },
}));

import StageTracker from '../../components/appraisal/StageTracker';
import AppraisalDetail from './AppraisalDetail';
import {
  EMPLOYEE_STEPS, STAGES, stepForIndex, stepForStatus,
} from '../../components/appraisal/appraisalLabels';
import { appraisalService } from '../../services/appraisalService';
import { appraisal, CAPS, EVIDENCE } from './testFixtures';

const qc = () => new QueryClient({
  defaultOptions: { queries: { retry: false } } });

const renderRecord = async (row) => {
  appraisalService.getAppraisal.mockResolvedValue(row);
  const out = render(
    <QueryClientProvider client={qc()}>
      <MemoryRouter initialEntries={['/appraisals/a-1']}>
        <Routes>
          <Route path="/appraisals/:id" element={<AppraisalDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole('heading', { name: 'Bikash Kadayat', level: 1 });
  return out;
};

beforeEach(() => {
  vi.clearAllMocks();
  appraisalService.getEvidence.mockResolvedValue(EVIDENCE);
  appraisalService.getCompetencies.mockResolvedValue([]);
  appraisalService.searchPeople.mockResolvedValue([]);
});

describe('the five-step map', () => {
  it('is exactly the five steps the specification names', () => {
    expect(EMPLOYEE_STEPS.map((s) => s.label)).toEqual([
      'My Goals', 'My Review', 'Manager Review', 'Final Feedback',
      'My Growth Plan']);
  });

  it('groups all ten backend stages, leaving none unmapped', () => {
    // A stage with no step would render as a blank tracker for whoever is at
    // it — and it would be whoever is at the newest stage, which is exactly
    // the person nobody tests as.
    for (const stage of STAGES) {
      expect(stepForStatus(stage.value), stage.value).not.toBeNull();
    }
  });

  it('maps by POSITION and by STATUS to the same step', () => {
    // The dashboard sends only a display label plus stage_index, so the two
    // paths must agree — otherwise the Home card and the record page would
    // disagree about which step somebody is on.
    STAGES.forEach((stage, i) => {
      expect(stepForIndex(i + 1), stage.value)
        .toEqual(stepForStatus(stage.value));
    });
  });

  it('folds goal setting, approval and mid-year into My Goals', () => {
    for (const status of ['goal_setting', 'goal_approval', 'mid_year_review']) {
      expect(stepForStatus(status).label).toBe('My Goals');
    }
  });

  it('folds supervisor and committee review into Manager Review', () => {
    // The committee's INVOLVEMENT stays visible in the feedback, attributed.
    // What goes is the demand that an employee learn the word "calibration".
    for (const status of ['supervisor_review', 'review_committee']) {
      expect(stepForStatus(status).label).toBe('Manager Review');
    }
  });
});

describe('what the tracker shows whom', () => {
  it('shows the subject five steps, not ten', async () => {
    await renderRecord(appraisal({
      capabilities: { ...CAPS, is_subject: true } }));
    const nav = screen.getByRole('navigation',
      { name: /where your appraisal is/i });
    expect(within(nav).getAllByRole('listitem')).toHaveLength(5);
    expect(within(nav).queryByText('Review Committee')).toBeNull();
    expect(within(nav).queryByText('Goal Approval')).toBeNull();
  });

  it('shows a reviewer the full ladder', async () => {
    // A reviewer needs to know a record is at Review Committee rather than
    // Supervisor Review. That is their job; it is not the employee's.
    await renderRecord(appraisal());
    const nav = screen.getByRole('navigation', { name: /appraisal stages/i });
    expect(within(nav).getAllByRole('listitem')).toHaveLength(10);
    expect(within(nav).getByText('Review Committee')).toBeInTheDocument();
  });

  it('announces the step in text, not by colour alone', () => {
    render(<StageTracker status="self_assessment" stageIndex={4} simple />);
    expect(screen.getByText('Step 2 of 5: My Review')).toBeInTheDocument();
  });

  it('says an appraisal is complete rather than showing it mid-ladder', () => {
    render(<StageTracker status="closed" stageIndex={10} simple />);
    expect(screen.getByText(/your appraisal is complete/i))
      .toBeInTheDocument();
  });
});

describe('no backend vocabulary reaches the employee', () => {
  const FORBIDDEN = [
    'goal_setting', 'goal_approval', 'mid_year_review', 'self_assessment',
    'supervisor_review', 'review_committee', 'final_review',
    'development_plan', 'training_plan',
    'Calibration', 'stage_index', 'capabilities',
  ];

  it('shows no raw status or internal term on the subject\'s record',
    async () => {
      const { container } = await renderRecord(appraisal({
        capabilities: { ...CAPS, is_subject: true } }));
      const text = container.textContent;
      for (const term of FORBIDDEN) {
        expect(text, `the employee must not see "${term}"`)
          .not.toContain(term);
      }
    });
});
