import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { TOURS } from '../components/help/GuidedTour';

/**
 * A tour stop whose `data-tour` anchor is gone is SKIPPED, not broken -- by
 * design, so a phone layout never describes a button it lacks. The cost is
 * that a redesign which drops an anchor shortens every tour silently. This
 * pins each stop to an anchor that still exists somewhere in the source,
 * either literally (data-tour="x") or as a quoted value on a data-tour line.
 */
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');
const files = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  if (statSync(path).isDirectory()) return files(path);
  return /\.jsx$/.test(name) && !/\.test\./.test(name) && name !== 'GuidedTour.jsx' ? [path] : [];
});

const anchorLines = files(SRC).flatMap((path) => readFileSync(path, 'utf8')
  .split('\n').filter((line) => line.includes('data-tour')));

describe('guided tour anchors', () => {
  const targets = [...new Set(Object.values(TOURS).flat().map((stop) => stop.target))];

  it.each(targets)('"%s" is anchored somewhere in the app', (target) => {
    const found = anchorLines.some((line) => line.includes(`data-tour="${target}"`)
      || line.includes(`'${target}'`));
    expect(found).toBe(true);
  });
});
