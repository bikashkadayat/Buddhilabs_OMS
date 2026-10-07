/**
 * Phase APM-03b — the HR cycle dashboard and promotion readiness.
 *
 * The organisation-wide screen is where a ranking is most tempting and most
 * damaging, so these pin the two rules that keep it honest: departments and
 * people are listed in the server's alphabetical order, and the absence of a
 * promotion recommendation is never rendered as a negative.
 */
import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/appraisalService', () => ({
  appraisalService: { getDashboard: vi.fn() },
}));

import HRAppraisalDashboard from './HRAppraisalDashboard';
import { appraisalService } from '../../services/appraisalService';
import { DASHBOARD, HR } from './testFixtures';

const renderPage = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><HRAppraisalDashboard /></MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  vi.clearAllMocks();
  appraisalService.getDashboard.mockResolvedValue({ ...DASHBOARD, hr: HR });
});

describe('cycle progress', () => {
  it('shows completion with the figures it was taken over', async () => {
    renderPage();
    expect(await screen.findByText('40%')).toBeInTheDocument();
    expect(screen.getByText('4 of 10 closed')).toBeInTheDocument();
  });

  it('shows where the round is stuck', async () => {
    renderPage();
    const section = await screen.findByRole('region',
      { name: /pending reviews/i });
    expect(within(section).getByText(/awaiting self assessment/i))
      .toBeInTheDocument();
    expect(within(section).getByText(/with committee/i)).toBeInTheDocument();
  });

  it('lists departments alphabetically, not by completion', async () => {
    // Engineering is 33% and Finance 50%. Sorted by completion, Finance leads;
    // alphabetically Engineering does. A league table of departments becomes a
    // league table of the managers who run them.
    renderPage();
    const section = await screen.findByRole('region',
      { name: /department progress/i });
    const names = within(section).getAllByRole('rowheader')
      .map((cell) => cell.textContent);
    expect(names).toEqual(['Engineering', 'Finance']);
  });
});

describe('training needs', () => {
  it('splits requests by type, because only two of the four cost money',
    async () => {
      renderPage();
      const section = await screen.findByRole('region',
        { name: /training needs/i });
      for (const kind of ['Training', 'Certification', 'Mentorship',
        'On-the-job']) {
        expect(within(section).getByText(kind)).toBeInTheDocument();
      }
    });
});

describe('promotion readiness', () => {
  it('shows the recommendation each person actually received', async () => {
    renderPage();
    const section = await screen.findByRole('region',
      { name: /promotion readiness/i });
    expect(within(section).getAllByText('Development Required').length)
      .toBeGreaterThan(0);
    expect(within(section).getAllByText('Ready').length).toBeGreaterThan(0);
    expect(within(section).getByText('Ready With Development'))
      .toBeInTheDocument();
  });

  it('lists people alphabetically, not best answer first', async () => {
    // Aarati is Development Required and Zenith is Ready. Ordered by readiness
    // Zenith leads; alphabetically Aarati does.
    renderPage();
    const section = await screen.findByRole('region',
      { name: /promotion readiness/i });
    const names = within(section).getAllByRole('rowheader')
      .map((cell) => cell.textContent);
    expect(names).toEqual(['Aarati Shrestha', 'Zenith Rai']);
  });

  it('carries the rationale beside every name', async () => {
    renderPage();
    expect(await screen.findByText(/needs a full cycle owning the close/i))
      .toBeInTheDocument();
    expect(screen.getByText(/operating a grade above for two cycles/i))
      .toBeInTheDocument();
  });

  it('counts only the three recorded answers, never "not considered"',
    async () => {
      // A "Not Considered: 42" tile sits beside the real answers and reads as
      // a fourth verdict on 42 people.
      renderPage();
      const section = await screen.findByRole('region',
        { name: /promotion readiness/i });
      expect(within(section).queryByText(/not considered/i)).toBeNull();
    });

  it('says in words that an absent recommendation is not a negative', async () => {
    renderPage();
    const section = await screen.findByRole('region',
      { name: /promotion readiness/i });
    expect(within(section).getByRole('note')).toHaveTextContent(
      /simply absent — not marked unready/i);
  });
});

describe('succession', () => {
  it('shows what a supervisor recorded and calls it neither a shortlist nor an '
    + 'ordering', async () => {
    renderPage();
    const section = await screen.findByRole('region',
      { name: /succession planning/i });
    expect(within(section).getByText('Finance Manager')).toBeInTheDocument();
    expect(section).toHaveTextContent(/not a shortlist and not an ordering/i);
  });
});

describe('what the organisation-wide screen refuses to show', () => {
  it('has no aggregate of anybody\'s performance', async () => {
    // An "average performance by department" figure is a ranking of
    // departments, and within a month it becomes a ranking of the people in
    // them. There is deliberately no such number to render.
    const { container } = renderPage();
    await screen.findByText('40%');
    const text = container.textContent.toLowerCase();
    for (const phrase of ['performance score', 'average rating',
      'top performer', 'leaderboard', 'percentile', 'performance index']) {
      expect(text, `HR dashboard must not say "${phrase}"`)
        .not.toContain(phrase);
    }
  });

  it('renders nothing organisation-wide when the server sent no HR block',
    async () => {
      appraisalService.getDashboard.mockResolvedValue(DASHBOARD);
      renderPage();
      expect(await screen.findByText(/available to hr and admin only/i))
        .toBeInTheDocument();
    });
});
