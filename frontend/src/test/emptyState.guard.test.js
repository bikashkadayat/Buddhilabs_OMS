import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

/**
 * EmptyState takes `body`, not `description`. Passing `description` renders
 * a title with no explanation and no error anywhere -- the biometric devices
 * page shipped three of them, which is the case this guard exists for.
 * PageHeader DOES take `description`, so only <EmptyState ...> is checked.
 */
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

const files = (dir) => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name);
  if (statSync(path).isDirectory()) return name === 'node_modules' ? [] : files(path);
  return /\.jsx?$/.test(name) && !/\.test\./.test(name) ? [path] : [];
});

describe('EmptyState usage', () => {
  it('never passes `description` (the prop is `body`)', () => {
    const offenders = [];
    for (const path of files(SRC)) {
      const text = readFileSync(path, 'utf8');
      for (const match of text.matchAll(/<EmptyState\b([\s\S]*?)\/>/g)) {
        if (/\bdescription\s*=/.test(match[1])) offenders.push(path.replace(SRC, 'src'));
      }
    }
    expect(offenders).toEqual([]);
  });
});
