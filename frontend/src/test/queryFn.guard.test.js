import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/**
 * Every `queryFn: <service>.<member>` must actually exist.
 *
 * The Assets launcher passed `inventoryService.dashboard`, but `dashboard`
 * lives on the neighbouring `assetLifecycle` export in the same file. The
 * property was simply `undefined`, so React Query never ran the query and
 * every tile showed "—" instead of a count. Nothing failed loudly: the only
 * symptom was a console line, on a page nobody had a test for.
 *
 * A typo'd member is indistinguishable from a missing one at runtime, so this
 * resolves each reference against the module it was imported from.
 */
const here = dirname(fileURLToPath(import.meta.url));
const srcRoot = resolve(here, '..');

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const full = join(dir, entry);
  if (statSync(full).isDirectory()) return walk(full);
  return /\.jsx?$/.test(entry) && !/\.test\./.test(entry) ? [full] : [];
});

/** `queryFn: foo.bar` / `mutationFn: foo.bar` -> [{ object, member }] */
const fnRefs = (source) =>
  [...source.matchAll(/(?:query|mutation)Fn:\s*([A-Za-z_$][\w$]*)\.([\w$]+)\s*[,\n}]/g)]
    .map((m) => ({ object: m[1], member: m[2] }));

/** Which module each imported name came from. */
const importSources = (source) => {
  const map = {};
  for (const m of source.matchAll(/import\s*\{([^}]+)\}\s*from\s*'([^']+)'/g)) {
    for (const raw of m[1].split(',')) {
      const name = raw.trim().split(/\s+as\s+/).pop().trim();
      if (name) map[name] = m[2];
    }
  }
  return map;
};

const files = walk(srcRoot);

describe('every queryFn resolves', () => {
  it('finds the source tree (guards against a vacuous sweep)', () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it('references only members the service actually exports', async () => {
    const broken = [];
    for (const file of files) {
      const source = readFileSync(file, 'utf8');
      const refs = fnRefs(source);
      if (!refs.length) continue;
      const imports = importSources(source);

      for (const { object, member } of refs) {
        const from = imports[object];
        // Only checks imported service objects; a locally-defined function or
        // an inline arrow is out of scope and fine.
        if (!from || !from.startsWith('.')) continue;
        const mod = await import(resolve(dirname(file), from));
        const target = mod[object] ?? mod.default;
        if (!target) continue;
        if (typeof target[member] !== 'function') {
          broken.push(`${file.slice(srcRoot.length + 1)}: ${object}.${member} is ${typeof target[member]}`);
        }
      }
    }
    expect(broken, `queryFn/mutationFn pointing at nothing:\n${broken.join('\n')}`).toEqual([]);
  });
});
