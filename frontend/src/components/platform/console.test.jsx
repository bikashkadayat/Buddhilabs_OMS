import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

/**
 * The console shell: identity, notifications, and the way out.
 *
 * WHAT THESE GUARD. The console had no profile area, no user menu and no
 * notifications — the account with the most power in the product had the
 * least account surface of anybody using it. The tests that matter are the
 * ones about REACHABILITY (can an operator get to their own password?) and
 * about the brand boundary, which is a security property rather than a
 * cosmetic one.
 */
// src/, which holds index.css and every component that could use its classes.
const cssDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..');

const logout = vi.fn();
const navigate = vi.fn();
let user = { email: 'ops@buddhilabs.com', name: 'Platform Operator' };

vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ user, logout }) }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
vi.mock('./PlatformNotifications', () => ({
  default: () => <div data-testid="notes">notifications</div>,
}));

import PlatformTopbar from './PlatformTopbar';

const wrap = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  user = { email: 'ops@buddhilabs.com', name: 'Platform Operator' };
  document.documentElement.removeAttribute('data-theme');
});

describe('the console top bar', () => {
  it('wears the platform brand, never a tenant one', () => {
    // `BrandingProvider` excludes platform staff deliberately: an operator
    // looking at a customer's colours is how somebody suspends the wrong
    // organization. This is the one surface that is always the platform.
    wrap(<PlatformTopbar />);
    expect(screen.getByText('Buddhi Labs')).toBeInTheDocument();
    expect(screen.getByText('Platform Console')).toBeInTheDocument();
    const mark = document.querySelector('.pf-top-mark');
    expect(mark).toHaveAttribute('src', expect.stringContaining('buddhi-labs'));
  });

  it('gives the operator a route to their own profile and password', async () => {
    wrap(<PlatformTopbar />);
    await userEvent.click(screen.getByRole('button', { name: /platform operator/i }));
    expect(screen.getByRole('menuitem', { name: /my profile/i }))
      .toHaveAttribute('href', '/platform/profile');
    expect(screen.getByRole('menuitem', { name: /security/i }))
      .toHaveAttribute('href', '/platform/profile#security');
    expect(screen.getByRole('menuitem', { name: /activity log/i }))
      .toHaveAttribute('href', '/platform/audit');
  });

  it('signs out from the menu', async () => {
    wrap(<PlatformTopbar />);
    await userEvent.click(screen.getByRole('button', { name: /platform operator/i }));
    await userEvent.click(screen.getByRole('menuitem', { name: /sign out/i }));
    await waitFor(() => expect(logout).toHaveBeenCalled());
  });

  it('closes the menu on Escape, not only on a click elsewhere', async () => {
    // A menu that can only be dismissed by clicking away traps a keyboard
    // user inside it.
    wrap(<PlatformTopbar />);
    await userEvent.click(screen.getByRole('button', { name: /platform operator/i }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('menu')).toBeNull());
  });

  it('opens notifications, and only one panel at a time', async () => {
    wrap(<PlatformTopbar />);
    await userEvent.click(screen.getByRole('button', { name: /notifications/i }));
    expect(screen.getByTestId('notes')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /platform operator/i }));
    expect(screen.queryByTestId('notes')).toBeNull();
    expect(screen.getByRole('menu')).toBeInTheDocument();
  });

  it('reports the theme up rather than styling the whole document', async () => {
    // Scoped to the console shell: a half-converted dark mode across the
    // tenant application would be worse than none.
    const onTheme = vi.fn();
    wrap(<PlatformTopbar theme="light" onTheme={onTheme} />);
    await userEvent.click(screen.getByRole('button', { name: /dark theme/i }));
    expect(onTheme).toHaveBeenCalledWith('dark');
    expect(document.documentElement).not.toHaveAttribute('data-theme');
  });

  it('searches organizations, which is the only list worth searching here', async () => {
    wrap(<PlatformTopbar />);
    await userEvent.type(screen.getByLabelText(/search organizations/i), 'abc{Enter}');
    await waitFor(() => expect(navigate)
      .toHaveBeenCalledWith('/platform/organizations?search=abc'));
  });

  it('falls back to the email when the operator has no name', () => {
    user = { email: 'ops@buddhilabs.com' };
    wrap(<PlatformTopbar />);
    expect(screen.getByText('ops@buddhilabs.com')).toBeInTheDocument();
  });
});

/**
 * The stylesheet, which is where a redesign actually rots.
 *
 * The console used to be a sidebar beside a page; it is now a top bar above a
 * body with the rail as a drawer. The old layout's rules did not disappear
 * when its markup did, and two of them were still winning at phone width --
 * `flex-direction: row` and `overflow-x: auto` on `.pf-side`, from a
 * `max-width: 860px` block that the drawer's `max-width: 1024px` block did
 * not override because it never sets those properties. The drawer slid in as
 * a sideways strip of nav items. Nothing failed; it just looked wrong, on the
 * one screen size nobody was looking at.
 *
 * These two read the file because that is the only place the evidence is.
 */
describe('the console stylesheet carries no rules for the old layout', () => {
  const css = readFileSync(join(cssDir, 'index.css'), 'utf8');

  it('never lays the rail out as a horizontal strip', () => {
    // Every block that styles `.pf-side`, with its declarations.
    const blocks = [...css.matchAll(/\.pf-side[^{}]*\{([^}]*)\}/g)]
      .map((m) => m[1]);
    expect(blocks.length).toBeGreaterThan(0);
    for (const body of blocks) {
      expect(body.replace(/\s+/g, ' '))
        .not.toMatch(/flex-direction:\s*row/);
    }
  });

  it('defines no `pf-` class that no component uses', () => {
    const defined = new Set(
      [...css.matchAll(/\.(pf-[a-z0-9-]+)/g)].map((m) => m[1]),
    );
    const used = new Set();
    const walk = (dir) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = join(dir, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (/\.jsx?$/.test(entry.name)) {
          for (const m of readFileSync(full, 'utf8').matchAll(/pf-[a-z0-9-]+/g)) {
            used.add(m[0]);
          }
        }
      }
    };
    walk(cssDir);
    const dead = [...defined].filter((c) => !used.has(c)).sort();
    expect(dead, `dead console CSS: ${dead.join(', ')}`).toEqual([]);
  });
});
