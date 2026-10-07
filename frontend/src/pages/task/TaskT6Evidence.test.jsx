/**
 * Phase T6 — the personal performance page and the manager evidence view.
 *
 * The manager view is the page in this whole module where a fairness failure
 * would do the most damage, so most of these assert an ABSENCE: no sort control,
 * server order preserved, denominators rendered, low-volume marked in words.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/taskService', () => ({
  taskService: {
    getEvidence: vi.fn(), getEvidenceSnapshots: vi.fn(), getTeamEvidence: vi.fn(),
    downloadReport: vi.fn(),
  },
}));

import TaskEvidence from './TaskEvidence';
import TaskTeamEvidence from './TaskTeamEvidence';
import { taskService } from '../../services/taskService';

const EVIDENCE = {
  employee_id: 'u1', employee_name: 'Bikash Kadayat',
  period_start: '2025-09-01', period_end: '2026-08-31',
  tasks_assigned: 12, tasks_completed: 9, tasks_open: 3, tasks_overdue: 1,
  completion_percent: 75, completion_of: 12,
  completed_with_due_date: 8, completed_on_time: 6, on_time_percent: 75,
  average_completion_days: 4.5, reviews_performed: 2,
  checklist_items_total: 30, checklist_items_completed: 24,
  evidence_files_submitted: 5, comments_posted: 11,
  low_volume: false, contract_version: '1.0',
  disclaimer: 'Counts and durations describing recorded activity. This is '
    + 'evidence, not an assessment: nothing here is scored, weighted, ranked or '
    + 'compared between people.',
};

const person = (name, extra = {}) => ({
  employee_id: name, employee_name: name, department: 'Engineering',
  tasks_assigned: 20, tasks_completed: 15, tasks_open: 5, tasks_overdue: 1,
  completion_percent: 75, completion_of: 20,
  completed_with_due_date: 12, on_time_percent: 80,
  average_completion_days: 3.2, reviews_performed: 1,
  evidence_files_submitted: 4, comments_posted: 6, low_volume: false, ...extra,
});

const TEAM = {
  period_start: '2025-09-01', period_end: '2026-08-31', contract_version: '1.0',
  note: 'Activity evidence for your reports, ordered alphabetically. These '
    + 'figures are not scored, ranked or weighted, and a row flagged low_volume '
    + 'has too few tasks for its percentages to mean anything.',
  employees: [
    // Alphabetical, and deliberately WORST-first so a metric ordering would
    // visibly differ from what the server sent.
    person('Aarati Shrestha', { tasks_assigned: 2, tasks_completed: 0,
      completion_percent: 0, completion_of: 2, low_volume: true,
      average_completion_days: null }),
    person('Zenith Rai', { tasks_assigned: 40, tasks_completed: 40,
      completion_percent: 100, completion_of: 40 }),
  ],
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
  taskService.getEvidence.mockResolvedValue(EVIDENCE);
  taskService.getEvidenceSnapshots.mockResolvedValue({ snapshots: [] });
  taskService.getTeamEvidence.mockResolvedValue(TEAM);
});

// ---------------------------------------------------------------------------
// Part 4 / 9 — the personal page
// ---------------------------------------------------------------------------
describe('my task performance', () => {
  it('renders the five sections Part 9 names', async () => {
    renderPage(<TaskEvidence />);
    await screen.findByRole('heading', { name: 'Completion', level: 3 });
    for (const heading of ['Completion', 'Timeliness', 'Reviews', 'Evidence']) {
      expect(screen.getByRole('heading', { name: heading, level: 3 }))
        .toBeInTheDocument();
    }
  });

  it('shows Historical Trends when there are snapshots', async () => {
    taskService.getEvidenceSnapshots.mockResolvedValue({
      employee_name: 'Bikash Kadayat',
      snapshots: [{ snapshot_date: '2026-08-30', period_type: 'daily',
        tasks_assigned: 12, tasks_completed: 9, tasks_open: 3, tasks_overdue: 1,
        completion_percent: 75, on_time_percent: 75 }],
    });
    renderPage(<TaskEvidence />);
    expect(await screen.findByRole('heading', { name: 'Historical Trends' }))
      .toBeInTheDocument();
  });

  it('says what it is not before showing a number', async () => {
    renderPage(<TaskEvidence />);
    const notes = await screen.findAllByRole('note');
    expect(notes[0].textContent).toContain('not an assessment');
  });

  it('warns the PERSON when their own figures are too thin to read', async () => {
    /* Somebody should know that about their own numbers before anybody quotes
       them at them. */
    taskService.getEvidence.mockResolvedValue({
      ...EVIDENCE, low_volume: true, tasks_assigned: 2,
    });
    renderPage(<TaskEvidence />);
    expect(await screen.findByText(/Too few tasks to read as a pattern/))
      .toBeInTheDocument();
  });

  it('does not warn when there is enough to read', async () => {
    renderPage(<TaskEvidence />);
    await screen.findByRole('heading', { name: 'Completion', level: 3 });
    expect(screen.queryByText(/Too few tasks to read as a pattern/)).toBeNull();
  });

  it('renders no score, grade or ranking anywhere', async () => {
    renderPage(<TaskEvidence />);
    await screen.findByRole('heading', { name: 'Completion', level: 3 });
    const body = document.body.textContent.toLowerCase()
      .replace(/nothing here is scored, weighted, ranked or compared between people\.?/g, '');
    for (const word of ['rating', 'grade', 'percentile', 'ranked', 'scored']) {
      expect(body).not.toContain(word);
    }
  });
});

// ---------------------------------------------------------------------------
// Part 5 / 8 — the manager view
// ---------------------------------------------------------------------------
describe('team task evidence', () => {
  it('renders rows in the SERVER order and offers no way to re-sort', async () => {
    /* The page where a ranking would be most tempting. The worst performer is
       FIRST, which a league table would never do. */
    renderPage(<TaskTeamEvidence />);
    await screen.findByText('Aarati Shrestha');

    const table = screen.getByRole('table');
    const names = within(table).getAllByRole('row').slice(1)
      .map((r) => r.cells[0].textContent);
    expect(names[0]).toContain('Aarati Shrestha');
    expect(names[1]).toContain('Zenith Rai');

    for (const header of within(table).getAllByRole('columnheader')) {
      expect(within(header).queryByRole('button')).toBeNull();
    }
  });

  it('carries the not-a-ranking note at the top, not as a footnote', async () => {
    renderPage(<TaskTeamEvidence />);
    const notes = await screen.findAllByRole('note');
    expect(notes[0].textContent).toContain('not an assessment');
    expect(notes[0].textContent).toContain('not scored, ranked or weighted');
  });

  it('renders every percentage with the count it was taken over', async () => {
    /* "75%" over four tasks and over four hundred are not the same claim. */
    renderPage(<TaskTeamEvidence />);
    const table = await screen.findByRole('table');
    const row = within(table).getByText('Zenith Rai').closest('tr');
    expect(within(row).getByText('of 40 tasks')).toBeInTheDocument();
    expect(within(row).getByText('of 12 dated')).toBeInTheDocument();
  });

  it('marks a thin row in words, not only with a tint', async () => {
    renderPage(<TaskTeamEvidence />);
    const table = await screen.findByRole('table');
    const row = within(table).getByText('Aarati Shrestha').closest('tr');
    expect(within(row).getByText('too few tasks to read')).toBeInTheDocument();
    expect(row.className).toContain('is-low-volume');
  });

  it('summarises how many rows are too thin to read', async () => {
    renderPage(<TaskTeamEvidence />);
    expect(await screen.findByText(/1 of 2 person has too few tasks/))
      .toBeInTheDocument();
  });

  it('shows a blank rather than zero where there is no data', async () => {
    renderPage(<TaskTeamEvidence />);
    const table = await screen.findByRole('table');
    const row = within(table).getByText('Aarati Shrestha').closest('tr');
    expect(within(row).getByText('—')).toBeInTheDocument();
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
  it('downloads the evidence export through the authenticated client',
    async () => {
      const user = userEvent.setup();
      renderPage(<TaskTeamEvidence />);

      await user.click(await screen.findByRole('button', { name: /CSV/ }));
      expect(taskService.downloadReport)
        .toHaveBeenCalledWith('employee-evidence', 'csv');
    });

  it('names the contract version it is rendering', async () => {
    renderPage(<TaskTeamEvidence />);
    expect(await screen.findByText(/contract v1\.0/)).toBeInTheDocument();
  });

  it('says so when nobody in scope has activity', async () => {
    taskService.getTeamEvidence.mockResolvedValue({ ...TEAM, employees: [] });
    renderPage(<TaskTeamEvidence />);
    expect(await screen.findByText(/Nobody in your scope has task activity/))
      .toBeInTheDocument();
    expect(screen.queryByRole('table')).toBeNull();
  });
});
