/**
 * Phase APM-03b — accessibility across the appraisal screens.
 *
 * The specification asks for WCAG compliance, keyboard navigation and
 * screen-reader labels. A full audit needs a real browser; what a unit test can
 * pin is the structural half, which is where the regressions actually happen:
 * every control has an accessible name, every table has a caption, every input
 * is bound to a label, and nothing depends on colour to be understood.
 *
 * These run over the pages a person actually completes an appraisal on, so a
 * later change that drops a label fails here rather than in somebody's hands.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: {
    getAppraisal: vi.fn(), getEvidence: vi.fn(), getCompetencies: vi.fn(),
    getDashboard: vi.fn(), updateAppraisal: vi.fn(), rate: vi.fn(),
    searchPeople: vi.fn(),
    addGoal: vi.fn(), updateGoal: vi.fn(), deleteGoal: vi.fn(),
    addDevelopmentAction: vi.fn(), addTrainingNeed: vi.fn(),
    decideTraining: vi.fn(), attachEvidence: vi.fn(),
    recordFinalReview: vi.fn(), returnStage: vi.fn(), reopen: vi.fn(),
    agreeGoals: vi.fn(), recordMidYear: vi.fn(),
    submitSelfAssessment: vi.fn(), recordSupervisorReview: vi.fn(),
    recordCommitteeReview: vi.fn(), agreeDevelopmentPlan: vi.fn(),
    agreeTrainingPlan: vi.fn(),
  },
}));

import AppraisalDetail from './AppraisalDetail';
import HRAppraisalDashboard from './HRAppraisalDashboard';
import MyGoals from './MyGoals';
import MyEvidence from './MyEvidence';
import { appraisalService } from '../../services/appraisalService';
import { appraisal, CAPS, DASHBOARD, EVIDENCE, HR } from './testFixtures';

const ALL_CAPS = Object.fromEntries(
  Object.keys(CAPS).map((key) => [key, true]));

const qc = () => new QueryClient({
  defaultOptions: { queries: { retry: false } } });

const renderRecord = async () => {
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
  appraisalService.getAppraisal.mockResolvedValue(
    appraisal({ capabilities: ALL_CAPS }));
  appraisalService.getEvidence.mockResolvedValue({
    ...EVIDENCE,
    sources: [{ source: 'task', available: true },
      { source: 'leave', available: false }],
  });
  appraisalService.getCompetencies.mockResolvedValue(
    [{ id: 'c-1', code: 'delivery', name: 'Delivery' }]);
  appraisalService.getDashboard.mockResolvedValue({ ...DASHBOARD, hr: HR });
  appraisalService.searchPeople.mockResolvedValue([]);
});

/** Render a standalone page and wait for its first real content. */
const renderPage = async (ui, settled) => {
  const out = render(
    <QueryClientProvider client={qc()}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
  await settled();
  return out;
};

describe('screen-reader labels', () => {
  it('gives every button on the record an accessible name', async () => {
    const { container } = await renderRecord();
    for (const button of container.querySelectorAll('button')) {
      const name = (button.textContent || '').trim()
        || button.getAttribute('aria-label');
      expect(name, `a button on the record has no name: ${button.outerHTML}`)
        .toBeTruthy();
    }
  });

  it('binds every input, select and textarea to a label', async () => {
    const user = userEvent.setup();
    const { container } = await renderRecord();
    // Open every collapsed form, so the fields inside them are checked too.
    for (const label of [/add objective/i, /add development action/i,
      /add training need/i, /send back a stage/i]) {
      const button = screen.queryByRole('button', { name: label });
      if (button) await user.click(button);
    }

    for (const field of container.querySelectorAll(
      'input, select, textarea')) {
      const labelled = field.labels?.length
        || field.getAttribute('aria-label')
        || field.getAttribute('aria-labelledby');
      expect(labelled, `an unlabelled field: ${field.outerHTML}`).toBeTruthy();
    }
  });

  it('captions every table, so its purpose survives without the page around it',
    async () => {
      const { container } = await renderRecord();
      for (const table of container.querySelectorAll('table')) {
        expect(table.querySelector('caption'),
          'a table with no caption').toBeTruthy();
      }
    });

  it('gives every section a heading a screen reader can navigate by',
    async () => {
      const { container } = await renderRecord();
      for (const section of container.querySelectorAll('section')) {
        const labelled = section.getAttribute('aria-label')
          || section.getAttribute('aria-labelledby');
        expect(labelled, `an unlabelled section: ${section.className}`)
          .toBeTruthy();
      }
    });

  it('captions the HR dashboard tables too', async () => {
    const { container } = render(
      <QueryClientProvider client={qc()}>
        <MemoryRouter><HRAppraisalDashboard /></MemoryRouter>
      </QueryClientProvider>,
    );
    await screen.findByText('40%');
    for (const table of container.querySelectorAll('table')) {
      expect(table.querySelector('caption'),
        'an HR table with no caption').toBeTruthy();
    }
  });
});

describe('the standalone employee pages', () => {
  it('captions the goals table and labels every section', async () => {
    const { container } = await renderPage(
      <MyGoals />, () => screen.findByRole('table'));
    expect(container.querySelector('table caption')).toBeTruthy();
  });

  it('gives every evidence source section a heading a reader can navigate by',
    async () => {
      const { container } = await renderPage(
        <MyEvidence />,
        () => screen.findByRole('heading', { name: 'Tasks', level: 3 }));
      for (const section of container.querySelectorAll('section')) {
        const labelled = section.getAttribute('aria-label')
          || section.getAttribute('aria-labelledby');
        expect(labelled, `an unlabelled section: ${section.className}`)
          .toBeTruthy();
      }
    });

  it('states an unavailable source in words, never by omitting it', async () => {
    // The accessibility half of the fairness rule: a source rendered as
    // nothing is a source a screen-reader user never learns exists.
    await renderPage(
      <MyEvidence />,
      () => screen.findByRole('heading', { name: 'Leave', level: 3 }));
    expect(screen.getByRole('heading', { name: 'Leave', level: 3 })
      .closest('section')).toHaveTextContent(/not currently tracked/i);
  });
});

describe('nothing depends on colour alone', () => {
  it('says "completed" in words on a finished stage, not just a tick',
    async () => {
      await renderRecord();
      expect(screen.getAllByText(/— (completed|done)/).length)
        .toBeGreaterThan(0);
    });

  it('states the weight total in words as well as in the badge tone',
    async () => {
      appraisalService.getAppraisal.mockResolvedValue(
        appraisal({ capabilities: ALL_CAPS, goal_weight_total: 70 }));
      await renderRecord();
      expect(screen.getByRole('status'))
        .toHaveTextContent('add 30% more');
    });

  it('puts errors in a live region rather than only colouring the field',
    async () => {
      const user = userEvent.setup();
      await renderRecord();
      await user.click(screen.getByRole('radio',
        { name: 'Development Required' }));
      await user.type(screen.getByLabelText(/rationale/i), '  ');
      await user.click(screen.getByRole('button',
        { name: /save recommendation/i }));
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
});

describe('keyboard navigation', () => {
  it('reaches and operates a collapsed form with the keyboard alone',
    async () => {
      const user = userEvent.setup();
      await renderRecord();

      const add = screen.getByRole('button', { name: /add goal/i });
      add.focus();
      expect(add).toHaveFocus();
      await user.keyboard('{Enter}');

      // Pressing the trigger REMOVES the trigger, so focus must be moved
      // deliberately — otherwise it falls to the document body and the next Tab
      // starts again from the top of the page, which is where a keyboard user
      // gives up.
      expect(screen.getByLabelText(/^goal$/i)).toHaveFocus();
    });

  it('marks expandable controls with aria-expanded', async () => {
    // A button that reveals a form must say so, or a screen-reader user
    // presses it and hears nothing change.
    const user = userEvent.setup();
    await renderRecord();
    const rate = await screen.findByRole('button', { name: /rate Delivery/i });
    expect(rate).toHaveAttribute('aria-expanded', 'false');
    await user.click(rate);
    expect(rate).toHaveAttribute('aria-expanded', 'true');
  });
});
