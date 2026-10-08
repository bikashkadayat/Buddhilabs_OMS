import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { browserFrom, deviceFrom, osFrom } from '../../utils/supportContext';
import { pageHelp } from '../../components/help/NeedHelp';
import { contextForPath, visibleItems } from '../../components/layout/navConfig';

/**
 * Customer Success Center. What matters to a customer: the context is
 * captured without typing, the KB is offered before a ticket, internal notes
 * never show, satisfaction is asked once resolved, every major page has
 * "Need help?", and Help & Support is one sidebar section.
 */
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role: 'maker', user: {} }) }));
const svc = vi.hoisted(() => ({
  createTicket: vi.fn(), ticket: vi.fn(), reply: vi.fn(), rate: vi.fn(), close: vi.fn(),
  mine: vi.fn(), openFile: vi.fn(), assistShown: vi.fn(), deflected: vi.fn(),
  assist: vi.fn(() => Promise.resolve({ similar_tickets: [], known_issues: [] })),
}));
vi.mock('../../services/supportService', () => ({ supportService: svc }));

const { default: Support } = await import('./Support');
const { default: TicketDetail } = await import('./TicketDetail');

beforeEach(() => Object.values(svc).forEach((fn) => fn.mockReset()));

describe('context capture', () => {
  const chrome = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36';
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
  it('names the browser, OS and device coarsely', () => {
    expect(browserFrom(chrome)).toBe('Chrome 141');
    expect(osFrom(chrome)).toBe('Windows');
    expect(deviceFrom(chrome, 1440)).toBe('Desktop');
    expect(browserFrom(iphone)).toBe('Safari 18');
    expect(osFrom(iphone)).toBe('iOS');
    expect(deviceFrom(iphone, 390)).toBe('Mobile');
  });
});

describe('Need help? on every major page', () => {
  it.each([
    ['/my-attendance', 'attendance', 'attendance'], ['/attendance/records', 'attendance', 'attendance'],
    ['/leave/apply', 'leave', 'leave'], ['/tasks/mine', 'tasks', 'task'],
    ['/settings/subscription', 'billing', 'billing'], ['/settings/domains', 'domains', 'domain'],
  ])('%s → %s help, %s tickets', (path, topic, category) => {
    expect(pageHelp(path)).toEqual({ topic, category });
  });
  it('stays off pages without a topic', () => {
    expect(pageHelp('/')).toBeNull();
    expect(pageHelp('/help/tickets')).toBeNull();
  });
});

describe('Help & Support sidebar section', () => {
  it('is one rail with the seven sections', () => {
    expect(contextForPath('/help/tickets')).toBe('help');
    expect(contextForPath('/help/connect-device')).toBe('help');
    expect(visibleItems('help', 'maker').map((i) => i.label)).toEqual([
      'My tickets', 'Create ticket', 'Knowledge base', 'Feature requests',
      'Product updates', 'System status', 'Contact Buddhi Labs']);
  });
});

describe('Create ticket', () => {
  const renderIt = (url = '/help/contact') => render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/help/contact" element={<Support />} />
        <Route path="/help/tickets/:id" element={<p>ticket page</p>} />
      </Routes>
    </MemoryRouter>,
  );

  it('asks only for a category, a title and a description, and shows what it will attach', () => {
    renderIt();
    expect(screen.getByRole('button', { name: /Create ticket/ })).toBeDisabled();
    expect(screen.getByText(/We’ll attach these details automatically/)).toBeInTheDocument();
    expect(screen.getByText(/Your name, organization and plan/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { pressed: false })).toHaveLength(10);
  });

  it('offers Knowledge Base articles that match the title before sending', () => {
    renderIt();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'apply for leave' } });
    expect(screen.getByRole('link', { name: 'Apply for leave' })).toBeInTheDocument();
  });

  it('sends the category and the page the customer came from, then opens the ticket', async () => {
    svc.createTicket.mockResolvedValue({ id: 't1', detail: 'SUP-000001 is open.' });
    renderIt('/help/contact?category=attendance&from=%2Fmy-attendance');
    expect(screen.getByRole('button', { name: /Attendance/ })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Button gone' } });
    fireEvent.change(screen.getByLabelText(/What happened/), { target: { value: 'It vanished.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create ticket' }));
    await waitFor(() => expect(svc.createTicket).toHaveBeenCalled());
    const [body] = svc.createTicket.mock.calls[0];
    expect(body).toMatchObject({ category: 'attendance', subject: 'Button gone', from: '/my-attendance' });
    expect(await screen.findByText('ticket page')).toBeInTheDocument();
  });
});

describe('Ticket detail', () => {
  const ticket = (over = {}) => ({
    id: 't1', reference: 'SUP-000007', category_display: 'Attendance issue', subject: 'Button gone',
    message: 'It vanished.', status: 'in_progress', status_display: 'In progress',
    submitted_by_name: 'Bikash', created_at: '2026-10-07T10:00:00Z', page: '/my-attendance',
    screenshot: null, attachment: null, can_reply: true, can_rate: false, satisfaction_rating: null,
    messages: [{ id: 'm1', author_kind: 'staff', author_name: 'Gita', body: 'Please refresh.',
      is_internal: false, attachment: null, created_at: '2026-10-07T10:05:00Z' }],
    ...over,
  });
  const renderIt = () => render(
    <MemoryRouter initialEntries={['/help/tickets/t1']}>
      <Routes><Route path="/help/tickets/:id" element={<TicketDetail />} /></Routes>
    </MemoryRouter>,
  );

  it('shows the conversation and a reply box', async () => {
    svc.ticket.mockResolvedValue(ticket());
    renderIt();
    expect(await screen.findByText('Please refresh.')).toBeInTheDocument();
    expect(screen.getByText(/Gita \(support\)/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send reply' })).toBeDisabled();
    expect(screen.queryByText(/How was your support experience/)).toBeNull();
  });

  it('asks how support was once resolved, and sends the rating', async () => {
    svc.ticket.mockResolvedValue(ticket({ status: 'resolved', status_display: 'Resolved', can_rate: true }));
    svc.rate.mockResolvedValue(ticket({ status: 'resolved', can_rate: false, satisfaction_rating: 5 }));
    renderIt();
    expect(await screen.findByText(/How was your support experience/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '5 out of 5' }));
    fireEvent.click(screen.getByRole('button', { name: 'Send rating' }));
    await waitFor(() => expect(svc.rate).toHaveBeenCalledWith('t1', 5, ''));
    expect(await screen.findByText(/Thanks for rating our support 5\/5/)).toBeInTheDocument();
  });
});

describe('assistant knowledge-base ranking', () => {
  it('matches how people describe problems, not just exact article words', async () => {
    const { rankHelp } = await import('../../services/helpContent');
    const slugs = rankHelp('Check in button missing', 'maker').map((a) => a.slug);
    expect(slugs).toEqual(expect.arrayContaining(['checkin-disabled']));
    expect(rankHelp('the and for', 'maker')).toEqual([]);
  });
});
