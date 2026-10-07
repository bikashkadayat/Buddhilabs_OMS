import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import AppSidebar from './AppSidebar';

/**
 * The sidebar footer (Phase E1).
 *
 * The contextual rail is the navigation now, not a trial of one, so the escape
 * hatch is gone. Three things are worth pinning against its return:
 *
 *   - no control in the footer at all. A test for the exact string "Use the
 *     classic menu" would pass the moment somebody reinstates the same switch
 *     under a different label, so this asserts the shape - the footer holds an
 *     identity block and nothing clickable.
 *   - nothing anywhere still reads or writes the opt-out flag. That is a source
 *     grep rather than a render assertion, because a stale branch in Layout is
 *     invisible to a test that only mounts the rail.
 *   - the identity line survives a null department, which the profile allows.
 */

let user = {
  initials: 'BK', full_name: 'Bikash Kadayat', username: 'bkadayat',
  department: 'Administration',
};
let role = 'checker';
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ user, role }) }));
vi.mock('../../hooks/useNavContext', () => ({
  useNavContext: () => ({
    context: 'workspace',
    isWorkspace: true,
    counts: {},
    items: [{ to: '/', label: 'Home', icon: 'home' }],
  }),
}));

const here = dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(join(here, ...p), 'utf8');

const mount = () => render(
  <MemoryRouter><AppSidebar open onClose={() => {}} /></MemoryRouter>,
);

beforeEach(() => {
  user = {
    initials: 'BK', full_name: 'Bikash Kadayat', username: 'bkadayat',
    department: 'Administration',
  };
  role = 'checker';
});

describe('the removed switch', () => {
  it('puts no navigation control in the footer', () => {
    // NARROWED (Phase UX-PRODUCTION-FINAL-IMPLEMENTATION), and the narrowing
    // matters more than the assertion.
    //
    // This used to assert the footer held nothing clickable at all — a proxy
    // for "the classic-menu switch has not come back", chosen because a test
    // for the exact old label would pass the moment somebody reinstated the
    // same switch under a new name. The proxy then blocked an unrelated and
    // wanted change: making the identity block a link to the profile.
    //
    // So it now pins the thing it was written to protect. The footer may hold
    // exactly one link, to the profile, and nothing that alters navigation:
    // no toggle, no switch, no button, no input.
    const { container } = mount();
    const foot = container.querySelector('.sb-foot');
    expect(foot).toBeInTheDocument();
    expect(foot.querySelector('button, [role=button], input, select')).toBeNull();

    const links = foot.querySelectorAll('a');
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute('href')).toBe('/me');
  });

  it('makes the avatar, the name and the role all part of that one link', () => {
    // One target, not three: three separate links stacked in a 60px footer is
    // three tab stops and three tap targets for one destination.
    const { container } = mount();
    const link = container.querySelector('.sb-foot a');
    expect(link).toHaveTextContent('Bikash Kadayat');
    // roleLabel maps the raw role to the human name — 'checker' reads as
    // 'Department Head', which is the whole point of that helper.
    expect(link).toHaveTextContent('Department Head');
    expect(link.querySelector('.sb-foot-av')).toBeInTheDocument();
  });

  it('offers nothing named for the old menu anywhere in the rail', () => {
    mount();
    for (const label of [/classic/i, /new navigation/i, /switch/i]) {
      expect(screen.queryByRole('button', { name: label })).toBeNull();
    }
  });

  it('leaves no trace of the opt-out flag in the layout sources', () => {
    // Layout branched on this flag. With the button gone, a surviving branch
    // would strand anyone still carrying the flag in a menu with no way out.
    for (const file of ['Layout.jsx', 'AppSidebar.jsx', 'LeaveSidebar.jsx']) {
      const src = read(file);
      expect(src, file).not.toMatch(/useClassicNav/);
      expect(src, file).not.toMatch(/nif-nav-classic/);
    }
    expect(read('Layout.jsx')).not.toMatch(/LeaveSidebar/);
  });

  it('drops the sb-classic styles rather than orphaning them', () => {
    expect(read('..', '..', 'index.css')).not.toMatch(/\.sb-classic/);
  });
});

describe('what the footer keeps', () => {
  it('shows the avatar, the name, and role and department as separate chips', () => {
    /**
     * THE FORMAT CHANGED, and the reason is worth keeping.
     *
     * This pinned "Department Head · Administration" as one string. Role and
     * department are two different facts — what you may do, and where you sit
     * — and joining them made one long muted line that truncated from the
     * wrong end on a narrow rail: "Department Head · Administrat…". They are
     * now two chips that wrap (Phase OMS-NAVIGATION-PREMIUM-ICON-UPGRADE).
     *
     * The nullable-department case below still matters and still holds: the
     * chip is omitted rather than left as a separator with nothing after it.
     */
    const { container } = mount();
    expect(container.querySelector('.sb-foot-av')).toHaveTextContent('BK');
    expect(screen.getByText('Bikash Kadayat')).toBeInTheDocument();
    expect(screen.getByText('Department Head')).toBeInTheDocument();
    expect(screen.getByText('Administration')).toBeInTheDocument();
    expect(container.querySelectorAll('.sb-tag')).toHaveLength(2);
  });

  it('shows the role alone when the profile carries no department', () => {
    // `department` is nullable, and a separator hanging off a role is exactly
    // the debris a footer this small cannot hide.
    user = { ...user, department: null };
    const { container } = mount();
    expect(screen.getByText('Department Head')).toBeInTheDocument();
    expect(screen.queryByText(/·/)).toBeNull();
    expect(container.querySelectorAll('.sb-tag')).toHaveLength(1);
  });

  it('falls back to the username when there is no full name', () => {
    user = { ...user, full_name: null };
    mount();
    expect(screen.getByText('bkadayat')).toBeInTheDocument();
  });

  it('states presence in words, with the dot as decoration beside them', () => {
    /**
     * THE DOT MOVED, and the accessibility position improved rather than
     * survived (Phase OMS-SIDEBAR-FOOTER-ENTERPRISE).
     *
     * It used to sit on the avatar's corner, unlabelled, inside an
     * aria-hidden wrapper — so a screen reader was told nothing at all, and a
     * sighted user had to infer what a green dot meant. It is now one labelled
     * row: the word "Online" is real text everyone gets, and the dot stays
     * aria-hidden because it repeats that word rather than adding to it.
     */
    const { container } = mount();
    const status = container.querySelector('.sb-foot-status');
    expect(status).toBeInTheDocument();
    expect(status).toHaveTextContent('Online');

    const dot = container.querySelector('.sb-foot-dot');
    expect(dot).toBeEmptyDOMElement();
    expect(dot.getAttribute('aria-hidden')).toBe('true');
    expect(status).toContainElement(dot);

    // Exactly one presence indicator: the same fact twice in a 280px rail is
    // what removing the corner dot avoided.
    expect(container.querySelectorAll('.sb-foot-dot')).toHaveLength(1);
  });
});
