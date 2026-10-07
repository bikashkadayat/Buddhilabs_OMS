/**
 * Phase FIX — the guard that would have caught the download bug.
 *
 * Nine export buttons across five pages were plain `<a href="/api/v1/...">`
 * links. This app authenticates with a JWT held in JavaScript, not a session
 * cookie, so a link navigation carries no Authorization header: every one of
 * them hit the API unauthenticated, got a 401, and the browser rendered that
 * JSON or saved it as the "file".
 *
 * Three tests covered those buttons and all three passed, because they asserted
 * a LINK EXISTED with the right href — which was true, and useless. A control
 * that does nothing still has an href.
 *
 * This is a source-level guard rather than a render test on purpose: it holds
 * for pages nobody has written a test for yet, which is where the next one
 * would appear.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, '..');

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const full = join(dir, entry);
  if (statSync(full).isDirectory()) return walk(full);
  return /\.jsx?$/.test(entry) && !/\.test\./.test(entry) ? [full] : [];
});

const FILES = walk(SRC);

/** `href` values that point at the API, with comments stripped. */
const apiHrefs = (source) => {
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
  const hits = [];
  for (const m of code.matchAll(/href=\{?["'`]?([^"'`}\n]*)/g)) {
    const value = m[1];
    if (value.includes('/api/') || /export\s*=/.test(value)) hits.push(value);
  }
  // A JSX expression like href={service.reportCsvUrl(x)} has no literal URL, so
  // catch the shape that produced this bug by name as well.
  for (const m of code.matchAll(/href=\{[^}]*(Url\()[^}]*\}/g)) hits.push(m[0]);
  return hits;
};

describe('downloads go through the authenticated client', () => {
  it('no page links straight at an API endpoint', () => {
    const offenders = [];
    for (const file of FILES) {
      const hits = apiHrefs(readFileSync(file, 'utf8'));
      if (hits.length) offenders.push(`${file.replace(SRC, 'src')}: ${hits[0]}`);
    }
    expect(offenders, 'An <a href> to the API sends no Authorization header on '
      + 'a JWT-authenticated API and downloads a 401 body. Fetch through the '
      + 'api client and hand the response to saveBlob — see '
      + 'components/common/ExportButtons.jsx.').toEqual([]);
  });

  it('no task file is reached by browser navigation', () => {
    // ADDED after the task-evidence 401 (Phase
    // CRITICAL-TASK-EVIDENCE-DOWNLOAD-BUG), which the check above did not
    // catch and could not have.
    //
    // WHY A MODULE-SCOPED RULE RATHER THAN A GENERAL ONE. Most modules serve
    // attachments as SIGNED media URLs, which carry their own credential and
    // are perfectly safe to put in an href. Tasks deliberately do not: they use
    // an authenticated, task-scoped view, because per-request authorisation is
    // what makes their download log possible. Both are correct; only one may be
    // linked to.
    //
    // From the source alone those two are indistinguishable — both arrive as a
    // property like `file.download_url` on an API response, with no literal
    // path to match. So the honest guard is the one that knows WHICH module
    // serves authenticated paths, rather than a clever pattern that would
    // either miss this or condemn the four modules that are fine.
    const offenders = [];
    for (const file of FILES) {
      if (!/[\\/](components|pages)[\\/]task[\\/]/.test(file)) continue;
      const code = readFileSync(file, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '');
      for (const m of code.matchAll(/href=\{([^}]+)\}/g)) {
        // `link_url` is an EXTERNAL address somebody pasted in — a real link to
        // a real elsewhere, and the one href in this module that belongs.
        if (/link_url/.test(m[1])) continue;
        offenders.push(`${file.replace(SRC, 'src')}: href={${m[1].trim()}}`);
      }
    }
    expect(offenders, 'Task attachments are served by an authenticated view, so '
      + 'an <a href> downloads a 401 body. Fetch with '
      + 'taskService.downloadAttachment and hand the response to saveBlob.')
      .toEqual([]);
  });

  it('every service method that fetches a file asks for a blob', () => {
    // A download helper that forgets `responseType: 'blob'` returns a decoded
    // string that saveBlob will happily wrap — producing a corrupt PDF that
    // opens to a blank page rather than an error anybody can act on.
    //
    // Scoped to a fixed window of lines after the key rather than trying to
    // parse a method body with a regex: the first version of this check stopped
    // at the `params:` line and reported `responseType` missing on code that
    // had it two lines further down. A guard that fires on correct code is one
    // the next person deletes.
    const offenders = [];
    for (const file of FILES.filter((f) => f.includes('/services/'))) {
      const lines = readFileSync(file, 'utf8').split('\n');
      lines.forEach((line, i) => {
        const key = /^ *(\w*[Dd]ownload\w*|\w*[Pp]df\w*|\w*[Gg]atePass\w*|\w*[Rr]eceipt\w*):/.exec(line);
        if (!key) return;
        const window = lines.slice(i, i + 8).join('\n');
        if (!/api\.(get|post)/.test(window)) return;
        if (/responseType/.test(window)) return;
        // A method that unwraps to `.data` is returning JSON by construction —
        // `getDownloadLog` reads the download AUDIT LOG, not a file. Matching
        // on the name alone would flag it forever.
        if (/\.then\(\(r\) => r\.data\)/.test(window)) return;
        offenders.push(`${file.replace(SRC, 'src')}: ${key[1]}`);
      });
    }
    expect(offenders, 'a file download without responseType: "blob" yields a '
      + 'corrupt file rather than a readable error').toEqual([]);
  });
});
