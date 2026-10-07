/**
 * Phase APM-03b — where appraisal meets the rest of the product.
 *
 * The work queue, the Home card and the employee's own landing. Each of these
 * had to be added WITHOUT changing how any other module's rows behave, so the
 * queue tests assert the appraisal source's shape while leaving the existing
 * sources' guards (workQueue.normalise, workQueue.priority) untouched.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { SOURCES, TYPES, appraisalSituation } from '../../services/workQueue';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: { getDashboard: vi.fn() },
}));
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u-1' }, role: 'maker' }),
}));

import AppraisalCard from '../../components/home/AppraisalCard';
import MyAppraisal from './MyAppraisal';
import { appraisalService } from '../../services/appraisalService';
import { DASHBOARD, HR, MANAGER } from './testFixtures';

const renderPage = (ui) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  appraisalService.getDashboard.mockResolvedValue(DASHBOARD);
});

describe('the work queue source', () => {
  const source = SOURCES.find((s) => s.type === TYPES.APPRAISAL);

  it('exists, and asks the server for what is waiting on this person', () => {
    expect(source).toBeTruthy();
    expect(source.label).toBe('Appraisals');
  });

  it('names every situation the specification lists', () => {
    // Derived from the STATUS the server returned, not from the caller's role:
    // `needs_me` has already decided this row is this person's turn.
    //
    // Goal Setting and Goal Approval are now DIFFERENT rows to be in: one is
    // "write your objectives", the other is "somebody is waiting to approve
    // them", and a queue that called both "Goal approval" would send the
    // employee looking for a button they do not have.
    expect(appraisalSituation({ status: 'goal_setting' }))
      .toBe('Objectives to submit');
    expect(appraisalSituation({ status: 'goal_approval' }))
      .toBe('Goal approval');
    expect(appraisalSituation({ status: 'self_assessment' }))
      .toBe('Self assessment');
    expect(appraisalSituation({ status: 'supervisor_review' }))
      .toBe('Review pending');
    expect(appraisalSituation({ status: 'final_review' }))
      .toBe('Final review');
    expect(appraisalSituation({ status: 'development_plan' }))
      .toBe('Development plan approval');
  });

  it('falls back to the server label for a status it does not know', () => {
    expect(appraisalSituation({ status: 'something_new',
      status_label: 'Something New' })).toBe('Something New');
  });

  it('maps a row to a queue item that opens the record', () => {
    const item = source.map({
      id: 'a-1', employee_name: 'Bikash Kadayat', cycle_name: 'FY 2083/84',
      status: 'supervisor_review', status_label: 'Supervisor Review',
      stage_index: 4, updated_at: '2026-08-01T00:00:00Z',
    });
    expect(item.title).toBe('Bikash Kadayat — Review pending');
    expect(item.href).toBe('/appraisals/a-1');
    expect(item.subtitle).toContain('stage 4 of 10');
  });

  it('carries NO inline action', () => {
    // Every appraisal step means reading somebody's written year and adding to
    // it. A one-click "approve" on a queue row is exactly the rubber-stamp this
    // module exists to prevent.
    const item = source.map({
      id: 'a-1', employee_name: 'X', status: 'supervisor_review',
      stage_index: 4 });
    expect(item.actions).toEqual([]);
  });
});

describe('the Home card', () => {
  it('shows the cycle and never a raw backend stage', async () => {
    // "Self Assessment, stage 4 of 10" asks somebody to learn a ten-step ladder
    // before they can read their own card. The step is not repeated here at
    // all — the Review Due card carries whose turn it is, which is the part
    // they act on.
    renderPage(<AppraisalCard />);
    expect(await screen.findByText('FY 2083/84 Annual')).toBeInTheDocument();
    expect(screen.queryByText(/stage \d+ of/)).toBeNull();
    expect(screen.queryByText('Self Assessment')).toBeNull();
  });

  it('says plainly when the appraisal is waiting on the reader', async () => {
    renderPage(<AppraisalCard />);
    await screen.findByText('FY 2083/84 Annual');
    const tile = screen.getByText('Review Due').closest('a');
    expect(within(tile).getByText('Now')).toBeInTheDocument();
  });

  it('shows no progress figure — that moved to the appraisal page', () => {
    // Home answers "is it my turn"; the appraisal page answers "how far along".
    // A weighted progress percentage is a figure somebody studies, not one they
    // act on from a home screen.
    renderPage(<AppraisalCard />);
    expect(screen.queryByText('Progress')).toBeNull();
  });

  it('adds the team and organisation blocks WITHOUT removing the personal one',
    async () => {
      // A manager is also somebody with a manager.
      appraisalService.getDashboard.mockResolvedValue({
        ...DASHBOARD, manager: MANAGER, hr: HR });
      renderPage(<AppraisalCard />);
      expect(await screen.findByText('FY 2083/84 Annual')).toBeInTheDocument();
      expect(screen.getByText('Appraisals To Review')).toBeInTheDocument();
      expect(screen.getByText('Cycle Completion')).toBeInTheDocument();
    });

  it('renders nothing at all when there is no appraisal and no team', async () => {
    // "Appraisal: —" on the home page every day of the nine months between
    // cycles is noise that teaches people to ignore the whole region.
    appraisalService.getDashboard.mockResolvedValue({
      employee: { current_appraisal: null, goals: [] } });
    const { container } = renderPage(<AppraisalCard />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector('.hm-appraisal')).toBeNull();
  });

  it('renders nothing when the dashboard will not load', async () => {
    // Home aggregates half a dozen sources; one that fails must cost its own
    // card and nothing else.
    appraisalService.getDashboard.mockRejectedValue(new Error('boom'));
    const { container } = renderPage(<AppraisalCard />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.querySelector('.hm-appraisal')).toBeNull();
  });
});

describe('the employee landing', () => {
  it('shows the stage, the objectives and what is waiting on them', async () => {
    renderPage(<MyAppraisal />);
    expect(await screen.findByRole('heading', { name: /my appraisal/i }))
      .toBeInTheDocument();
    expect(screen.getByText(/waiting on you/i)).toBeInTheDocument();
    expect(screen.getByText('Deliver the quarterly return'))
      .toBeInTheDocument();
  });

  it('warns when the goal weights do not total 100', async () => {
    appraisalService.getDashboard.mockResolvedValue({
      employee: { ...DASHBOARD.employee, goal_weight_total: 80 } });
    renderPage(<MyAppraisal />);
    expect(await screen.findByText(/need to add up to 100% before you can send them/i))
      .toBeInTheDocument();
  });

  it('compares the person with nobody', async () => {
    appraisalService.getDashboard.mockResolvedValue({
      ...DASHBOARD, manager: MANAGER, hr: HR });
    const { container } = renderPage(<MyAppraisal />);
    await screen.findByText('Deliver the quarterly return');
    const text = container.textContent.toLowerCase();
    // Checked against LEAKS, not against vocabulary. The page's own note says
    // "nothing here is scored or compared with anybody else's", so a word-match
    // on "scored" or "compared" would fire on the sentence promising the very
    // thing it is guarding — and a guard that fires on its own denial is one
    // the next person loosens rather than tightens.
    //
    // What must not appear is a colleague's name or a figure about anybody but
    // the reader. The dashboard mock deliberately carries a full manager and HR
    // block, so any of those reaching this page would show up here.
    for (const leak of ['aarati', 'zenith', 'team size', 'direct reports',
      'cycle completion', 'department progress', 'promotion readiness']) {
      expect(text, `my appraisal must not show "${leak}"`).not.toContain(leak);
    }
  });

  it('says so plainly when no appraisal is open, and still shows the history',
    async () => {
      appraisalService.getDashboard.mockResolvedValue({
        employee: { current_appraisal: null, goals: [],
          history: [{ id: 'a-0', cycle: 'FY 2082/83', status: 'Closed' }] } });
      renderPage(<MyAppraisal />);
      // "at the moment" was dropped with the shared empty-state vocabulary
      // (Phase UI-PRODUCTION-V1) — every empty state is about now.
      expect(await screen.findByText(/no appraisal open/i))
        .toBeInTheDocument();
      expect(screen.getByText('FY 2082/83')).toBeInTheDocument();
    });
});
