import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../../hooks/useAuth', () => ({ useAuth: () => ({ role: 'maker', user: {} }) }));
const platform = vi.hoisted(() => ({
  transferTicket: vi.fn(), linkTicket: vi.fn(), createTicketTask: vi.fn(), updateSupport: vi.fn(),
  createKnownIssue: vi.fn(), updateKnownIssue: vi.fn(), supportSla: vi.fn(), setTeamMember: vi.fn(),
  updateSupportTeam: vi.fn(), createSupportTeam: vi.fn(), successCampaigns: vi.fn(), createCampaign: vi.fn(),
  successOperations: vi.fn(),
}));
vi.mock('../../../services/platformService', () => ({ platformService: platform }));
const support = vi.hoisted(() => ({ assist: vi.fn(), deflected: vi.fn(), assistShown: vi.fn() }));
vi.mock('../../../services/supportService', () => ({ supportService: support }));

const { default: TicketOps } = await import('./TicketOps');
const { default: SlaBoard } = await import('./SlaBoard');
const { default: Teams } = await import('./Teams');
const { default: KnownIssues } = await import('./KnownIssues');
const { default: Campaigns } = await import('../success/Campaigns');
const { default: Executive } = await import('../success/Executive');
const { default: SupportAssistant } = await import('../../help/SupportAssistant');

const ok = (data = {}) => Promise.resolve({ data });
beforeEach(() => {
  Object.values(platform).forEach((f) => { f.mockReset(); f.mockImplementation(() => ok()); });
  Object.values(support).forEach((f) => f.mockReset());
});

const TEAMS = [
  { id: 'tb', key: 'billing', name: 'Billing Team', is_active: true, is_default: false, auto_assign: true,
    description: 'Payments', categories: ['billing'], open: 2, unassigned: 1, overdue: 1,
    members: [{ id: 'm1', user: 'u1', name: 'Gita', role: 'lead', is_available: true, open_tickets: 2 }] },
  { id: 'tt', key: 'technical', name: 'Technical Team', is_active: true, is_default: false, auto_assign: true,
    description: 'Bugs', categories: ['bug'], open: 0, unassigned: 0, overdue: 0, members: [] },
];
const TICKET = {
  id: 't1', team: 'tb', team_name: 'Billing Team', assigned_to_name: 'Gita', escalation_level: 1,
  escalated_at: '2026-10-07T10:00:00Z', organization_slug: 'abcschool', known_issue: null,
  linked: [{ id: 't2', reference: 'SUP-000002', subject: 'Charged twice', status_display: 'Open' }],
  tasks: [{ id: 'k1', kind_display: 'Payment verification', title: 'Check the bank slip', status: 'open',
    status_display: 'Open', due_date: '2026-10-09', assigned_to_name: 'Gita', overdue: false }],
  customer: { slug: 'abcschool', name: 'ABC School', health: 41, band: 'at_risk', band_label: 'Needs attention',
    plan: 'Monthly', subscription_status: 'active', open_tickets: 2, reasons: [{ code: 'inactive', text: 'Nobody active for 15 days' }],
    timeline: [{ at: '2026-10-07T09:00:00Z', kind: 'usage', title: 'Usage in October 2026 so far', detail: '12 active person-days' }] },
};

describe('ticket operations', () => {
  it('shows ownership, links, tasks and the customer story', () => {
    render(<MemoryRouter><TicketOps t={TICKET} teams={TEAMS} issues={[]} onChanged={vi.fn()} /></MemoryRouter>);
    expect(screen.getByText('Billing Team')).toBeInTheDocument();
    expect(screen.getByText(/Escalation L1/)).toBeInTheDocument();
    expect(screen.getByText(/SUP-000002/)).toBeInTheDocument();
    expect(screen.getByText(/Payment verification: Check the bank slip/)).toBeInTheDocument();
    expect(screen.getByLabelText('Health 41 of 100, Needs attention')).toBeInTheDocument();
    expect(screen.getByText(/Nobody active for 15 days/)).toBeInTheDocument();
    expect(screen.getByText('Usage in October 2026 so far')).toBeInTheDocument();
    // Transfer offers the other teams only.
    const select = screen.getByLabelText('Transfer to team');
    expect(within(select).queryByText('Billing Team')).toBeNull();
  });

  it('transfers, links, adds a task and opens a known issue', async () => {
    const changed = vi.fn();
    render(<MemoryRouter><TicketOps t={TICKET} teams={TEAMS} issues={[]} onChanged={changed} /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText('Transfer to team'), { target: { value: 'tt' } });
    fireEvent.change(screen.getByLabelText('Transfer note'), { target: { value: 'calculation bug' } });
    fireEvent.click(screen.getByRole('button', { name: /Transfer$/ }));
    await waitFor(() => expect(platform.transferTicket).toHaveBeenCalledWith('t1', { team: 'tt', note: 'calculation bug' }));

    fireEvent.change(screen.getByLabelText('Ticket to link'), { target: { value: 'SUP-000009' } });
    fireEvent.click(screen.getByRole('button', { name: /^Link$/ }));
    await waitFor(() => expect(platform.linkTicket).toHaveBeenCalledWith('t1', 'SUP-000009'));
    fireEvent.click(screen.getByRole('button', { name: 'Unlink SUP-000002' }));
    await waitFor(() => expect(platform.linkTicket).toHaveBeenCalledWith('t1', 't2', true));

    fireEvent.change(screen.getByLabelText('Task type'), { target: { value: 'domain_setup' } });
    fireEvent.click(screen.getByRole('button', { name: /Add task/ }));
    await waitFor(() => expect(platform.createTicketTask).toHaveBeenCalledWith('t1', { kind: 'domain_setup', title: '' }));

    fireEvent.click(screen.getByRole('button', { name: /New known issue from this ticket/ }));
    await waitFor(() => expect(platform.createKnownIssue).toHaveBeenCalledWith({ from_ticket: 't1' }));
    expect(changed).toHaveBeenCalled();
  });
});

describe('SLA board', () => {
  it('shows each bucket and opens a ticket', async () => {
    const board = {
      critical_due: { count: 1, tickets: [{ id: 'c1', reference: 'SUP-000601', subject: 'Nothing loads', priority: 'critical',
        organization: 'ABC School', team: 'Technical Team', assigned_to: null, minutes_left: 45, escalation_level: 0 }] },
      overdue: { count: 0, tickets: [] }, escalated: { count: 0, tickets: [] },
      waiting_team: { count: 3, tickets: [] }, waiting_customer: { count: 2, tickets: [] },
      unassigned: { count: 1, tickets: [] }, resolved_today: { count: 4, tickets: [] },
      sla_met_30d: { percent: 90, on_time: 9, resolved: 10 }, rules: [{ priority: 'critical', hours: 4 }],
      by_team: [{ id: 'tt', name: 'Technical Team', open: 3, overdue: 0, unassigned: 1 }],
    };
    platform.supportSla.mockResolvedValue({ data: board });
    const open = vi.fn();
    render(<SlaBoard teams={TEAMS} onOpen={open} />);
    expect(await screen.findByText(/SLA met 90%/)).toBeInTheDocument();
    expect(screen.getByText(/critical after 4h/)).toBeInTheDocument();
    const summary = screen.getByLabelText('SLA summary');
    expect(within(summary).getByText('Resolved today').previousSibling).toHaveTextContent('4');
    expect(within(summary).getByText('Waiting on team').previousSibling).toHaveTextContent('3');
    expect(screen.getByText('45m left')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /SUP-000601/ }));
    expect(open).toHaveBeenCalledWith('c1');
    fireEvent.change(screen.getByLabelText('Team'), { target: { value: 'tb' } });
    await waitFor(() => expect(platform.supportSla).toHaveBeenLastCalledWith('tb'));
  });
});

describe('teams', () => {
  it('lists members and manages them', async () => {
    const changed = vi.fn(() => Promise.resolve());
    const agents = [{ id: 'u1', name: 'Gita', username: 'gita', open_tickets: 2, teams: [{ name: 'Billing Team', role: 'lead' }] },
      { id: 'u2', name: 'Ram', username: 'ram', open_tickets: 0, teams: [] }];
    render(<Teams teams={TEAMS} agents={agents} onChanged={changed} />);
    const billing = screen.getByRole('article', { name: 'Billing Team' });
    expect(within(billing).getByText(/Owns:/).parentElement).toHaveTextContent('Billing');
    fireEvent.click(within(billing).getByRole('button', { name: 'Mark away' }));
    await waitFor(() => expect(platform.setTeamMember).toHaveBeenCalledWith('tb', { user: 'u1', is_available: false }));
    fireEvent.change(within(billing).getByLabelText('Add agent to Billing Team'), { target: { value: 'u2' } });
    fireEvent.click(within(billing).getByRole('button', { name: /Add/ }));
    await waitFor(() => expect(platform.setTeamMember).toHaveBeenCalledWith('tb', { user: 'u2' }));
    const tech = screen.getByRole('article', { name: 'Technical Team' });
    expect(within(tech).getByText(/Nobody yet/)).toBeInTheDocument();
    expect(screen.getByText(/Billing Team \(lead\)/)).toBeInTheDocument();
  });
});

describe('known issues', () => {
  it('marks an issue fixed and resolves its tickets', async () => {
    const issue = { id: 'i1', title: 'Blank PDF exports', state: 'workaround', state_display: 'Workaround available',
      is_public: true, tickets: 3, organizations: 2, open_tickets: 2, deflected: 5, symptoms: 'pdf blank',
      workaround: 'Use Chrome for now.', updated_at: '2026-10-07T10:00:00Z' };
    render(<KnownIssues issues={[issue]} onChanged={() => Promise.resolve()} />);
    expect(screen.getByText(/3 tickets from 2 customers · 2 open · answered 5 before a ticket/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mark fixed & resolve 2 tickets' }));
    await waitFor(() => expect(platform.updateKnownIssue).toHaveBeenCalledWith('i1', { state: 'fixed', resolve_linked: true }));
  });

  it('says what to do when there are none', () => {
    render(<KnownIssues issues={[]} onChanged={() => Promise.resolve()} />);
    expect(screen.getByText('No known issues')).toBeInTheDocument();
  });
});

describe('campaigns', () => {
  it('previews a segment and launches one task per customer', async () => {
    platform.successCampaigns.mockImplementation((segment) => ok(segment
      ? { organizations: [{ slug: 'abcschool', name: 'ABC School', health: 62, reason: 'Subscription ends in 10 days' }] }
      : { segments: [{ key: 'near_renewal', label: 'Near renewal', count: 1, default_title: 'Renewal conversation' }],
        campaigns: [{ id: 'c1', name: 'Trial customers — 1 Oct', segment_display: 'Trial customers', task_title: 'Trial check-in',
          assigned_to_name: null, created_at: '2026-10-01T00:00:00Z', tasks: 4, done: 1, open: 3, percent: 25 }] }));
    platform.createCampaign.mockResolvedValue({ data: { name: 'Near renewal — 7 Oct', created: 1, skipped: 0 } });
    render(<MemoryRouter><Campaigns agents={[{ id: 'u1', name: 'Gita' }]} /></MemoryRouter>);
    expect(await screen.findByText(/1\/4 done · 3 open/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /Near renewal/ }));
    expect(await screen.findByText('ABC School')).toBeInTheDocument();
    expect(screen.getByLabelText('Task title')).toHaveValue('Renewal conversation');
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: 'u1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Open 1 outreach task' }));
    await waitFor(() => expect(platform.createCampaign).toHaveBeenCalledWith(expect.objectContaining({
      segment: 'near_renewal', task_title: 'Renewal conversation', assigned_to: 'u1', task_kind: 'outreach' })));
    expect(await screen.findByText(/1 task opened/)).toBeInTheDocument();
  });
});

describe('founder view', () => {
  it('shows revenue, customers, support and the renewal pipeline', async () => {
    platform.successOperations.mockResolvedValue({ data: {
      currency: 'NPR', mrr_minor: 1500000, arr_minor: 18000000, paying_customers: 6, active_customers: 9,
      at_risk_customers: 2, open_tickets: 7, escalated_open: 1, satisfaction: { average: 4.6, count: 12 },
      deflection_rate_30d: 30, tickets_deflected_30d: 3, assist_shown_30d: 10, tickets_30d: 20,
      renewal_pipeline: { 30: { count: 2, value_minor: 3000000, unpriced: 0 }, 60: { count: 3, value_minor: 4500000 },
        90: { count: 3, value_minor: 4500000 } },
      renewals_upcoming: [{ slug: 'abcschool', name: 'ABC School', status: 'active', plan: 'Monthly',
        ends_on: '2026-10-20', days_left: 13, value_minor: 1500000 }],
      bands: { healthy: 5, watch: 2, onboarding: 0, at_risk: 2 }, at_risk: [], top_active: [],
      support_load: { open: 7, critical: 1, overdue: 2, waiting_customer: 1 }, overdue_tasks: 0, average_health: 68,
    } });
    render(<MemoryRouter><Executive /></MemoryRouter>);
    expect(await screen.findByText('NPR 15,000')).toBeInTheDocument();
    expect(screen.getByText('NPR 180,000')).toBeInTheDocument();
    const headline = screen.getByLabelText('Founder view');
    expect(within(headline).getByText('At-risk customers').nextSibling).toHaveTextContent('2');
    expect(within(headline).getByText('Open tickets').nextSibling).toHaveTextContent('7');
    expect(within(headline).getByText('4.6/5')).toBeInTheDocument();
    expect(within(headline).getByText('30%')).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /60 days 3 NPR 45,000/ })).toBeInTheDocument();
    expect(screen.getByText(/13d\) · NPR 15,000/)).toBeInTheDocument();
  });
});

describe('support assistant 3.0', () => {
  it('leads with a known-issue fix, counts "shown" once, and credits the issue', async () => {
    support.assist.mockResolvedValue({
      similar_tickets: [], known_issues: [],
      known_solutions: [{ id: 'i1', title: 'Late marks off by an hour', state_display: 'Workaround available',
        workaround: 'Re-save the shift.', resolved_for: 4, score: 0.8 }],
      possible_solution: { source: 'known_issue', id: 'i1', title: 'Late marks off by an hour', text: 'Re-save the shift.', score: 0.8 },
    });
    const solved = vi.fn();
    const { rerender } = render(<MemoryRouter><SupportAssistant subject="everyone late one hour" message="" category="attendance" onSolved={solved} /></MemoryRouter>);
    expect(await screen.findByText('Re-save the shift.', {}, { timeout: 2000 })).toBeInTheDocument();
    expect(screen.getByText('Possible solution')).toBeInTheDocument();
    expect(screen.getByText(/resolved for 4 other customers/)).toBeInTheDocument();
    rerender(<MemoryRouter><SupportAssistant subject="everyone late one hour since" message="" category="attendance" onSolved={solved} /></MemoryRouter>);
    expect(support.assistShown).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: /That solved it/ }));
    expect(solved).toHaveBeenCalledWith('issue:i1');
  });
});
