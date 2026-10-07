/**
 * Phase APM-03b — the evidence panel.
 *
 * This is the surface where a fairness failure would do the most damage, so
 * most of these assert an ABSENCE: no scoring, no ranking, no arithmetic on the
 * client, and never a percentage without the denominator it was taken over.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: { getEvidence: vi.fn(), attachEvidence: vi.fn() },
}));

import EvidencePanel from '../../components/appraisal/EvidencePanel';
import { appraisalService } from '../../services/appraisalService';
import { appraisal, EVIDENCE } from './testFixtures';

const renderPanel = async (props) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const out = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><EvidencePanel {...props} /></MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText('Tasks Assigned');
  return out;
};

beforeEach(() => {
  vi.clearAllMocks();
  appraisalService.getEvidence.mockResolvedValue(EVIDENCE);
  appraisalService.attachEvidence.mockResolvedValue({});
});

describe('the seven figures the specification names', () => {
  it('shows assigned, completed, completion, reviews and evidence files',
    async () => {
      await renderPanel({ appraisal: appraisal(), canAttach: false });
      for (const label of ['Tasks Assigned', 'Tasks Completed', 'Completion',
        'On Time', 'Reviews', 'Evidence Files', 'Checklist']) {
        expect(screen.getByText(label)).toBeInTheDocument();
      }
    });

  it('renders exactly the values the server sent, computing none of them',
    async () => {
      await renderPanel({ appraisal: appraisal(), canAttach: false });
      expect(screen.getByText('12')).toBeInTheDocument();
      expect(screen.getByText('9')).toBeInTheDocument();
      // Two metrics are 75%; both render, neither is combined with the other.
      expect(screen.getAllByText('75%')).toHaveLength(2);
    });

  it('never shows a percentage without its denominator', async () => {
    // "75%" over four tasks and over four hundred are not the same claim.
    await renderPanel({ appraisal: appraisal(), canAttach: false });
    expect(screen.getByText('9 of 12 tasks')).toBeInTheDocument();
    expect(screen.getByText('6 of 8 with a due date')).toBeInTheDocument();
    expect(screen.getByText('24 of 30 items')).toBeInTheDocument();
  });

  it('marks a figure the source no longer provides rather than dropping it',
    async () => {
      // A panel that silently loses a row is how somebody ends up appraised
      // against six figures believing they saw seven.
      appraisalService.getEvidence.mockResolvedValue({
        ...EVIDENCE,
        headline: [...EVIDENCE.headline.slice(0, 6), {
          key: 'checklist_completion', label: 'Checklist Completion', unit: '',
          value: null, definition: '', basis_of: null, missing: true }],
      });
      await renderPanel({ appraisal: appraisal(), canAttach: false });
      expect(screen.getByText(/not currently tracked/i))
        .toBeInTheDocument();
    });
});

describe('what the panel refuses to imply', () => {
  it('states that this is activity, not an assessment, before the figures',
    async () => {
      await renderPanel({ appraisal: appraisal(), canAttach: false });
      const notes = screen.getAllByRole('note');
      expect(notes[0]).toHaveTextContent(/record of your work, not a score/i);
      expect(notes[0]).toHaveTextContent(/nothing in this appraisal is computed/i);
    });

  it('shows no score, rating, rank or grade anywhere', async () => {
    // Matched on CONSTRUCTIONS, not bare words. The panel's own reassurance now
    // reads "this is a record of your work, not a score", and a guard that
    // fires on the sentence denying the behaviour is one the next person
    // deletes rather than tightens — which is how the behaviour comes back.
    const { container } = await renderPanel({
      appraisal: appraisal(), canAttach: false });
    const text = container.textContent.toLowerCase();
    for (const phrase of ['your score', 'score of', 'score:', 'overall score',
      'rating of', 'rated ', 'ranked', 'rank of', 'percentile', 'grade of',
      'out of 5', 'out of 10', 'overall performance', 'performance index']) {
      expect(text, `evidence must not say "${phrase}"`).not.toContain(phrase);
    }
    // And structurally: no element is a score badge or a rank column.
    expect(container.querySelector('[class*="score"], [class*="rank"]'))
      .toBeNull();
  });

  it('warns the EMPLOYEE when the volume is too low to read as a pattern',
    async () => {
      // Shown to the person as well as to the reviewer. Somebody should know
      // that about their own numbers before anybody quotes them.
      appraisalService.getEvidence.mockResolvedValue({
        ...EVIDENCE, low_volume: true });
      await renderPanel({ appraisal: appraisal(), canAttach: false });
      expect(screen.getByText(/too few records to read anything into/i))
        .toBeInTheDocument();
    });
});

describe('the employee chooses what is added', () => {
  it('offers no citation control when the server says the caller may not',
    async () => {
      await renderPanel({ appraisal: appraisal(), canAttach: false });
      expect(screen.queryByRole('button', { name: /add to my appraisal/i }))
        .toBeNull();
    });

  it('adds evidence against a chosen goal, with a reason', async () => {
    const user = userEvent.setup();
    await renderPanel({ appraisal: appraisal(), canAttach: true });

    await user.selectOptions(
      screen.getByLabelText(/add against a goal/i),
      'g-Deliver the quarterly return');
    await user.type(screen.getByLabelText(/why this matters/i),
      'Covers the return I was asked about.');
    await user.click(screen.getByRole('button', { name: /add to my appraisal/i }));

    expect(appraisalService.attachEvidence).toHaveBeenCalledWith('a-1', {
      goal: 'g-Deliver the quarterly return',
      note: 'Covers the return I was asked about.',
    });
  });

  it('shows the FROZEN figures of an existing entry, with when it was read',
    async () => {
      // So a number quoted months later can be checked against the day it was
      // true rather than recomputed against tasks that have since moved.
      await renderPanel({
        appraisal: appraisal({
          evidence_references: [{
            id: 'e-1', source: 'task', period_start: '2025-09-01',
            period_end: '2026-08-31', captured_at: '2026-08-31T10:00:00Z',
            attached_by_name: 'Bikash Kadayat', note: 'My best quarter.',
            metrics: [{ key: 'tasks_completed', label: 'Tasks Completed',
              value: 9 }],
          }],
        }),
        canAttach: false,
      });
      expect(screen.getByText(/added to this appraisal/i)).toBeInTheDocument();
      expect(screen.getByText(/Tasks Completed: 9/)).toBeInTheDocument();
      expect(screen.getByText(/My best quarter/)).toBeInTheDocument();
    });
});

describe('degrading', () => {
  it('says so plainly when no evidence source is deployed', async () => {
    appraisalService.getEvidence.mockResolvedValue({
      ...EVIDENCE, available: false, headline: [] });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <EvidencePanel appraisal={appraisal()} canAttach />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/nothing is tracked automatically yet/i))
      .toBeInTheDocument();
    // And the citation control goes with it: nothing to cite.
    expect(screen.queryByRole('button', { name: /add to my appraisal/i }))
      .toBeNull();
  });
});
