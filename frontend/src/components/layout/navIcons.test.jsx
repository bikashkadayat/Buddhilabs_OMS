import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as lucide from 'lucide-react';

import { NAV_ICONS } from './navIcons';
import { CONTEXTS } from './navConfig';

/**
 * Phase OMS-NAVIGATION-ICON-CONSISTENCY.
 *
 * Before this, icons rendered only in the workspace rail: a destination had an
 * icon at workspace level and none one click deeper, so navigation appeared to
 * lose its iconography the further in you went. 69 of 78 entries had no icon
 * at all. Four surfaces — sidebar, mobile tab bar, command palette, global
 * search — each carried their own hand-written SVG paths, so the same
 * destination was drawn several different ways at several different strokes.
 *
 * These pin the outcome: every entry names an icon, every name resolves, and
 * no navigation surface draws its own.
 */
const here = dirname(fileURLToPath(import.meta.url));
const read = (rel) => readFileSync(join(here, rel), 'utf8');

// The workspace rail is CONTEXTS.workspace; every module is a sibling of it,
// so one walk covers both levels — which is the point, since the bug was that
// only the first level had icons.
const allItems = () => Object
  .values(CONTEXTS)
  .flatMap((ctx) => ctx.items || [])
  .filter((i) => !i.group);

describe('navigation icons', () => {
  it('covers every navigation entry, in every context', () => {
    const items = allItems();
    // Guards against a silent empty sweep: if the config shape changes and
    // this stops finding entries, the assertions below would pass vacuously.
    expect(items.length).toBeGreaterThan(70);
    const naked = items.filter((i) => !i.icon).map((i) => i.key);
    expect(naked, `entries with no icon: ${naked.join(', ')}`).toEqual([]);
  });

  it('resolves every named icon to a real Lucide component', () => {
    const unknown = allItems().filter((i) => !NAV_ICONS[i.icon])
      .map((i) => `${i.key} -> ${i.icon}`);
    expect(unknown, `names missing from the registry: ${unknown.join(', ')}`).toEqual([]);
  });

  it('registry entries are all genuine Lucide components', () => {
    const byName = new Map(Object.entries(lucide).map(([n, c]) => [c, n]));
    for (const [name, Glyph] of Object.entries(NAV_ICONS)) {
      expect(byName.has(Glyph), `"${name}" is not a lucide-react icon`).toBe(true);
    }
  });

  it('renders an icon for module entries, not only the workspace rail', () => {
    // The specific regression: `{isWorkspace && <Icon …>}` meant module menus
    // were text-only.
    const sidebar = read('AppSidebar.jsx');
    expect(sidebar).not.toMatch(/isWorkspace\s*&&\s*<(Nav)?Icon/);
    expect(sidebar).toMatch(/<NavIcon name=\{item\.icon\}/);
  });

  it('no navigation surface draws its own SVG paths', () => {
    const surfaces = [
      'AppSidebar.jsx',
      'MobileTabBar.jsx',
      '../search/CommandPalette.jsx',
      '../search/GlobalSearch.jsx',
      '../home/QuickActions.jsx',
    ];
    for (const file of surfaces) {
      expect(read(file), `${file} still hand-rolls an icon`)
        .not.toMatch(/<svg[\s\S]{0,80}viewBox/);
    }
  });

  it('sets stroke in one place, and no surface overrides it', () => {
    // 2.25, raised from 2 with the size (Phase
    // OMS-NAVIGATION-PREMIUM-ICON-UPGRADE). The number is asserted rather than
    // read from the component so a change to it is a deliberate edit here.
    expect(read('NavIcon.jsx')).toMatch(/strokeWidth=\{2\.25\}/);
    expect(read('NavIcon.jsx')).toMatch(/size = 22/);
    for (const file of ['AppSidebar.jsx', 'MobileTabBar.jsx', '../home/QuickActions.jsx',
                        '../search/CommandPalette.jsx', '../search/GlobalSearch.jsx']) {
      const source = read(file);
      // A NavIcon must never carry its own stroke — that is the drift this
      // registry exists to prevent.
      expect(source, `${file}: NavIcon overrides stroke`)
        .not.toMatch(/<NavIcon[^>]*strokeWidth/);
      // A Lucide icon used directly (a close button, say) must still match the
      // weight the navigation glyphs use, so nothing looks half a shade off.
      for (const [, width] of source.matchAll(/strokeWidth=\{(\d+(?:\.\d+)?)\}/g)) {
        expect(Number(width), `${file}: stroke ${width} differs from the nav weight`)
          .toBe(2.25);
      }
    }
  });
});
