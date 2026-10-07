/**
 * Phase APM-03b completion — the surfaces added after the first pass.
 *
 * My Goals, My Evidence, the sectioned self assessment, the supervisor's
 * completed reviews, the committee's history, and HR's review delays and
 * development plans.
 *
 * The evidence page carries the guard that matters most in this file: it must
 * distinguish a source with NO DATA from a source that is NOT COLLECTED. "No
 * evidence from Leave" and "Leave evidence is not collected on this system" are
 * completely different sentences to have quoted at you in a review, and only
 * one of them is true.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: {
    getDashboard: vi.fn(), getAppraisal: vi.fn(), getEvidence: vi.fn(),
    getAppraisals: vi.fn(), updateAppraisal: vi.fn(),
  },
}));
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u-1' }, role: 'maker' }),
}));

import MyGoals from './MyGoals';
import MyEvidence from './MyEvidence';
import TeamAppraisals from './TeamAppraisals';
import CommitteeQueue from './CommitteeQueue';
import HRAppraisalDashboard from './HRAppraisalDashboard';
import AppraisalCard from '../../components/home/AppraisalCard';
import SelfAssessmentForm from '../../components/appraisal/SelfAssessmentForm';
import { appraisalService } from '../../services/appraisalService';
import { appraisal, DASHBOARD, EVIDENCE, HR, MANAGER } from './testFixtures';

const ALL_SOURCES = [
  { source: 'task', available: true },
  { source: 'memo', available: false },
  { source: 'minute', available: false },
  { source: 'circular', available: false },
  { source: 'attendance', available: false },
  { source: 'leave', available: false },
  { source: 'inventory', available: false },
];

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
  appraisalService.getAppraisal.mockResolvedValue(appraisal());
  appraisalService.getEvidence.mockResolvedValue(
    { ...EVIDENCE, sources: ALL_SOURCES });
  appraisalService.getAppraisals.mockResolvedValue([]);
  appraisalService.updateAppraisal.mockResolvedValue({});
});

describe('My Goals', () => {
  it('shows every field the specification names, plus the goal lifecycle',
    async () => {
    renderPage(<MyGoals />);
    const table = await screen.findByRole('table');
    const headers = within(table).getAllByRole('columnheader')
      .map((h) => h.textContent);
    expect(headers).toEqual(['Goal', 'Status', 'Share of year',
      'Success looks like', 'What happened', 'Progress', 'Evidence', 'Due']);
  });

  it('keeps target and achievement as separate columns', async () => {
    // Overwriting what was agreed with what happened is how a year's
    // objectives quietly become whatever was delivered.
    renderPage(<MyGoals />);
    const table = await screen.findByRole('table');
    expect(within(table).getAllByText('What success looks like').length)
      .toBeGreaterThan(0);
  });

  it('leads with the weight total and names the shortfall', async () => {
    appraisalService.getAppraisal.mockResolvedValue(
      appraisal({ goal_weight_total: 70 }));
    renderPage(<MyGoals />);
    // The page renders the dashboard's shorter goal list first so it is never
    // blank, then the record's fuller one — so wait for the record's total
    // rather than for the table, which arrives a beat earlier.
    expect(await screen.findByText(/add 30% more/))
      .toBeInTheDocument();
    expect(screen.getByRole('note')).toHaveTextContent(
      /need to add up to 100% before you can send them/i);
  });

  it('shows the evidence cited against each objective', async () => {
    appraisalService.getAppraisal.mockResolvedValue(appraisal({
      evidence_references: [{
        id: 'e-1', goal: 'g-Deliver the quarterly return', source: 'task',
        captured_at: '2026-08-31T10:00:00Z', note: '',
        metrics: [{ key: 'tasks_completed', label: 'Tasks Completed',
          value: 9 }],
      }],
    }));
    renderPage(<MyGoals />);
    expect(await screen.findByText(/Tasks Completed: 9/)).toBeInTheDocument();
  });

  it('says so plainly when there is no appraisal open', async () => {
    appraisalService.getDashboard.mockResolvedValue({
      employee: { current_appraisal: null, goals: [] } });
    renderPage(<MyGoals />);
    expect(await screen.findByText(/no appraisal open/i)).toBeInTheDocument();
  });
});

describe('My Evidence', () => {
  it('groups by all seven declared sources, not just the one that works',
    async () => {
      renderPage(<MyEvidence />);
      for (const label of ['Tasks', 'Memos', 'Minutes', 'Circulars',
        'Attendance', 'Leave', 'Inventory']) {
        expect(await screen.findByRole('heading', { name: label, level: 3 }))
          .toBeInTheDocument();
      }
    });

  it('says NOT CURRENTLY TRACKED, never a zero', async () => {
    // The guard this page exists for. Somebody appraised on task activity alone
    // must not read the silence about their memo work as a zero.
    renderPage(<MyEvidence />);
    const leave = (await screen.findByRole('heading',
      { name: 'Leave', level: 3 })).closest('section');
    expect(leave).toHaveTextContent(/not currently tracked/i);
    expect(leave).toHaveTextContent(/not a score of zero/i);
  });

  it('shows the task figures with their denominators', async () => {
    renderPage(<MyEvidence />);
    // Completion and on-time are behind "Show all figures" now, so open it.
    await screen.findByRole('heading', { name: 'Tasks', level: 3 });
    for (const d of document.querySelectorAll('details')) d.open = true;
    expect(await screen.findByText('9 of 12 tasks')).toBeInTheDocument();
    expect(screen.getByText('6 of 8 with a due date')).toBeInTheDocument();
  });

  it('says what has not been added to the appraisal yet', async () => {
    renderPage(<MyEvidence />);
    const tasks = (await screen.findByRole('heading',
      { name: 'Tasks', level: 3 })).closest('section');
    expect(tasks).toHaveTextContent(/Not added to your appraisal yet/i);
    expect(tasks).toHaveTextContent(/You choose what to include/i);
  });

  it('shows what was added, with when it was read', async () => {
    appraisalService.getAppraisal.mockResolvedValue(appraisal({
      evidence_references: [{
        id: 'e-1', goal: null, source: 'task', period_start: '2025-09-01',
        period_end: '2026-08-31', captured_at: '2026-08-31T10:00:00Z',
        attached_by_name: 'Bikash Kadayat', note: 'My best quarter.',
        metrics: [{ key: 'tasks_completed', label: 'Tasks Completed',
          value: 9 }],
      }],
    }));
    renderPage(<MyEvidence />);
    expect(await screen.findByText(/Added to your appraisal/))
      .toBeInTheDocument();
    expect(screen.getByText(/My best quarter/)).toBeInTheDocument();
  });

  it('states that this is activity and not an assessment', async () => {
    renderPage(<MyEvidence />);
    const notes = await screen.findAllByRole('note');
    expect(notes[0]).toHaveTextContent(/record of activity, not an assessment/i);
  });

  it('offers no citing control — that belongs on the record, with a reason',
    async () => {
      renderPage(<MyEvidence />);
      await screen.findByRole('heading', { name: 'Tasks', level: 3 });
      expect(screen.queryByRole('button', { name: /cite/i })).toBeNull();
      expect(screen.getByRole('link', { name: /add to my appraisal/i }))
        .toBeInTheDocument();
    });
});

describe('the sectioned self assessment', () => {
  it('offers the four questions the specification names', () => {
    renderPage(<SelfAssessmentForm appraisal={appraisal()} canWrite />);
    for (const label of ['Achievements', 'Challenges', 'Support Needed',
      'Future Goals']) {
      expect(screen.getByLabelText(new RegExp(`^${label}`, 'i')))
        .toBeInTheDocument();
    }
  });

  it('composes the sections into the one field the server has', async () => {
    const user = userEvent.setup();
    renderPage(<SelfAssessmentForm appraisal={appraisal()} canWrite />);
    await user.type(screen.getByLabelText(/^Achievements/i),
      'Closed the return on time.');
    await user.type(screen.getByLabelText(/^Challenges/i),
      'The migration slipped.');
    await user.click(screen.getByRole('button',
      { name: /save self assessment/i }));

    expect(appraisalService.updateAppraisal).toHaveBeenCalledWith('a-1', {
      self_assessment: '## Achievements\nClosed the return on time.\n\n'
        + '## Challenges\nThe migration slipped.',
    });
  });

  it('never requires a section nobody has anything to say about', async () => {
    // Forced to fill Challenges, somebody invents one — and the invented answer
    // is what ends up in the meeting.
    const user = userEvent.setup();
    renderPage(<SelfAssessmentForm appraisal={appraisal()} canWrite />);
    for (const label of ['Achievements', 'Challenges', 'Support Needed',
      'Future Goals']) {
      expect(screen.getByLabelText(new RegExp(`^${label}`, 'i')))
        .not.toBeRequired();
    }
    // The two that were dropped are not offered on a new self assessment.
    expect(screen.queryByLabelText(/Lessons Learned/i)).toBeNull();
    expect(screen.queryByLabelText(/^Comments/i)).toBeNull();
    await user.type(screen.getByLabelText(/^Achievements/i), 'Only this.');
    await user.click(screen.getByRole('button',
      { name: /save self assessment/i }));
    expect(appraisalService.updateAppraisal).toHaveBeenCalledWith('a-1',
      { self_assessment: '## Achievements\nOnly this.' });
  });

  it('keeps text written before the form had sections, and says where it is',
    () => {
      renderPage(<SelfAssessmentForm
        appraisal={appraisal({
          self_assessment: 'I delivered both objectives.' })} canWrite />);
      const earlier = screen.getByLabelText(/written earlier/i);
      expect(earlier).toHaveValue('I delivered both objectives.');
      expect(screen.getByText(/it is kept either way/i)).toBeInTheDocument();
    });

  it('separates saving from submitting, and says so', async () => {
    // One button that did both would make every half-finished draft a
    // submission to the person's supervisor.
    const user = userEvent.setup();
    renderPage(<SelfAssessmentForm appraisal={appraisal()} canWrite />);
    await user.type(screen.getByLabelText(/^Achievements/i), 'Draft.');
    await user.click(screen.getByRole('button',
      { name: /save self assessment/i }));
    expect(await screen.findByRole('status')).toHaveTextContent(
      /not with your supervisor until you submit it/i);
  });

  it('reads as prose, section by section, to somebody who may not write it',
    () => {
      renderPage(<SelfAssessmentForm
        appraisal={appraisal({
          self_assessment: '## Achievements\nShipped it.\n\n'
            + '## Comments\nThank you.' })} canWrite={false} />);
      expect(screen.getByText('Shipped it.')).toBeInTheDocument();
      expect(screen.getByText('Thank you.')).toBeInTheDocument();
      // Sections with nothing in them are omitted, not shown as empty headings.
      expect(screen.queryByText('Challenges')).toBeNull();
      expect(screen.queryByRole('textbox')).toBeNull();
    });

  it('renders nothing at all when there is no self assessment to read', () => {
    const { container } = renderPage(
      <SelfAssessmentForm appraisal={appraisal()} canWrite={false} />);
    expect(container.querySelector('section')).toBeNull();
  });
});

describe('the supervisor\'s completed reviews', () => {
  it('lists closed appraisals separately from live ones', async () => {
    appraisalService.getDashboard.mockResolvedValue({
      ...DASHBOARD,
      manager: {
        ...MANAGER,
        team: [...MANAGER.team, {
          appraisal_id: 'a-3', employee: 'Mina Gurung', stage: 'Closed',
          stage_index: 9, is_closed: true, goals: 2, goal_weight_total: 100,
          awaiting_me: false }],
      },
    });
    renderPage(<TeamAppraisals />);
    const done = await screen.findByRole('region',
      { name: /completed reviews/i });
    expect(within(done).getByText('Mina Gurung')).toBeInTheDocument();

    const team = screen.getByRole('region', { name: /my team/i });
    expect(within(team).queryByText('Mina Gurung')).toBeNull();
  });

  it('says so plainly when nothing has closed', async () => {
    appraisalService.getDashboard.mockResolvedValue({
      ...DASHBOARD, manager: MANAGER });
    renderPage(<TeamAppraisals />);
    const done = await screen.findByRole('region',
      { name: /completed reviews/i });
    expect(done).toHaveTextContent(/no appraisal on your team has closed yet/i);
  });
});

describe('the committee\'s review history', () => {
  it('separates closed reviews from assigned ones', async () => {
    appraisalService.getAppraisals.mockImplementation(({ scope }) => Promise
      .resolve(scope === 'committee' ? [
        appraisal({ id: 'a-1', employee_name: 'Aarati Shrestha',
          status: 'review_committee', status_label: 'Review Committee' }),
        appraisal({ id: 'a-2', employee_name: 'Zenith Rai', status: 'closed',
          status_label: 'Closed', stage_index: 9 }),
      ] : []));
    renderPage(<CommitteeQueue />);
    const history = await screen.findByRole('region',
      { name: /review history/i });
    expect(within(history).getByText('Zenith Rai')).toBeInTheDocument();

    const assigned = screen.getByRole('region', { name: /assigned reviews/i });
    expect(within(assigned).queryByText('Zenith Rai')).toBeNull();
  });

  it('points at the record for the committee\'s comment rather than inlining it',
    async () => {
      // List rows are shared with the supervisor's and HR's views, so carrying
      // review prose on them would put every committee comment in the system
      // into one HR response.
      appraisalService.getAppraisals.mockResolvedValue([
        appraisal({ id: 'a-2', employee_name: 'Zenith Rai', status: 'closed',
          status_label: 'Closed', stage_index: 9,
          committee_comments: 'Should not be rendered here.' }),
      ]);
      renderPage(<CommitteeQueue />);
      const history = await screen.findByRole('region',
        { name: /review history/i });
      expect(history).toHaveTextContent(/comments are on each record/i);
      expect(within(history).queryByText(/Should not be rendered here/))
        .toBeNull();
    });
});

describe('HR review delays and development plans', () => {
  const withDelays = {
    ...HR,
    longest_wait_days: 14,
    review_delays: [
      { employee: 'Zenith Rai', department: 'Finance',
        stage: 'Supervisor Review', with_whom: 'Sita Rai', days_waiting: 14 },
      { employee: 'Aarati Shrestha', department: 'Engineering',
        stage: 'Review Committee', with_whom: 'Sita Rai', days_waiting: 3 },
    ],
    development_plans: {
      total: 5,
      by_status: [{ status: 'agreed', label: 'Agreed', count: 4 },
        { status: 'completed', label: 'Completed', count: 1 }],
      top_areas: [{ area: 'Presentation skills', count: 3 }],
    },
  };

  beforeEach(() => {
    appraisalService.getDashboard.mockResolvedValue(
      { ...DASHBOARD, hr: withDelays });
  });

  it('orders delays by the WAIT, which ranks records and not people',
    async () => {
      renderPage(<HRAppraisalDashboard />);
      const section = await screen.findByRole('region',
        { name: /review delays/i });
      const names = within(section).getAllByRole('rowheader')
        .map((c) => c.textContent);
      expect(names).toEqual(['Zenith Rai', 'Aarati Shrestha']);
      expect(section).toHaveTextContent(/measures the process, not the people/i);
    });

  it('names who each delayed record is with', async () => {
    // A delay nobody owns is a delay nobody clears.
    renderPage(<HRAppraisalDashboard />);
    const section = await screen.findByRole('region',
      { name: /review delays/i });
    expect(within(section).getAllByText('Sita Rai')).toHaveLength(2);
  });

  it('counts development actions by AREA and names nobody', async () => {
    renderPage(<HRAppraisalDashboard />);
    // "Growth Plans" since Phase UI-PRODUCTION-V1 — one name per concept, and
    // the employee-facing surfaces already called it that.
    const section = await screen.findByRole('region',
      { name: /growth plans/i });
    expect(within(section).getByText(/Presentation skills — 3/))
      .toBeInTheDocument();
    expect(within(section).queryByText('Zenith Rai')).toBeNull();
    expect(section).toHaveTextContent(/nobody is named/i);
  });
});

describe('the Home card', () => {
  it('shows three tiles, not six', async () => {
    // A home-page card is read in about two seconds. Six figures is a
    // dashboard, and a dashboard is what this module was accused of being.
    appraisalService.getDashboard.mockResolvedValue(DASHBOARD);
    renderPage(<AppraisalCard />);
    await screen.findByText('FY 2083/84 Annual');

    for (const label of ['My Review', 'Goals', 'Review Due']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    for (const gone of ['Progress', 'Objective Weight', 'Development Actions',
      'Training Requests']) {
      expect(screen.queryByText(gone), gone).toBeNull();
    }
  });
});
