/**
 * Every FormData upload must set its own Content-Type.
 *
 * WHY THIS EXISTS. `services/api.js` creates the shared axios instance with a
 * DEFAULT of `Content-Type: application/json`. A request that posts FormData
 * without overriding it ships a multipart body labelled as JSON; Django's
 * parser finds no files, `request.FILES` is empty, and the endpoint returns
 * 400 "No file was supplied" in about ten milliseconds without reading the
 * file. Nothing throws, and the browser console shows a plain 400.
 *
 * Memo and minute uploads were broken this way for the whole life of the
 * feature. Circular and task uploads always passed the header and always
 * worked — the difference between them was one line.
 *
 * NO BACKEND TEST COULD HAVE CAUGHT IT. Django's test client builds its own
 * multipart body and never consults axios, so every server-side attachment
 * test passed against a feature that failed in every browser. The bug lives
 * exactly in the gap between the two, which is why the guard is here and not
 * there.
 *
 * The durable fix would be to drop the JSON default from api.js and let axios
 * infer per request. Until someone does that, this holds the line.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const SERVICES = join(here, '..', 'services');

const files = readdirSync(SERVICES).filter((f) => /\.jsx?$/.test(f) && !f.includes('.test.'));

const MEMO_TYPES = [
  'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt',
  'ppt', 'pptx', 'png', 'jpg', 'jpeg', 'webp', 'zip',
];

describe("the file picker offers what the server accepts", () => {
  it('memo upload inputs accept every supported type', () => {
    // Found by driving the real browser: the server had been widened to fifteen
    // types while both file inputs still said `accept=".pdf,.doc,.docx,.xls,
    // .xlsx,.csv"`. Uploading a PNG through the API worked; choosing one in the
    // file dialog did not show it. No API test can see this — `accept` never
    // reaches the server.
    const ROOT = join(here, '..');
    const targets = [
      join(ROOT, 'pages', 'memo', 'CreateMemo.jsx'),
      join(ROOT, 'components', 'memo', 'MemoSpecialActions.jsx'),
    ];
    const missing = [];
    for (const file of targets) {
      const m = /accept="([^"]+)"/.exec(readFileSync(file, 'utf8'));
      if (!m) { missing.push(`${file}: no accept attribute`); continue; }
      const listed = m[1].split(',').map((x) => x.trim().replace(/^\./, ''));
      const absent = MEMO_TYPES.filter((t) => !listed.includes(t));
      if (absent.length) missing.push(`${file.split('/').pop()}: missing ${absent.join(', ')}`);
    }
    expect(missing, 'The picker filter has drifted from the server allowlist.')
      .toEqual([]);
  });
});

describe('FormData uploads declare multipart', () => {
  it('every service that posts FormData sets Content-Type', () => {
    const offenders = [];
    for (const name of files) {
      const src = readFileSync(join(SERVICES, name), 'utf8');
      const code = src
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '');
      if (!/new FormData\(\)/.test(code)) continue;
      // Count the posts that hand a FormData variable to the client, and
      // the multipart declarations. Every such post needs one.
      //
      // MATCHED BY THE VARIABLE THE FILE ACTUALLY DECLARES, not by the name
      // `form`. The first version of this guard hard-coded that name, so
      // `brandingService.uploadAsset` — which calls its FormData `body` —
      // posted a multipart payload labelled as JSON for the whole of its
      // life with this guard green. A guard that only catches one spelling
      // of a mistake teaches people that the mistake is caught.
      const names = [...code.matchAll(/(?:const|let|var)\s+(\w+)\s*=\s*new FormData\(\)/g)]
        .map((m) => m[1]);
      const pattern = names.length
        ? new RegExp(`api\\.(post|put|patch)\\([^)]*\\b(${names.join('|')})\\b`, 'g')
        : /api\.(post|put|patch)\([^)]*\bform\b/g;
      const posts = (code.match(pattern) || []).length;
      // EITHER SPELLING COUNTS, because both work and both are in use.
      //
      //   'Content-Type': 'multipart/form-data'  — axios 1.x sees a FormData
      //       body, recognises the type and appends the boundary itself.
      //   'Content-Type': undefined              — axios deletes the header
      //       and the browser sets type AND boundary.
      //
      // Widening the variable match above turned `profileService`, which uses
      // the second form, into a false positive. A guard that flags correct
      // code gets the code changed to suit the guard, which is how a working
      // upload becomes a broken one.
      const declared = (code.match(
        /['"]Content-Type['"]:\s*(?:['"]multipart\/form-data['"]|undefined)/g,
      ) || []).length;
      if (posts > declared) {
        offenders.push(`services/${name}: ${posts} FormData post(s), ${declared} multipart header(s)`);
      }
    }
    expect(offenders, 'A FormData body posted through services/api.js inherits '
      + "its Content-Type: application/json default, so the server receives no "
      + 'files and answers 400. Pass '
      + "{ headers: { 'Content-Type': 'multipart/form-data' } } on the request.")
      .toEqual([]);
  });
});
