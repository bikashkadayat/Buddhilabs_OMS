/**
 * Phase T5 — the performance dashboard and the evidence page.
 *
 * THE GUARDRAIL TESTS ARE THE POINT
 * ---------------------------------
 * Phase T5 says: display metrics, do not score, do not rank employees, do not
 * evaluate. On the backend that is enforced by the shape of the payload; here
 * it has to be enforced by the shape of the PAGE — a table with a sort control
 * on "Completion %" is a ranking tool whatever the API returned.
 *
 * So these assert the absence: no employee sort control, rows rendered in the
 * order the server sent, and the evidence page stating what it is not before it
 * shows a single number.
 */
import React from 'react';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/taskService', () => ({
  taskService: {
    getExecutive: vi.fn(), getHealth: vi.fn(), getTrend: vi.fn(),
    getEmployeeAnalytics: vi.fn(), getReviewerAnalytics: vi.fn(),
    getEvidence: vi.fn(), getEvidenceSnapshots: vi.fn(),
    downloadReport: vi.fn(),
  },
}));
// The recharts-backed frames render nothing useful in jsdom and cost seconds;
// the assertions here are about structure and ordering, not pixels.
vi.mock('../../components/analytics/TrendChart', () => ({ default: () => null }));
vi.mock('../../components/analytics/ComparisonBarChart', () => ({ default: () => null }));
vi.mock('../../components/analytics/DistributionPieChart', () => ({ default: () => null }));

import TaskAnalytics from './TaskAnalytics';
import TaskEvidence from './TaskEvidence';
import { taskService } from '../../services/taskService';

const KPI = (key, label, value, higher = true) => ({
  key, label, value, unit: '%', higher_is_better: higher,
  definition: `${label} is defined over a stated denominator, in a sentence long enough to be useful.`,
});

const EXECUTIVE = {
  totals: {
    total: 40, completed: 22, open: 15, overdue: 4, blocked: 2,
    review_backlog: 3, average_completion_days: 5.5, oldest_review_days: 9,
  },
  kpis: [KPI('completion_percent', 'Completion', 55)],
  department_ranking: [
    { department: 'Engineering', position: 1, total: 30, completed: 18,
      overdue: 2, completion_percent: 60, average_resolution_days: 4.2,
      low_volume: false },
    { department: 'Finance', position: 2, total: 2, completed: 2, overdue: 0,
      completion_percent: 100, average_resolution_days: 1.0, low_volume: true },
  ],
};

const HEALTH = {
  kpis: [
    KPI('on_time_percent', 'On-Time Delivery', 72),
    KPI('overdue_percent', 'Overdue', 18, false),
  ],
  inputs: { open: 15, completed: 22, blocked: 2, awaiting_review: 3, total: 40 },
};

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
  taskService.getExecutive.mockResolvedValue(EXECUTIVE);
  taskService.getHealth.mockResolvedValue(HEALTH);
  taskService.getTrend.mockResolvedValue({ period: 'weekly', buckets: [] });
  taskService.getEmployeeAnalytics.mockRejectedValue(new Error('403'));
  taskService.getReviewerAnalytics.mockRejectedValue(new Error('403'));
});

describe('performance dashboard', () => {
  it('shows the headline counts Part 1 names', async () => {
    renderPage(<TaskAnalytics />);
    const tiles = (await screen.findByText('Total Tasks')).closest('.memo-tiles');
    for (const label of ['Total Tasks', 'Completed', 'Open', 'Overdue',
      'Blocked', 'Review Backlog', 'Avg Completion (days)']) {
      expect(within(tiles).getByText(label)).toBeInTheDocument();
    }
  });

  it('renders every KPI with the sentence it is read by', async () => {
    /* A percentage with no stated denominator is the most reliable way to have
       a metric misread in a meeting. */
    renderPage(<TaskAnalytics />);
    const tip = await screen.findByRole('note',
      { name: /On-Time Delivery is defined over a stated denominator/ });
    expect(tip).toBeInTheDocument();
    // Reachable by keyboard, not only by hover.
    expect(tip).toHaveAttribute('tabindex', '0');
  });

  it('says which way is better rather than leaving colour to imply it', async () => {
    renderPage(<TaskAnalytics />);
    await screen.findByText('On-Time Delivery');
    expect(screen.getAllByText('Higher is better').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Lower is better').length).toBeGreaterThan(0);
  });

  it('ranks departments, and marks the ones too small to compare', async () => {
    renderPage(<TaskAnalytics />);
    await screen.findByText('Department Comparison');
    const table = screen.getAllByRole('table').at(-1);
    const finance = within(table).getByText('Finance').closest('tr');
    expect(within(finance).getByText('too few tasks to compare'))
      .toBeInTheDocument();
  });

  it('renders no employee table when the server refused it', async () => {
    /* An employee is refused rather than narrowed, and the page renders
       without the section instead of showing them an error. */
    renderPage(<TaskAnalytics />);
    await screen.findByText('Task Health');
    await waitFor(() => expect(taskService.getEmployeeAnalytics)
      .toHaveBeenCalled());
    expect(screen.queryByText('Employee Metrics')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('renders employee rows in the SERVER order and offers no way to sort them',
    async () => {
      /* A table sorted by completion rate is a ranking whatever the header
         says, and the person at the bottom of it will be asked about it. */
      taskService.getEmployeeAnalytics.mockResolvedValue({
        note: 'Metrics only. These figures are not scored, ranked or weighted.',
        employees: [
          { user_id: '1', name: 'Aarati Shrestha', department: 'ICT',
            assigned: 2, completed: 0, open: 2, overdue: 2,
            completion_percent: 0, average_close_days: null,
            review_delay_days: null },
          { user_id: '2', name: 'Zenith Rai', department: 'ICT',
            assigned: 9, completed: 9, open: 0, overdue: 0,
            completion_percent: 100, average_close_days: 2.1,
            review_delay_days: null },
        ],
      });
      renderPage(<TaskAnalytics />);

      await screen.findByText('Employee Metrics');
      const table = screen.getAllByRole('table').find(
        (t) => within(t).queryByText('Aarati Shrestha'));
      const names = within(table).getAllByRole('row').slice(1)
        .map((r) => r.cells[0].textContent);
      // Alphabetical, exactly as sent — the worst performer is FIRST, which a
      // ranking would never do.
      expect(names).toEqual(['Aarati Shrestha', 'Zenith Rai']);
      // No sort affordance anywhere in the header.
      const headers = within(table).getAllByRole('columnheader');
      for (const header of headers) {
        expect(within(header).queryByRole('button')).toBeNull();
      }
    });

  it('carries the not-a-ranking note beside the employee table', async () => {
    taskService.getEmployeeAnalytics.mockResolvedValue({
      note: 'Metrics only. These figures are not scored, ranked or weighted.',
      employees: [{ user_id: '1', name: 'Aarati Shrestha', department: 'ICT',
        assigned: 1, completed: 1, open: 0, overdue: 0, completion_percent: 100,
        average_close_days: 1, review_delay_days: null }],
    });
    renderPage(<TaskAnalytics />);
    expect(await screen.findByText(/not scored, ranked or weighted/))
      .toBeInTheDocument();
  });

  it('switches trend period through the server', async () => {
    renderPage(<TaskAnalytics />);
    fireEvent.click(await screen.findByRole('button', { name: 'Quarterly' }));
    await waitFor(() => expect(taskService.getTrend)
      .toHaveBeenLastCalledWith({ period: 'quarterly' }));
  });

  /*
   * Rewritten in Phase FIX. These used to assert that a LINK existed with the
   * right href — which is precisely what the bug was: the link existed, pointed
   * at the right URL, and downloaded nothing, because a plain navigation
   * carries no Authorization header on a JWT-authenticated API.
   *
   * A test that checks the presence of a control rather than what it DOES will
   * pass against a control that does nothing. These now click the button and
   * assert the authenticated request was made.
   */
  it('downloads through the authenticated client, in both formats', async () => {
    const user = userEvent.setup();
    renderPage(<TaskAnalytics />);

    await user.click(await screen.findByRole('button', { name: /CSV/ }));
    expect(taskService.downloadReport)
      .toHaveBeenCalledWith('executive', 'csv');

    await user.click(screen.getByRole('button', { name: /PDF/ }));
    expect(taskService.downloadReport)
      .toHaveBeenCalledWith('executive', 'pdf');
  });

  it('offers no plain link that would bypass the auth header', async () => {
    renderPage(<TaskAnalytics />);
    await screen.findByRole('button', { name: /CSV/ });
    for (const link of screen.queryAllByRole('link')) {
      expect(link.getAttribute('href') || '').not.toContain('/api/');
    }
  });
});

describe('evidence page', () => {
  const EVIDENCE = {
    employee_id: 'u1', employee_name: 'Bikash Kadayat',
    period_start: '2025-09-01', period_end: '2026-08-31',
    tasks_assigned: 12, tasks_completed: 9, tasks_open: 3, tasks_overdue: 1,
    completion_percent: 75, completion_of: 12,
    completed_with_due_date: 8, completed_on_time: 6, on_time_percent: 75,
    average_completion_days: 4.5, reviews_performed: 2,
    checklist_items_total: 30, checklist_items_completed: 24,
    evidence_files_submitted: 5, comments_posted: 11,
    disclaimer: 'Counts and durations only. This is evidence of activity, not '
      + 'an assessment: nothing here is scored, weighted or compared between people.',
  };

  beforeEach(() => {
    taskService.getEvidence.mockResolvedValue(EVIDENCE);
    taskService.getEvidenceSnapshots.mockResolvedValue({ snapshots: [] });
  });

  it('states what it is not before it shows a single number', async () => {
    /* Somebody reading their own record deserves to know what it is and is not
       before they read the figures. */
    renderPage(<TaskEvidence />);
    const callout = await screen.findByRole('note');
    expect(callout.textContent).toContain(
      'This is a record of activity, not an assessment');
    // The API's own disclaimer is rendered verbatim inside the callout.
    expect(callout.textContent).toContain(
      'nothing here is scored, weighted or compared between people');
  });

  it('renders every percentage beside its denominator', async () => {
    renderPage(<TaskEvidence />);
    // "Completion" is a section heading AND a tile label since Phase T6 split
    // the page into named sections; scope to the tile.
    await screen.findByRole('heading', { name: 'Completion', level: 3 });
    const tile = screen.getAllByText('Completion')
      .map((el) => el.closest('.memo-tile')).find(Boolean);
    expect(within(tile).getByText('9 of 12 tasks')).toBeInTheDocument();
    const onTime = screen.getByText('On Time').closest('.memo-tile');
    expect(within(onTime).getByText('6 of 8 with a due date')).toBeInTheDocument();
  });

  it('says which tasks the on-time figure excludes', async () => {
    renderPage(<TaskEvidence />);
    expect(await screen.findByText(/Tasks with no due date are not counted/))
      .toBeInTheDocument();
  });

  it('shows a blank average rather than zero when nothing completed', async () => {
    taskService.getEvidence.mockResolvedValue({
      ...EVIDENCE, average_completion_days: null,
    });
    renderPage(<TaskEvidence />);
    await screen.findByText('Avg Completion');
    const tile = screen.getByText('Avg Completion').closest('.memo-tile');
    expect(within(tile).getByText('—')).toBeInTheDocument();
  });

  it('carries no rating, grade or comparison anywhere on the page', async () => {
    /* The only permitted mention of scoring is the disclaimer SAYING there is
       none, so that phrase is removed before the check rather than special-cased
       inside a regex nobody can read. */
    renderPage(<TaskEvidence />);
    await screen.findByRole('heading', { name: 'Completion', level: 3 });
    const body = document.body.textContent.toLowerCase()
      .replace(/nothing here is scored, weighted or compared between people\.?/g, '')
      .replace(/not an assessment/g, '');

    for (const word of ['rating', 'grade', 'percentile', 'ranked', 'scored',
      'above average', 'below average']) {
      expect(body).not.toContain(word);
    }
  });

  it('shows the frozen daily record when there is one', async () => {
    taskService.getEvidenceSnapshots.mockResolvedValue({
      employee_name: 'Bikash Kadayat',
      snapshots: [{
        snapshot_date: '2026-08-30', period_type: 'daily',
        tasks_assigned: 12, tasks_completed: 9,
        tasks_open: 3, tasks_overdue: 1, completion_percent: 75,
        on_time_percent: 75,
      }],
    });
    renderPage(<TaskEvidence />);
    // Renamed to "Historical Trends" in Phase T6 (Part 9's section list).
    expect(await screen.findByRole('heading', { name: 'Historical Trends' }))
      .toBeInTheDocument();
    expect(screen.getByText('2026-08-30')).toBeInTheDocument();
  });
});
