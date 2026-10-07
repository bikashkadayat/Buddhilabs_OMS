import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as lucide from 'lucide-react';

/**
 * Phase OMS-ICON-STANDARDIZATION.
 *
 * Every launcher tile carried one or two letters — M, Mi, C, D, A, L, W, Ap —
 * which read as placeholder text: "Mi" says nothing the word "Minute" beside
 * it does not, and two modules sharing a first letter got arbitrary
 * disambiguation. This pins the replacement so the letters cannot come back
 * one tile at a time, and so a mistyped icon name fails here rather than
 * rendering an empty coloured square nobody notices.
 */
const here = dirname(fileURLToPath(import.meta.url));
const files = readdirSync(here).filter((f) => f.endsWith('Launcher.jsx'));
const read = (f) => readFileSync(join(here, f), 'utf8');

describe('launcher tiles use icons, not letters', () => {
  it('finds the launchers at all (guards against a silent empty sweep)', () => {
    expect(files.length).toBeGreaterThanOrEqual(4);
  });

  it.each(files)('%s declares no letter initials', (file) => {
    expect(read(file)).not.toMatch(/\binitial\s*:/);
  });

  it.each(files)('%s gives every tile an icon', (file) => {
    const source = read(file);
    const tiles = [...source.matchAll(/key: '([^']+)', title: '[^']*'/g)];
    expect(tiles.length).toBeGreaterThan(0);
    for (const [match, key] of tiles) {
      const after = source.slice(source.indexOf(match), source.indexOf(match) + 200);
      expect(after, `${file}: tile "${key}" has no icon`).toMatch(/icon: [A-Z]\w+/);
    }
  });

  it.each(files)('%s only names icons that exist in lucide-react', (file) => {
    const source = read(file);
    for (const [, name] of source.matchAll(/icon: ([A-Z]\w+)/g)) {
      expect(lucide[name], `${file}: lucide-react has no icon "${name}"`).toBeTruthy();
    }
  });

  it.each(files)('%s imports its icons from the one icon family', (file) => {
    const source = read(file);
    const used = new Set([...source.matchAll(/icon: ([A-Z]\w+)/g)].map((m) => m[1]));
    const imported = source.match(/import \{([^}]+)\} from 'lucide-react'/);
    expect(imported, `${file}: no lucide-react import`).toBeTruthy();
    const names = new Set(imported[1].split(',').map((n) => n.trim()));
    for (const name of used) {
      expect(names.has(name), `${file}: ${name} is used but not imported`).toBe(true);
    }
    // No second icon library may creep in beside it.
    expect(source).not.toMatch(/from '(@heroicons|@tabler|react-icons)/);
  });
});
