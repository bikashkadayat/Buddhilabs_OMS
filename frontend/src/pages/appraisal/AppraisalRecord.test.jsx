/**
 * Phase APM-03b — the shared appraisal record.
 *
 * One route for every role. What differs is which controls appear, and that
 * comes from the SERVER's `capabilities` block. These pin that the page never
 * re-derives permission from anything else — including a screen with NO
 * controls, which is a correct screen rather than a broken one.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: {
    getAppraisal: vi.fn(), getEvidence: vi.fn(), getCompetencies: vi.fn(),
    updateAppraisal: vi.fn(), rate: vi.fn(), agreeGoals: vi.fn(),
    recordMidYear: vi.fn(), submitSelfAssessment: vi.fn(),
    recordSupervisorReview: vi.fn(), recordCommitteeReview: vi.fn(),
    recordFinalReview: vi.fn(), agreeDevelopmentPlan: vi.fn(),
    agreeTrainingPlan: vi.fn(), returnStage: vi.fn(), reopen: vi.fn(),
    attachEvidence: vi.fn(), addGoal: vi.fn(), updateGoal: vi.fn(),
    deleteGoal: vi.fn(), addDevelopmentAction: vi.fn(),
    addTrainingNeed: vi.fn(), decideTraining: vi.fn(),
  },
}));

import AppraisalDetail from './AppraisalDetail';
import { appraisalService } from '../../services/appraisalService';
import { appraisal, CAPS, EVIDENCE } from './testFixtures';

const COMPETENCIES = [
  { id: 'c-1', code: 'delivery', name: 'Delivery' },
  { id: 'c-2', code: 'teamwork', name: 'Teamwork' },
];

const renderRecord = async (row) => {
  appraisalService.getAppraisal.mockResolvedValue(row);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const out = render(
    <QueryClientProvider client={qc}>
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
  appraisalService.getCompetencies.mockResolvedValue(COMPETENCIES);
  appraisalService.rate.mockResolvedValue({});
  appraisalService.recordFinalReview.mockResolvedValue({});
  appraisalService.returnStage.mockResolvedValue({});
});

describe('the stage tracker', () => {
  it('announces the position in the ladder in text, not only in colour',
    async () => {
      await renderRecord(appraisal());
      const nav = screen.getByRole('navigation', { name: /appraisal stages/i });
      expect(within(nav).getByText('Stage 7 of 10: Final Review'))
        .toBeInTheDocument();
    });

  it('marks the current step with aria-current, and completed ones in words',
    async () => {
      await renderRecord(appraisal());
      const current = screen.getByRole('listitem', { current: 'step' });
      expect(current).toHaveTextContent('Final Review');
      // Colour alone cannot say "done" to a screen reader or a printer.
      expect(screen.getAllByText(/— completed/).length).toBe(6);
    });

  it('shows the steps that have not been reached yet', async () => {
    // "Final Review" tells somebody where they are. It does not tell them what
    // is left, which is the only thing the tracker adds.
    await renderRecord(appraisal());
    const nav = screen.getByRole('navigation', { name: /appraisal stages/i });
    expect(within(nav).getByText('Training Plan')).toBeInTheDocument();
    expect(within(nav).getByText('Closed')).toBeInTheDocument();
  });
});

describe('the server decides which controls exist', () => {
  it('renders a record with no controls at all when the caller may do nothing',
    async () => {
      // A committee member reading an appraisal at goal-setting stage. This is
      // a CORRECT screen: they may read it and do nothing with it.
      await renderRecord(appraisal());
      expect(screen.queryByRole('button', { name: /agree objectives/i })).toBeNull();
      expect(screen.queryByRole('button', { name: /complete final review/i })).toBeNull();
      expect(screen.queryByRole('button', { name: /add objective/i })).toBeNull();
      expect(screen.queryByRole('button', { name: /send back a stage/i })).toBeNull();
    });

  it('offers exactly the transition the capability block allows', async () => {
    const user = userEvent.setup();
    await renderRecord(appraisal({
      capabilities: { ...CAPS, can_finalise: true } }));

    const actions = screen.getByRole('region', { name: /appraisal actions/i });
    expect(within(actions).getAllByRole('button')).toHaveLength(1);
    await user.click(within(actions).getByRole('button',
      { name: /complete final review/i }));
    expect(appraisalService.recordFinalReview).toHaveBeenCalledWith('a-1');
  });

  it('shows a written block nobody may write and nobody has written: not at all',
    async () => {
      await renderRecord(appraisal());
      expect(screen.queryByRole('heading', { name: /self assessment/i }))
        .toBeNull();
    });

  it('shows a written block as prose when it exists but is not the caller\'s',
    async () => {
      // A greyed-out textarea invites people to try to type in it and explains
      // nothing. Prose that is not yours to edit should look like prose.
      await renderRecord(appraisal({
        self_assessment: 'I delivered both objectives.' }));
      expect(screen.getByText('I delivered both objectives.'))
        .toBeInTheDocument();
      expect(screen.queryByRole('textbox',
        { name: /self assessment/i })).toBeNull();
    });
});

describe('sending an appraisal back', () => {
  it('offers only stages already passed', async () => {
    // Returning forwards is refused by the engine, so offering it would be
    // offering a control that 400s.
    const user = userEvent.setup();
    await renderRecord(appraisal({
      capabilities: { ...CAPS, can_return: true } }));
    await user.click(screen.getByRole('button', { name: /send back a stage/i }));

    const select = screen.getByLabelText(/send back to/i);
    const options = within(select).getAllByRole('option')
      .map((o) => o.textContent);
    expect(options).toEqual(['Choose a stage', 'Goal Setting', 'Goal Approval',
      'Mid-Year Review', 'Self Assessment', 'Supervisor Review',
      'Review Committee']);
    expect(options).not.toContain('Training Plan');
  });

  it('requires a written reason', async () => {
    const user = userEvent.setup();
    await renderRecord(appraisal({
      capabilities: { ...CAPS, can_return: true } }));
    await user.click(screen.getByRole('button', { name: /send back a stage/i }));
    expect(screen.getByLabelText(/reason/i)).toBeRequired();
    expect(screen.getByLabelText(/reason/i)).toHaveAttribute('minLength', '10');
  });
});

describe('competency ratings', () => {
  it('shows self, supervisor and committee side by side', async () => {
    // The gap between a self-rating and a supervisor's is usually the most
    // useful thing in the conversation, so it is never reconciled away.
    await renderRecord(appraisal({
      competency_ratings: [
        { id: 'r-1', competency: 'c-1', competency_name: 'Delivery',
          level: 'meets', level_label: 'Meets Expectations',
          comment: 'Consistent all year.', rated_by_role: 'employee',
          rated_by_name: 'Bikash Kadayat' },
        { id: 'r-2', competency: 'c-1', competency_name: 'Delivery',
          level: 'exceeds', level_label: 'Exceeds Expectations',
          comment: 'Took on the archive work unasked.',
          rated_by_role: 'supervisor', rated_by_name: 'Sita Rai' },
      ],
    }));
    const table = screen.getByRole('table',
      { name: /competency levels recorded/i });
    expect(within(table).getByText('Meets Expectations')).toBeInTheDocument();
    expect(within(table).getByText('Exceeds Expectations')).toBeInTheDocument();
    expect(within(table).getByText('Consistent all year.')).toBeInTheDocument();
  });

  it('offers levels as words with no numeric equivalent', async () => {
    const user = userEvent.setup();
    await renderRecord(appraisal({
      capabilities: { ...CAPS, can_rate_as_supervisor: true } }));
    await user.click(await screen.findByRole('button',
      { name: /rate Delivery/i }));

    const select = screen.getByLabelText(/level for Delivery/i);
    const options = within(select).getAllByRole('option')
      .map((o) => o.textContent);
    expect(options).toEqual(['Choose a level', 'Needs Development',
      'Developing', 'Meets Expectations', 'Exceeds Expectations',
      'Outstanding']);
    for (const option of options) expect(option).not.toMatch(/\d/);
  });

  it('requires reasoning with every level', async () => {
    // A level with no reasoning is a number in disguise.
    const user = userEvent.setup();
    await renderRecord(appraisal({
      capabilities: { ...CAPS, can_rate_as_supervisor: true } }));
    await user.click(await screen.findByRole('button',
      { name: /rate Delivery/i }));
    expect(screen.getByLabelText(/reasoning/i)).toBeRequired();
  });

  it('records the rating under the role the SERVER said the caller may use',
    async () => {
      const user = userEvent.setup();
      await renderRecord(appraisal({
        capabilities: { ...CAPS, can_rate_as_committee: true } }));
      await user.click(await screen.findByRole('button',
        { name: /rate Teamwork/i }));
      await user.selectOptions(screen.getByLabelText(/level for Teamwork/i),
        'outstanding');
      await user.type(screen.getByLabelText(/reasoning/i),
        'Held the cross-team work together.');
      await user.click(screen.getByRole('button', { name: /save rating/i }));

      expect(appraisalService.rate).toHaveBeenCalledWith('a-1', {
        competency: 'c-2', level: 'outstanding',
        comment: 'Held the cross-team work together.', role: 'committee',
      });
    });

  it('shows no total, average or count of levels', async () => {
    const { container } = await renderRecord(appraisal({
      competency_ratings: [
        { id: 'r-1', competency: 'c-1', competency_name: 'Delivery',
          level: 'meets', level_label: 'Meets Expectations',
          comment: 'Consistent.', rated_by_role: 'employee',
          rated_by_name: 'Bikash Kadayat' },
      ],
    }));
    const table = container.querySelector('table');
    expect(table.querySelector('tfoot')).toBeNull();
    const text = container.textContent.toLowerCase();
    // "average" is deliberately absent: the panel's own note says levels are
    // "never averaged", and a guard that fires on the denial is one the next
    // person loosens rather than tightens. The tfoot check above is the
    // structural version of the same claim.
    for (const word of ['overall rating', 'total level', 'composite',
      'out of 5']) {
      expect(text, `the record must not say "${word}"`).not.toContain(word);
    }
  });
});

describe('the subject\'s reading view', () => {
  it('gathers feedback in one place for the person it is about', async () => {
    // Feedback is the part of an appraisal people come back to read, and the
    // APM-00 freeze recorded that it could only be found by scrolling the whole
    // record mixed in with the controls for editing it.
    await renderRecord(appraisal({
      capabilities: { ...CAPS, is_subject: true },
      supervisor_comments: 'A solid year.',
      committee_comments: 'The committee agrees.',
    }));
    const section = screen.getByRole('region', { name: /^feedback$/i });
    expect(within(section).getByText('A solid year.')).toBeInTheDocument();
    expect(within(section).getByText('The committee agrees.'))
      .toBeInTheDocument();
  });

  it('does not show that reading view to somebody else', async () => {
    await renderRecord(appraisal({ supervisor_comments: 'A solid year.' }));
    expect(screen.queryByRole('region', { name: /^feedback$/i })).toBeNull();
  });
});
