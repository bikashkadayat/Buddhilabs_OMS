import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

/**
 * Customer success: help that points at real pages, a tour that points at
 * real controls, and feedback that never nags.
 */
vi.mock('../../services/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } } },
}));
let authUser = { id: 'u1', ui_state: {}, must_change_password: false };
let authRole = 'maker';
const refreshUser = vi.fn();
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ user: authUser, role: authRole, refreshUser }),
}));

import api from '../../services/api';
import { HELP, searchHelp, helpFor } from '../../services/helpContent';
import GuidedTour from './GuidedTour';
import RateThis from './RateThis';
import { restartTour } from '../../services/tours';

const src = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

const realRect = Element.prototype.getBoundingClientRect;
afterEach(() => { Element.prototype.getBoundingClientRect = realRect; });

beforeEach(() => {
  vi.clearAllMocks();
  authUser = { id: 'u1', ui_state: {}, must_change_password: false };
  authRole = 'maker';
  try { localStorage.clear(); } catch { /* fine */ }
});

describe('the help content', () => {
  it('links only to pages that exist', () => {
    // Every <Route path="..."> in the app, as patterns.
    const app = readFileSync(join(src, 'App.jsx'), 'utf8');
    const routes = [...app.matchAll(/<Route\s+path="([^"]+)"/g)].map((m) => m[1].replace(/^\//, ''));
    const exists = (to) => {
      const path = to.split(/[?#]/)[0].replace(/^\//, '');
      return routes.some((r) => {
        const re = new RegExp(`^${r.replace(/:[^/]+/g, '[^/]+')}$`);
        return re.test(path) || (path === '' && r === '');
      }) || path === '';
    };
    const broken = HELP.flatMap((a) => a.links.map((l) => l.to)).filter((to) => !exists(to));
    expect(broken, `help links to missing pages: ${broken.join(', ')}`).toEqual([]);
  });

  it('shows each person only what applies to them', () => {
    const employee = helpFor('maker').map((a) => a.slug);
    expect(employee).toContain('check-in');
    expect(employee).not.toContain('add-user');
    expect(helpFor('admin').map((a) => a.slug)).toContain('add-user');
  });

  it('finds an answer from the words people actually type', () => {
    expect(searchHelp('forgot password', 'maker')[0].slug).toBe('forgot-password');
    expect(searchHelp('esewa', 'admin')[0].slug).toBe('subscription');
    expect(searchHelp('esewa', 'maker')).toEqual([]);
  });
});

describe('the guided tour', () => {
  const page = () => render(
    <MemoryRouter>
      <section data-tour="attendance" style={{ width: 100, height: 40 }}>hero</section>
      <GuidedTour />
    </MemoryRouter>,
  );

  it('points only at controls that are on the page, and is remembered when done', async () => {
    // jsdom has no layout: give the one target a size.
    const rect = { width: 100, height: 40, top: 10, left: 10, bottom: 50, right: 110 };
    Element.prototype.getBoundingClientRect = function getRect() {
      return this.dataset?.tour === 'attendance' ? rect : { width: 0, height: 0, top: 0, left: 0, bottom: 0, right: 0 };
    };
    api.post.mockResolvedValue({ data: { tours_done: ['employee'] } });
    page();
    act(() => restartTour());
    expect(await screen.findByText('Your day starts here')).toBeInTheDocument();
    expect(screen.getByText('1 of 1')).toBeInTheDocument();      // the others had no target
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/profile/me/tour/', { tour: 'employee' }));
    expect(refreshUser).toHaveBeenCalled();
  });

  it('does not start by itself for someone who has finished it', async () => {
    authUser = { id: 'u1', ui_state: { tours_done: ['employee'] } };
    vi.useFakeTimers();
    page();
    act(() => { vi.advanceTimersByTime(3000); });
    vi.useRealTimers();
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('rating', () => {
  it('sends a good rating at once, and asks why only after a low one', async () => {
    api.post.mockResolvedValue({ data: {} });
    render(<RateThis feature="help:check-in" question="Was this helpful?" />);
    fireEvent.click(screen.getByRole('button', { name: '5 out of 5' }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/support/requests/',
      expect.objectContaining({ kind: 'feedback', rating: 5, feature: 'help:check-in' })));
    expect(await screen.findByRole('status')).toHaveTextContent('Thanks');
  });

  it('opens a comment box after a low rating', () => {
    render(<RateThis feature="help:other" />);
    fireEvent.click(screen.getByRole('button', { name: '2 out of 5' }));
    expect(api.post).not.toHaveBeenCalled();
    expect(screen.getByLabelText(/what would have made it better/i)).toBeInTheDocument();
  });

  it('never asks the same person twice', () => {
    localStorage.setItem('feedback:help:seen', '1');
    const { container } = render(<RateThis feature="help:seen" />);
    expect(container).toBeEmptyDOMElement();
  });
});
