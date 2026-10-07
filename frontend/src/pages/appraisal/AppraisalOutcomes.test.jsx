/**
 * Phase APM-03b — promotion readiness and the training plan.
 *
 * These two panels are where the phase's model deltas surface. Both are places
 * where a well-meant convenience becomes a judgement: a four-value selector
 * collapsed back to a yes/no, or a colour ramp that turns three words into a
 * score.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: {
    updateAppraisal: vi.fn(), addTrainingNeed: vi.fn(), decideTraining: vi.fn(),
    searchPeople: vi.fn(),
  },
}));

import PromotionReadinessPanel from '../../components/appraisal/PromotionReadinessPanel';
import TrainingPlanPanel from '../../components/appraisal/TrainingPlanPanel';
import { appraisalService } from '../../services/appraisalService';
import { appraisal } from './testFixtures';

const renderPanel = (ui) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  appraisalService.updateAppraisal.mockResolvedValue({});
  appraisalService.addTrainingNeed.mockResolvedValue({});
  appraisalService.decideTraining.mockResolvedValue({});
  appraisalService.searchPeople.mockResolvedValue([]);
});

describe('promotion readiness', () => {
  it('offers all four states the specification names', () => {
    renderPanel(<PromotionReadinessPanel appraisal={appraisal()} canEdit />);
    for (const label of ['Ready', 'Ready With Development',
      'Development Required', 'Not Yet Considered']) {
      expect(screen.getByRole('radio', { name: label })).toBeInTheDocument();
    }
  });

  it('submits "not considered" as an ABSENCE, not as a fourth value', async () => {
    // It must never be countable, chartable or filterable beside the three real
    // answers — "not considered" and "development required" are different, and
    // a stored fourth value would put a negative on every record that never
    // reached the question.
    const user = userEvent.setup();
    renderPanel(
      <PromotionReadinessPanel
        appraisal={appraisal({ promotion_readiness: 'ready',
          promotion_rationale: 'Already written.' })} canEdit />);
    await user.click(screen.getByRole('radio', { name: 'Not Yet Considered' }));
    await user.click(screen.getByRole('button', { name: /save recommendation/i }));

    expect(appraisalService.updateAppraisal).toHaveBeenCalledWith('a-1',
      expect.objectContaining({ promotion_readiness: '' }));
  });

  it('defaults an untouched record to Not Yet Considered', () => {
    renderPanel(<PromotionReadinessPanel appraisal={appraisal()} canEdit />);
    expect(screen.getByRole('radio', { name: 'Not Yet Considered' })).toBeChecked();
  });

  it('refuses a recommendation whose rationale is only whitespace', async () => {
      // The field is marked `required`, so the browser already stops an EMPTY
      // one. A space is what gets past that, and the direction it matters most
      // in is Development Required: a no with no stated development is a
      // verdict nobody can act on or appeal.
      const user = userEvent.setup();
      renderPanel(<PromotionReadinessPanel appraisal={appraisal()} canEdit />);
      await user.click(screen.getByRole('radio',
        { name: 'Development Required' }));
      await user.type(screen.getByLabelText(/rationale/i), '   ');
      await user.click(screen.getByRole('button',
        { name: /save recommendation/i }));

      expect(screen.getByRole('alert'))
        .toHaveTextContent(/needs its rationale recorded/i);
      expect(appraisalService.updateAppraisal).not.toHaveBeenCalled();
    });

  it('accepts a recommendation once its rationale is written', async () => {
    const user = userEvent.setup();
    renderPanel(<PromotionReadinessPanel appraisal={appraisal()} canEdit />);
    await user.click(screen.getByRole('radio', { name: 'Ready' }));
    await user.type(screen.getByLabelText(/rationale/i),
      'Operating a grade above for two cycles.');
    await user.click(screen.getByRole('button',
      { name: /save recommendation/i }));

    expect(appraisalService.updateAppraisal).toHaveBeenCalledWith('a-1',
      expect.objectContaining({
        promotion_readiness: 'ready',
        promotion_rationale: 'Operating a grade above for two cycles.',
      }));
  });

  it('allows Not Considered with no rationale', async () => {
    const user = userEvent.setup();
    renderPanel(<PromotionReadinessPanel appraisal={appraisal()} canEdit />);
    await user.click(screen.getByRole('button', { name: /save recommendation/i }));
    expect(appraisalService.updateAppraisal).toHaveBeenCalled();
  });

  it('attaches no number, rank or colour scale to the three answers', () => {
    const { container } = renderPanel(
      <PromotionReadinessPanel appraisal={appraisal()} canEdit />);
    const fieldset = container.querySelector('.apr-fieldset');
    // No 1-4 beside the labels, and no per-value class a stylesheet could turn
    // into a red-to-green ramp — a five-step colour scale is a score with the
    // digits filed off.
    expect(fieldset.textContent).not.toMatch(/\d/);
    for (const radio of within(fieldset).getAllByRole('radio')) {
      expect(radio.closest('label').className).toBe('apr-radio');
    }
  });

  it('reads as plain prose to somebody who may not edit it', () => {
    renderPanel(
      <PromotionReadinessPanel
        appraisal={appraisal({
          promotion_readiness: 'ready_with_development',
          promotion_readiness_label: 'Ready With Development',
          promotion_rationale: 'Ready once they have run a project alone.' })}
        canEdit={false} />);
    expect(screen.getByText('Ready With Development')).toBeInTheDocument();
    expect(screen.getByText(/run a project alone/i)).toBeInTheDocument();
    expect(screen.queryByRole('radio')).toBeNull();
  });
});

describe('the training plan', () => {
  const withTraining = appraisal({
    training_plans: [
      { id: 't-1', title: 'ISO 27001', kind: 'certification',
        kind_label: 'Certification', priority: 'high', priority_label: 'High',
        status: 'identified', status_label: 'Identified', target_period: 'Q2',
        justification: 'Required for the audit.', mentor_name: '',
        decided_by_name: '', decision_note: '' },
      { id: 't-2', title: 'Shadow the close', kind: 'mentorship',
        kind_label: 'Mentorship', priority: 'medium', priority_label: 'Medium',
        status: 'approved', status_label: 'Approved', target_period: '',
        justification: '', mentor_name: 'Sita Rai',
        decided_by_name: 'HR Manager', decision_note: 'Agreed for Q3.' },
    ],
  });

  it('names the TYPE of every line, not just its title', () => {
    // Before `kind` existed a mentoring pairing could only be expressed by
    // writing the word in the title, and HR's training list silently included
    // things nobody had to fund.
    renderPanel(<TrainingPlanPanel appraisal={withTraining} />);
    expect(screen.getByText('Certification')).toBeInTheDocument();
    expect(screen.getByText('Mentorship')).toBeInTheDocument();
  });

  it('shows the mentor on a mentorship line', () => {
    renderPanel(<TrainingPlanPanel appraisal={withTraining} />);
    expect(screen.getByText(/Mentor: Sita Rai/)).toBeInTheDocument();
  });

  it('offers all four kinds when raising a need', async () => {
    const user = userEvent.setup();
    renderPanel(<TrainingPlanPanel appraisal={appraisal()} canManage />);
    await user.click(screen.getByRole('button', { name: /add training need/i }));
    const select = screen.getByLabelText(/^type$/i);
    expect(within(select).getAllByRole('option').map((o) => o.textContent))
      .toEqual(['Training', 'Certification', 'Mentorship', 'On-the-job']);
  });

  it('reveals the mentor field only for a mentorship', async () => {
    // The server refuses a mentor on any other kind, so offering the field
    // there would be offering a control that 400s.
    const user = userEvent.setup();
    renderPanel(<TrainingPlanPanel appraisal={appraisal()} canManage />);
    await user.click(screen.getByRole('button', { name: /add training need/i }));

    expect(screen.queryByLabelText(/mentor/i)).toBeNull();
    await user.selectOptions(screen.getByLabelText(/^type$/i), 'mentorship');
    expect(screen.getByLabelText(/mentor/i)).toBeInTheDocument();
  });

  it('names a mentor through the search-gated directory, not a roster dropdown',
    async () => {
      // A dropdown of everybody would defeat the three protections the
      // directory endpoint has — a two-character gate, a cap and a throttle —
      // which exist so it cannot be used to walk the roster.
      const user = userEvent.setup();
      appraisalService.searchPeople.mockResolvedValue([
        { id: 'u-9', full_name: 'Sita Rai', designation: 'Analyst' }]);
      renderPanel(<TrainingPlanPanel appraisal={appraisal()} canManage />);

      await user.click(screen.getByRole('button', { name: /add training need/i }));
      await user.type(screen.getByLabelText(/what is needed/i),
        'Shadow the close');
      await user.selectOptions(screen.getByLabelText(/^type$/i), 'mentorship');
      await user.type(screen.getByLabelText(/mentor/i), 'Sita');

      await user.click(await screen.findByRole('button', { name: /Sita Rai/ }));
      // Echoed back in words: a picker that shows only an id, or clears itself
      // after choosing, is how somebody names the wrong colleague.
      expect(screen.getByText(/Chosen: Sita Rai/)).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: /save training need/i }));
      expect(appraisalService.addTrainingNeed).toHaveBeenCalledWith('a-1',
        expect.objectContaining({ kind: 'mentorship', mentor: 'u-9' }));
    });

  it('sends no mentor when the kind is not a mentorship', async () => {
    const user = userEvent.setup();
    renderPanel(<TrainingPlanPanel appraisal={appraisal()} canManage />);
    await user.click(screen.getByRole('button', { name: /add training need/i }));
    await user.type(screen.getByLabelText(/what is needed/i), 'Advanced Excel');
    await user.selectOptions(screen.getByLabelText(/^type$/i), 'certification');
    await user.click(screen.getByRole('button', { name: /save training need/i }));

    expect(appraisalService.addTrainingNeed).toHaveBeenCalledWith('a-1',
      expect.objectContaining({ kind: 'certification', mentor: null }));
  });

  it('offers the decision only to whoever the server says may decide', () => {
    renderPanel(<TrainingPlanPanel appraisal={withTraining} canDecide={false} />);
    expect(screen.queryByRole('button', { name: /decide on/i })).toBeNull();
  });

  it('names the item in each decision control', async () => {
    // A plan can hold six lines. Six buttons called "Decide" is six identical
    // announcements to a screen reader.
    renderPanel(<TrainingPlanPanel appraisal={withTraining} canDecide />);
    expect(screen.getByRole('button', { name: /decide on .*ISO 27001/i }))
      .toBeInTheDocument();
  });

  it('records a decision with its note', async () => {
    const user = userEvent.setup();
    renderPanel(<TrainingPlanPanel appraisal={withTraining} canDecide />);
    await user.click(screen.getByRole('button',
      { name: /decide on .*ISO 27001/i }));
    await user.selectOptions(screen.getByLabelText(/decision on/i), 'approved');
    await user.type(screen.getByLabelText(/^note$/i), 'Budgeted for Q2.');
    await user.click(screen.getByRole('button', { name: /record decision/i }));

    expect(appraisalService.decideTraining).toHaveBeenCalledWith('a-1', 't-1',
      { status: 'approved', decision_note: 'Budgeted for Q2.' });
  });

  it('shows who decided and why, so a declined request has an owner', () => {
    renderPanel(<TrainingPlanPanel appraisal={withTraining} />);
    expect(screen.getByText('HR Manager')).toBeInTheDocument();
    expect(screen.getByText('Agreed for Q3.')).toBeInTheDocument();
  });
});
