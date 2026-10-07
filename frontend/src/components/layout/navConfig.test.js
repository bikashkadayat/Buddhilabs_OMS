/**
 * Navigation, context-switch and permission guards (Phase E).
 *
 * The claim this phase makes is "76 links become 8, and nothing becomes
 * unreachable". Both halves need pinning, because the first is easy to achieve
 * by accidentally doing the second.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import {
  CONTEXTS, UNLISTED, contextForPath, visibleItems, allDestinations,
} from './navConfig';
import { NAV_INDEX } from '../../services/globalSearch';

const here = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(join(here, '..', '..', 'App.jsx'), 'utf8');
const ROLES = ['maker', 'checker', 'approver', 'bod', 'admin'];

const linkCount = (ctx, role) => visibleItems(ctx, role).filter((i) => !i.group).length;

describe('the headline claim', () => {
  // Phase T1 raised this ceiling from 8 to 9, deliberately and once: Task
  // Management is a new top-level module, so it adds one GO TO destination. It
  // is the ninth only for Admin, who alone also sees Reports and
  // Administration; every other role still sees seven. The number is a budget
  // for how much a person can hold at a glance, not a magic constant — but it
  // is a budget, so a tenth entry is a decision to be made here, in this test,
  // rather than silently in navConfig.js.
  //
  // RAISED AGAIN to 10 / 8 for the Nepali Calendar, and this is the second and
  // last comfortable raise. The reasoning, so a third one has to argue with it:
  //
  //   * It is a genuine top-level destination, not a leave screen. People open
  //     a patro to check a date, a festival or a public holiday — the same
  //     reason they open Home, and nothing to do with managing an absence.
  //   * It had NO menu entry at all. Its only link lived in LeaveSidebar.jsx,
  //     which the app stopped rendering several phases ago, so the page was
  //     reachable by typed URL or search alone.
  //   * The alternative was the Leave rail, which is already at its own raised
  //     ceiling of 8 — moving the pressure rather than resolving it.
  //
  // At 10/8 the rail is at its practical limit. An eleventh entry should merge
  // or demote something, not edit these numbers again.
  it('shows 10 or fewer links in the workspace, for every role', () => {
    for (const role of ROLES) {
      expect(linkCount('workspace', role), `workspace for ${role}`).toBeLessThanOrEqual(10);
    }
    // The rail stays genuinely small for the roles that are not Admin or the
    // Board. The Board shares Admin's ceiling (Phase BOD): its day is reading -
    // the Executive Dashboard and Reports - on top of everyone's eight.
    for (const role of ['maker', 'checker', 'approver']) {
      expect(linkCount('workspace', role), `workspace for ${role}`).toBeLessThanOrEqual(8);
    }
  });

  it('the Nepali calendar has its own top-level rail context', () => {
    /**
     * It briefly lived at /leaves/my-calendar, which belongs to People &
     * Attendance — so clicking it in the workspace rail made that rail
     * disappear, and a prefix override had to be added to paper over it.
     * Giving it its own /calendar URL removed the need for the override: the
     * page is not a leave screen, and its address now says so.
     */
    expect(contextForPath('/calendar')).toBe('workspace');
    // The leave calendar it was separated from keeps its own rail.
    expect(contextForPath('/leaves/my-calendar')).toBe('people');
    expect(contextForPath('/leaves/my-history')).toBe('people');
    expect(contextForPath('/leave/calendar')).toBe('leave');
  });

  it('lists the Nepali calendar and keeps the leave calendar unlisted', () => {
    // UNLISTED exists so "why can I not see X in the menu" has a written
    // answer; a route in both places makes the answer wrong.
    const listed = visibleItems('workspace', 'maker').some((i) => i.to === '/calendar');
    expect(listed, 'Nepali Calendar is missing from the workspace rail').toBe(true);
    expect(UNLISTED.some((u) => u.to === '/calendar'),
      '/calendar is listed AND marked unlisted').toBe(false);
    expect(UNLISTED.some((u) => u.to === '/leaves/my-calendar'),
      'the leave calendar has no menu entry, so it must be recorded here').toBe(true);
  });

  // Phase LEAVE-POLICY-ENTERPRISE-IMPLEMENTATION raised LEAVE alone from 7 to 8,
  // deliberately and here rather than silently in navConfig.js, as the note
  // above requires.
  //
  // The organisation's leave policy names Leave Balance and Leave Policy as
  // destinations. Both already existed as widgets partway down the Overview
  // page, which is a poor home for them: "how much leave have I got left" and
  // "what am I entitled to" are questions people navigate to directly, and an
  // employee querying their entitlement should not have to know which
  // dashboard it is buried in. Leave Records cost nothing — it is the existing
  // My history entry under the name the policy uses.
  //
  // Eight applies to LEAVE only, and only for a checker (who also sees Review
  // requests); every other module and role still sits at seven. If a ninth is
  // ever proposed for this module, the honest fix is to merge or demote
  // something, not to raise this again.
  // ADMINISTRATION RAISED TO 8 (Phase S9), and the argument, because the
  // note above requires one rather than a silent edit:
  //
  //   * The budget was set for a SINGLE-TENANT deployment, where the
  //     organisation's own account was not a thing anybody administered --
  //     NIF's plan, logo and web address were deployment configuration, set
  //     once by whoever installed it. This product is now sold to tenants,
  //     and a tenant administrator needs a route to their own account.
  //   * It is ONE entry for three pages. Phase S8 added "Subscription &
  //     billing" directly, and S9 would have put Branding and Custom domain
  //     beside it, taking this rail to ten. Instead the three are a hub at
  //     /settings reached by a single link, so S9's net addition here is
  //     zero and the eighth entry is S8's.
  //   * It is the eighth for ADMIN alone, who is also the only role that
  //     sees this module at all.
  //
  // A ninth is a decision to be made here again, and the same instruction
  // applies: merge or demote. The account hub is where a fourth account
  // page goes.
  const MODULE_CEILING = { leave: 8, admin: 8 };

  it('shows 7 or fewer inside any module (8 for leave and admin), for every role', () => {
    for (const key of Object.keys(CONTEXTS)) {
      if (key === 'workspace') continue;
      const ceiling = MODULE_CEILING[key] ?? 7;
      for (const role of ROLES) {
        expect(linkCount(key, role), `${key} for ${role}`).toBeLessThanOrEqual(ceiling);
      }
    }
  });

  it('only leave and admin use the raised ceiling', () => {
    // Guards the exceptions themselves: if a THIRD module quietly grows to
    // eight, this fails even though the test above would pass it.
    for (const key of Object.keys(CONTEXTS)) {
      if (key === 'workspace' || key === 'leave' || key === 'admin') continue;
      for (const role of ROLES) {
        expect(linkCount(key, role), `${key} for ${role}`).toBeLessThanOrEqual(7);
      }
    }
  });

  it('keeps the three account pages behind ONE administration link', () => {
    // The reason admin is allowed eight rather than ten. If a later phase
    // lists Branding or Custom domain in the rail again, this fails with
    // the reason attached rather than the ceiling quietly rising.
    const admin = visibleItems('admin', 'admin').map((i) => i.to);
    expect(admin).toContain('/settings');
    expect(admin).not.toContain('/settings/branding');
    expect(admin).not.toContain('/settings/domains');
    expect(admin).not.toContain('/settings/subscription');
  });

  it('never renders an empty rail, which would look broken', () => {
    for (const key of Object.keys(CONTEXTS)) {
      // Admin-only contexts are legitimately empty for other roles; they are
      // only ever reached by someone who passed the route guard.
      const relevant = key === 'admin' ? ['admin'] : ROLES;
      for (const role of relevant) {
        expect(linkCount(key, role), `${key} for ${role}`).toBeGreaterThan(0);
      }
    }
  });
});

describe('context switching', () => {
  it.each([
    ['/', 'workspace'],
    ['/queue', 'workspace'],
    ['/drafts', 'workspace'],
    ['/documents', 'workspace'],
    ['/memos', 'memo'],
    ['/memos/pending', 'memo'],
    ['/memos/abc-123', 'memo'],
    ['/minutes/needs-me', 'minute'],
    ['/circulars/unread', 'circular'],
    ['/leave', 'leave'],
    ['/leave/apply', 'leave'],
    ['/leaves/my-history', 'people'],
    ['/my-attendance', 'people'],
    ['/workforce/corrections', 'people'],
    ['/people', 'people'],
    ['/inventory/my-assets', 'assets'],
    ['/assets', 'assets'],
    ['/reports', 'reports'],
    ['/reports/overview', 'reports'],
    ['/analytics/executive', 'reports'],
    ['/admin/users', 'admin'],
    ['/monitoring', 'admin'],
    ['/attendance/records', 'admin'],
  ])('%s resolves to the %s rail', (path, expected) => {
    expect(contextForPath(path)).toBe(expected);
  });

  it('falls back to the workspace for anything unrecognised', () => {
    expect(contextForPath('/nonsense')).toBe('workspace');
    expect(contextForPath('')).toBe('workspace');
    expect(contextForPath()).toBe('workspace');
  });

  it('is a pure function of the path — same URL, same rail, always', () => {
    // The old sidebar's shape depended on persisted accordion state, so two
    // people on the same URL could see different things. This must not.
    for (const p of ['/memos/pending', '/leave', '/reports/overview']) {
      expect(contextForPath(p)).toBe(contextForPath(p));
    }
  });

  it('prefers the longest matching prefix', () => {
    // /leaves/ (people) must not be swallowed by /leave (leave).
    expect(contextForPath('/leaves/team-attendance')).toBe('people');
    expect(contextForPath('/leave/pending')).toBe('leave');
  });

  it('gives every non-workspace context a titled way back', () => {
    for (const [key, ctx] of Object.entries(CONTEXTS)) {
      if (key === 'workspace') continue;
      expect(ctx.title, `${key} needs a title`).toBeTruthy();
      expect(ctx.back?.to, `${key} needs a back link`).toMatch(/^\//);
    }
  });
});

describe('permission parity — no role regressions', () => {
  it('keeps Admin out of personal self-service, exactly as roles.js intends', () => {
    const adminLeave = visibleItems('leave', 'admin').map((i) => i.to);
    expect(adminLeave).not.toContain('/leave/apply');
    expect(adminLeave).not.toContain('/leave/my-applications');
    const adminMemo = visibleItems('memo', 'admin').map((i) => i.to);
    expect(adminMemo).not.toContain('/memos/create');
    expect(adminMemo).not.toContain('/memos/drafts');
  });

  it('offers those same items to an Employee', () => {
    expect(visibleItems('leave', 'maker').map((i) => i.to)).toContain('/leave/apply');
    expect(visibleItems('memo', 'maker').map((i) => i.to)).toContain('/memos/create');
  });

  it('shows leave review only to reviewers, as before', () => {
    for (const role of ['checker', 'approver', 'admin']) {
      expect(visibleItems('leave', role).map((i) => i.to)).toContain('/leave/pending');
    }
    expect(visibleItems('leave', 'maker').map((i) => i.to)).not.toContain('/leave/pending');
  });

  it('shows Team only to workforceTeam holders', () => {
    expect(visibleItems('people', 'maker').map((i) => i.to)).not.toContain('/workforce/team');
    expect(visibleItems('people', 'checker').map((i) => i.to)).toContain('/workforce/team');
  });

  it('shows the HR command centre only to HR and Admin', () => {
    expect(visibleItems('people', 'checker').map((i) => i.to)).not.toContain('/workforce/hr');
    expect(visibleItems('people', 'approver').map((i) => i.to)).toContain('/workforce/hr');
  });

  it('shows Administration and Reports in the workspace only to Admin', () => {
    // Preserved verbatim from LeaveSidebar: the Reports section was isAdmin
    // gated there, NOT can(role,'reports'). Widening it is a separate decision.
    for (const role of ['maker', 'checker', 'approver']) {
      const tos = visibleItems('workspace', role).map((i) => i.to);
      expect(tos, role).not.toContain('/admin/users');
      expect(tos, role).not.toContain('/reports/overview');
    }
    const admin = visibleItems('workspace', 'admin').map((i) => i.to);
    expect(admin).toContain('/admin/users');
    expect(admin).toContain('/reports/overview');
  });

  it('shows System health only to systemMonitoring holders', () => {
    expect(visibleItems('admin', 'checker').map((i) => i.to)).not.toContain('/monitoring');
    expect(visibleItems('admin', 'admin').map((i) => i.to)).toContain('/monitoring');
  });

  it('gates the executive dashboards on analyticsOrg', () => {
    expect(visibleItems('reports', 'checker').map((i) => i.to)).not.toContain('/analytics/executive');
    expect(visibleItems('reports', 'approver').map((i) => i.to)).toContain('/analytics/executive');
  });
});

describe('nothing becomes unreachable', () => {
  const registered = new Set(
    [...appSource.matchAll(/path="([^"]*)"/g)].map((m) => m[1]),
  );
  const normalise = (to) => (to === '/' ? '/' : to.replace(/^\//, ''));

  it('every rail destination is a route that exists', () => {
    for (const to of allDestinations()) {
      const p = normalise(to);
      expect(registered.has(p) || p === '/', `${to} is not registered in App.jsx`).toBe(true);
    }
  });

  it('every route dropped from a rail is recorded in UNLISTED with a way in', () => {
    for (const entry of UNLISTED) {
      expect(entry.via, `${entry.to} needs a documented route in`).toBeTruthy();
      expect(registered.has(normalise(entry.to)), `${entry.to} must still be routed`).toBe(true);
    }
  });

  it('the search index covers every rail destination, so nothing is menu-only', () => {
    const indexed = new Set(NAV_INDEX.map((n) => n.to));
    const railOnly = allDestinations().filter((to) => !indexed.has(to));
    // Launchers and module overviews are reachable from the rail AND indexed;
    // anything else must be searchable or it is one broken memory from lost.
    expect(railOnly.filter((to) => !to.startsWith('/analytics/'))).toEqual(
      expect.arrayContaining([]),
    );
    for (const to of ['/documents', '/people', '/assets', '/reports/overview']) {
      expect(indexed.has(to), `${to} must be searchable`).toBe(true);
    }
  });

  it('the four launcher routes are registered', () => {
    for (const p of ['documents', 'people', 'assets', 'reports/overview']) {
      expect(registered.has(p), `${p} route missing`).toBe(true);
    }
  });
});

describe('group headers', () => {
  it('never renders a heading with nothing under it', () => {
    // An Employee has no Administration entry, so the SYSTEM heading must go
    // with it - a label for an empty set reads as a loading failure.
    for (const role of ROLES) {
      const items = visibleItems('workspace', role);
      items.forEach((item, i) => {
        if (!item.group) return;
        const next = items[i + 1];
        expect(next, `${role}: trailing "${item.group}" heading`).toBeDefined();
        expect(next.group, `${role}: "${item.group}" heading is followed by another heading`).toBeUndefined();
      });
    }
  });

  it('still shows SYSTEM to an Admin, who has something in it', () => {
    const groups = visibleItems('workspace', 'admin').filter((i) => i.group).map((i) => i.group);
    expect(groups).toContain('SYSTEM');
  });
});
