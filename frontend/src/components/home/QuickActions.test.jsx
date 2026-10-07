import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import QuickActions from './QuickActions';

/**
 * Start something (Phase 204A, redesigned).
 *
 * The things worth pinning: the whole card is one link to the right route,
 * a card is never offered to somebody who cannot use the page behind it, and
 * the secondary pills carry the less frequent starts.
 */

let role = 'maker';
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role }) }));

const dashboards = { memo: {}, minute: {}, circular: {} };
vi.mock('../../services/memoService', () => ({
  memoService: { getDashboard: () => Promise.resolve(dashboards.memo) },
}));
vi.mock('../../services/minuteService', () => ({
  minuteService: { getDashboard: () => Promise.resolve(dashboards.minute) },
}));
vi.mock('../../services/circularService', () => ({
  circularService: { getDashboard: () => Promise.resolve(dashboards.circular) },
}));

const wrap = () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><QuickActions /></MemoryRouter>
    </QueryClientProvider>,
  );
};

beforeEach(() => {
  role = 'maker';
  dashboards.memo = {};
  dashboards.minute = {};
  dashboards.circular = {};
});

describe('the primary shortcuts', () => {
  it('renders all four for an Employee, each linking to its create route', () => {
    wrap();
    const expected = [
      ['Create task', '/tasks/create'],
      ['Create memo', '/memos/create'],
      ['Create minute', '/minutes/create'],
      ['Apply for leave', '/leave/apply'],
    ];
    for (const [name, href] of expected) {
      expect(screen.getByRole('link', { name: new RegExp(name) }))
        .toHaveAttribute('href', href);
    }
  });

  it('makes the WHOLE card the target, not a button inside it', () => {
    const { container } = wrap();
    expect(container.querySelectorAll('a.qa-card')).toHaveLength(4);
    expect(container.querySelector('.qa-card button')).toBeNull();
  });

  it('is a labelled section with the new heading', () => {
    wrap();
    expect(screen.getByRole('region', { name: 'Start something' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Start something' })).toBeInTheDocument();
  });
});

describe('the secondary pills', () => {
  it('carry the circular, asset request and attendance routes', () => {
    const { container } = wrap();
    const pills = Array.from(container.querySelectorAll('a.qa-pill'))
      .map((a) => [a.textContent, a.getAttribute('href')]);
    expect(pills).toEqual([
      ['Publish a circular', '/circulars/create'],
      ['Request an asset', '/inventory/requests'],
      ['My attendance', '/my-attendance'],
    ]);
  });
});

describe('role gating', () => {
  it('hides memo and leave from Admin, who has neither capability', () => {
    role = 'admin';
    wrap();
    expect(screen.queryByRole('link', { name: /Create memo/ })).toBeNull();
    expect(screen.queryByRole('link', { name: /Apply for leave/ })).toBeNull();
    expect(screen.getByRole('link', { name: /Create task/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Create minute/ })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Publish a circular/ })).toBeInTheDocument();
  });

  it('shows all four cards to a Department Head and to HR', () => {
    for (const r of ['checker', 'approver']) {
      role = r;
      const { container, unmount } = wrap();
      expect(container.querySelectorAll('a.qa-card')).toHaveLength(4);
      unmount();
    }
  });
});

describe('badges', () => {
  it('shows counts once the cached dashboards resolve', async () => {
    dashboards.memo = { drafts: 2 };
    dashboards.minute = { my_drafts: 1 };
    dashboards.circular = { unread: 3 };
    wrap();
    expect(await screen.findByText('2 drafts')).toBeInTheDocument();
    expect(await screen.findByText('1 draft')).toBeInTheDocument();
    expect(await screen.findByText('3 unread')).toBeInTheDocument();
  });

  it('pluralises correctly', async () => {
    dashboards.memo = { drafts: 1 };
    wrap();
    expect(await screen.findByText('1 draft')).toBeInTheDocument();
  });

  it('shows no badge at zero, rather than a distracting "0 drafts"', () => {
    dashboards.memo = { drafts: 0 };
    const { container } = wrap();
    expect(container.querySelector('.qa-badge')).toBeNull();
  });

  it('renders the shortcuts even when every dashboard fails', () => {
    const { container } = wrap();
    expect(container.querySelectorAll('a.qa-card')).toHaveLength(4);
    expect(container.querySelectorAll('a.qa-pill')).toHaveLength(3);
    expect(container.querySelector('.qa-badge')).toBeNull();
  });
});
