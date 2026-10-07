/**
 * Phase APM-03b — the supervisor and committee workspaces.
 *
 * The two guards worth having here are about SCOPE and about ORDER. A
 * supervisor must see their direct reports and nobody else's; a committee
 * member must see the appraisals they were placed on and nothing more. And
 * neither list may be reordered by anything about the people in it.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: { getDashboard: vi.fn(), getAppraisals: vi.fn() },
}));

import TeamAppraisals from './TeamAppraisals';
import CommitteeQueue from './CommitteeQueue';
import { appraisalService } from '../../services/appraisalService';
import { appraisal, DASHBOARD, MANAGER } from './testFixtures';

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
  appraisalService.getDashboard.mockResolvedValue({
    ...DASHBOARD, manager: MANAGER });
  appraisalService.getAppraisals.mockResolvedValue([]);
});

describe('the supervisor workspace', () => {
  it('separates what is waiting on the supervisor from the rest of the team',
    async () => {
      renderPage(<TeamAppraisals />);
      const pending = (await screen.findByRole('region',
        { name: /pending reviews/i }));
      expect(within(pending).getByText('Aarati Shrestha')).toBeInTheDocument();
      expect(within(pending).queryByText('Zenith Rai')).toBeNull();

      const team = screen.getByRole('region', { name: /my team/i });
      expect(within(team).getByText('Zenith Rai')).toBeInTheDocument();
    });

  it('keeps the server order and offers no way to sort by anything', async () => {
    // The team arrives alphabetically with the LEAST advanced appraisal first.
    // A sort control, or any reordering, would put Zenith above Aarati.
    renderPage(<TeamAppraisals />);
    await screen.findByText('Aarati Shrestha');
    const headers = screen.getAllByRole('columnheader');
    for (const header of headers) {
      expect(within(header).queryByRole('button')).toBeNull();
    }
  });

  it('flags an objective set whose weights do not total 100', async () => {
    renderPage(<TeamAppraisals />);
    expect(await screen.findByText(/weights total 60%/i)).toBeInTheDocument();
  });

  it('aggregates development needs by AREA and names nobody', async () => {
    renderPage(<TeamAppraisals />);
    const section = await screen.findByRole('region',
      { name: /development needs across the team/i });
    expect(within(section).getByText(/Presentation skills — 2/))
      .toBeInTheDocument();
    expect(within(section).queryByText('Aarati Shrestha')).toBeNull();
  });

  it('says so plainly when nobody reports to the caller', async () => {
    appraisalService.getDashboard.mockResolvedValue(DASHBOARD);
    renderPage(<TeamAppraisals />);
    expect(await screen.findByText(/nobody reports to you/i))
      .toBeInTheDocument();
  });

  it('shows no scoring or ranking vocabulary anywhere', async () => {
    const { container } = renderPage(<TeamAppraisals />);
    await screen.findByText('Aarati Shrestha');
    const text = container.textContent.toLowerCase();
    for (const word of ['score', 'rank', 'leaderboard', 'top performer',
      'percentile', 'grade']) {
      expect(text, `team reviews must not say "${word}"`).not.toContain(word);
    }
  });
});

describe('the committee workspace', () => {
  const assigned = [
    appraisal({ id: 'a-1', employee_name: 'Aarati Shrestha',
      status: 'review_committee', status_label: 'Review Committee',
      stage_index: 5 }),
    appraisal({ id: 'a-2', employee_name: 'Zenith Rai',
      status: 'goal_setting', status_label: 'Goal Setting', stage_index: 1 }),
  ];

  it('shows only the appraisals the committee member was placed on', async () => {
    appraisalService.getAppraisals.mockImplementation(({ scope }) => Promise
      .resolve(scope === 'committee' ? assigned : []));
    renderPage(<CommitteeQueue />);
    await screen.findByText('Aarati Shrestha');
    expect(appraisalService.getAppraisals)
      .toHaveBeenCalledWith({ scope: 'committee' });
  });

  it('does NOT pull the caller\'s supervisor work into the committee queue',
    async () => {
      // `needs_me` also covers the stages somebody owns as a SUPERVISOR, and
      // those belong on Team Reviews. A committee queue that quietly included
      // them would be a second, differently ordered copy of another page.
      appraisalService.getAppraisals.mockImplementation(({ scope }) => Promise
        .resolve(scope === 'committee'
          ? assigned
          : [appraisal({ id: 'a-9', employee_name: 'Supervised Person' })]));
      renderPage(<CommitteeQueue />);
      await screen.findByText('Aarati Shrestha');
      expect(screen.queryByText('Supervised Person')).toBeNull();
    });

  it('states that there is no curve, quota or forced distribution', async () => {
    // Calibration in this system means reading across teams, not moderating
    // anybody's rating to fit a shape.
    appraisalService.getAppraisals.mockResolvedValue(assigned);
    renderPage(<CommitteeQueue />);
    expect(await screen.findByText(/no curve, quota or forced distribution/i))
      .toBeInTheDocument();
  });

  it('shows no aggregate of the queue for the committee to balance', async () => {
    // Checked by looking for the THING rather than the word: the page's own
    // disclaimer contains "forced distribution", and a guard that fires on a
    // sentence denying the behaviour is a guard the next person loosens rather
    // than tightens. What must not exist is a summary figure — a percentage, a
    // mean, a tally the committee could feel obliged to balance.
    appraisalService.getAppraisals.mockResolvedValue(assigned);
    const { container } = renderPage(<CommitteeQueue />);
    await screen.findByText('Aarati Shrestha');

    expect(container.querySelector('.memo-tiles')).toBeNull();
    expect(container.textContent).not.toMatch(/\d+\s?%/);
    const text = container.textContent.toLowerCase();
    // "rank" is deliberately absent from this list: the table's own caption
    // says "Not ranked", and a word-match that fires on the denial is the same
    // mistake as the one above.
    for (const word of ['moderat', 'average', 'score']) {
      expect(text, `calibration must not say "${word}"`).not.toContain(word);
    }
  });
});
