import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import HealthScore from './HealthScore';

vi.mock('../../../hooks/useAuth', () => ({ useAuth: () => ({ role: 'maker', user: {} }) }));
const support = vi.hoisted(() => ({ assist: vi.fn(), deflected: vi.fn(), assistShown: vi.fn() }));
vi.mock('../../../services/supportService', () => ({ supportService: support }));
const platform = vi.hoisted(() => ({ organizationTimeline: vi.fn() }));
vi.mock('../../../services/platformService', () => ({ platformService: platform }));

const { default: SupportAssistant } = await import('../../help/SupportAssistant');
const { default: Timeline } = await import('./Timeline');

beforeEach(() => { Object.values(support).forEach((f) => f.mockReset()); platform.organizationTimeline.mockReset(); });

describe('health score', () => {
  it('shows the number, the band and what it is made of', () => {
    render(<HealthScore detailed org={{
      health: 41, band: 'at_risk', band_label: 'Needs attention',
      components: [{ key: 'login', label: 'Login activity', score: 30, weight: 25 },
        { key: 'support', label: 'Support volume', score: 60, weight: 15 }],
    }} />);
    expect(screen.getByLabelText('Health 41 of 100, Needs attention')).toBeInTheDocument();
    expect(screen.getByText('Login activity')).toBeInTheDocument();
    expect(screen.getByText('60')).toBeInTheDocument();
  });
});

describe('support assistant', () => {
  it('offers a past fix and a known issue, and counts "that solved it"', async () => {
    support.assist.mockResolvedValue({
      similar_tickets: [{ id: 't9', reference: 'SUP-000009', subject: 'Check in button missing',
        status: 'resolved', status_display: 'Resolved', is_open: false,
        answer: 'Turn on location access.' }],
      known_issues: [{ component: 'Attendance services', level: 'degraded', message: 'Device sync delayed.' }],
    });
    const solved = vi.fn();
    render(<MemoryRouter><SupportAssistant subject="check in button missing" message="" category="attendance" onSolved={solved} /></MemoryRouter>);
    expect(await screen.findByText(/Turn on location access/, {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByText(/Device sync delayed/)).toBeInTheDocument();
    expect(support.assist).toHaveBeenCalledWith('check in button missing', 'attendance');
    fireEvent.click(screen.getByRole('button', { name: /That solved it/ }));
    expect(solved).toHaveBeenCalled();
  });

  it('stays out of the way until there is something to match', () => {
    render(<MemoryRouter><SupportAssistant subject="" message="" category="" onSolved={() => {}} /></MemoryRouter>);
    expect(screen.queryByLabelText('Suggestions before you send')).toBeNull();
    expect(support.assist).not.toHaveBeenCalled();
  });
});

describe('customer timeline', () => {
  it('lists the customer story newest first', async () => {
    platform.organizationTimeline.mockResolvedValue({ data: [
      { at: '2026-10-07T10:00:00Z', kind: 'ticket_opened', title: 'Support ticket opened', detail: 'SUP-000004', link: '/platform/support' },
      { at: '2026-09-01T10:00:00Z', kind: 'created', title: 'Organization created', detail: 'ABC School', link: '' },
    ] });
    render(<MemoryRouter><Timeline slug="abcschool" /></MemoryRouter>);
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2));
    const items = screen.getAllByRole('listitem').map((li) => li.textContent);
    expect(items[0]).toMatch(/Support ticket opened/);
    expect(items[1]).toMatch(/Organization created/);
  });
});
