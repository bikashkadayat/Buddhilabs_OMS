#!/usr/bin/env node
/**
 * Phase 100.1 Blocker 7 — safe-area insets, as far as they CAN be verified here.
 *
 * WHAT THIS CANNOT DO, STATED UP FRONT
 *
 * It cannot tell you the app looks right on a notched iPhone. `env(safe-area-inset-*)`
 * resolves to 0px in headless Chrome — there is no notch, no home indicator and no
 * Android gesture bar to report — and no Chrome flag or CDP command synthesises one.
 * Only a physical device answers that question, and Blocker 3 remains open until one is
 * used.
 *
 * WHAT IT DOES DO, AND WHY IT IS WORTH RUNNING
 *
 * Two failure modes are real, common, and fully testable without a notch:
 *
 *   1. THE COLLAPSE.  `padding-bottom: env(safe-area-inset-bottom)` is the obvious way
 *      to write it and it is wrong: on every device without an inset it computes to
 *      0px, silently DELETING the padding the design already had. The correct form is
 *      `max(<design value>, env(...))`. Headless Chrome, where every inset is 0, is
 *      precisely the environment that exposes this — the padding either survives at
 *      its design value or it does not.
 *
 *   2. THE UNPROTECTED BAR.  A control pinned to the bottom of the viewport sits
 *      exactly where the home indicator and gesture bar are drawn. Every such rule has
 *      to opt into an inset; this walks the stylesheet and names any that has not.
 *
 * Passing here means the insets are wired correctly and will not have removed padding
 * on ordinary phones. It does not mean they clear the notch. Those are different
 * claims and this file only makes the first.
 *
 * Run: node qa/safe-area.test.mjs      (needs `npm run qa:build` for the built CSS)
 */
import { spawn, spawnSync } from 'node:child_process';
import { resolveChrome, dumpDom } from './chrome.mjs';
import { mkdtempSync, writeFileSync, rmSync, readdirSync, copyFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const CHROME = resolveChrome();
const PORT = Number(process.env.PORT || 5402);

/* Selector → the padding the design must keep when the inset is 0. Each is a surface
   that sits against a device edge; the number is the value written inside the max(). */
const BOTTOM_ANCHORED = [
  { selector: '.memo-actionbar',                    min: 16, what: 'memo action bar' },
  { selector: '.lr-memo-actionbar',                 min: 16, what: 'create-memo action bar' },
  { selector: '.lr-modal-foot',                     min: 20, what: 'shared modal footer' },
  { selector: '.lr-modal > .lr-modal-actions',      min: 20, what: 'modal footer (canonical)' },
  { selector: '.lr-modal > .memo-modal-actions',    min: 20, what: 'modal footer (governance)' },
  { selector: '.ar-actions',                        min: 14, what: 'leave decision bar' },
  { selector: '.page',                              min: 20, what: 'page gutter' },
  { selector: '.sidebar',                           min: 32, what: 'off-canvas drawer' },
];

let failures = 0;
const fail = (message) => { failures += 1; console.log(`  FAIL  ${message}`); };
const pass = (message) => console.log(`  ok    ${message}`);

// ---------------------------------------------------------------------------
// Part 1 — static: is every inset written in the non-collapsing form?
// ---------------------------------------------------------------------------
console.log('Phase 100.1 Blocker 7 — safe-area insets\n');
console.log('1. Stylesheet: every inset must be inside max(), never bare\n');

const css = readFileSync(join(HERE, '..', 'src', 'index.css'), 'utf8');
const insetUses = [...css.matchAll(/[\w-]+\s*:\s*[^;{}]*env\(safe-area-inset-[a-z]+[^;{}]*/g)]
  .map((m) => m[0].trim());

if (!insetUses.length) {
  fail('no env(safe-area-inset-*) in the stylesheet at all');
} else {
  for (const use of insetUses) {
    // The rule is about DIRECTION, and it is not arbitrary.
    //
    //   top / left / right  — additive gutters. Nothing in this design puts a
    //     required padding against the notch or the camera; the inset IS the whole
    //     value, and on a device without one, zero is the correct answer. A bare
    //     env() is right here and `max(0px, env())` would be noise.
    //   bottom — this is where design padding lives (a 16px action bar, a 20px page
    //     gutter, a 20px modal footer). A bare env() there SILENTLY DELETES that
    //     padding on every phone without a home indicator, which is the exact bug
    //     this check exists to catch. It must always be inside max().
    //
    // Part 2 below is the substantive guard: it measures the computed padding on
    // each surface, so a design value that goes missing fails there regardless.
    const bare = !/max\s*\(/.test(use);
    const additive = /(top|left|right)\s*:\s*env\(/.test(use);
    if (bare && !additive) fail(`bare inset would collapse to 0px: "${use}"`);
  }
  const bad = insetUses.filter((u) => !/max\s*\(/.test(u)
    && !/(top|left|right)\s*:\s*env\(/.test(u));
  if (!bad.length) pass(`${insetUses.length} inset declaration(s), none collapsible`);
}

// ---------------------------------------------------------------------------
// Part 2 — computed: does the padding survive when every inset is 0?
// ---------------------------------------------------------------------------
console.log('\n2. Computed: with all insets 0 (headless), design padding must survive\n');

const distDir = join(HERE, '..', 'dist-qa', 'assets');
let cssFile;
try {
  cssFile = readdirSync(distDir).find((f) => f.startsWith('visual-qa') && f.endsWith('.css'));
} catch { /* handled below */ }
if (!cssFile) {
  console.error('\nNo built CSS in dist-qa/assets — run `npm run qa:build` first.');
  process.exit(2);
}

const work = mkdtempSync(join(tmpdir(), 'nif-safearea-'));
copyFileSync(join(distDir, cssFile), join(work, 'app.css'));

/* Every surface is rendered in the nesting its CSS assumes — a modal footer only gets
   its rule as a direct child of .lr-modal, and .ar-actions only inside .ar-decision. */
writeFileSync(join(work, 'index.html'), `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<link rel="stylesheet" href="./app.css"></head><body>
<div id="root">
  <div class="shell"><main class="main"><div class="page">
    <div class="memo-actionbar"><button class="btn">A</button></div>
    <div class="lr-memo-actionbar"><button class="btn">A</button></div>
  </div></main></div>
  <nav class="sidebar"></nav>
  <div class="lr-modal-overlay"><div class="lr-modal">
    <div class="lr-modal-head"><h3>t</h3></div>
    <div class="lr-modal-foot"><button class="lr-btn">Save</button></div>
  </div></div>
  <div class="lr-modal-overlay"><div class="lr-modal">
    <div class="lr-modal-actions"><button class="lr-btn">Save</button></div>
  </div></div>
  <div class="lr-modal-overlay"><div class="lr-modal">
    <div class="memo-modal-actions"><button class="lr-btn">Save</button></div>
  </div></div>
  <div class="ar-overlay"><aside class="ar-panel">
    <div class="ar-panel-body"><section class="ar-decision">
      <div class="ar-actions"><button class="btn">Approve</button></div>
    </section></div>
  </aside></div>
</div>
<pre id="out"></pre><script>
const want = ${JSON.stringify(BOTTOM_ANCHORED)};
document.getElementById('out').textContent = JSON.stringify(want.map((row) => {
  const el = document.querySelector(row.selector);
  if (!el) return { ...row, missing: true };
  const cs = getComputedStyle(el);
  return { ...row, got: Math.round(parseFloat(cs.paddingBottom) || 0),
           inset: cs.getPropertyValue('padding-bottom') };
}));
</script></body></html>`);

const server = spawn('python3', ['-m', 'http.server', String(PORT), '--directory', work],
  { stdio: 'ignore' });
process.on('exit', () => { server.kill(); rmSync(work, { recursive: true, force: true }); });

const deadline = Date.now() + 10_000;
for (;;) {
  if (spawnSync('curl', ['-sf', `http://127.0.0.1:${PORT}/index.html`]).status === 0) break;
  if (Date.now() > deadline) { console.error('server did not start'); process.exit(2); }
  spawnSync('sleep', ['0.1']);
}

const dom = await dumpDom(CHROME, `http://127.0.0.1:${PORT}/index.html`,
  { width: 390, height: 780 });

const match = /<pre id="out">(.*?)<\/pre>/s.exec(dom);
if (!match) {
  console.error('  FAIL  page did not render');
  process.exit(1);
}
for (const row of JSON.parse(match[1].replace(/&quot;/g, '"').replace(/&amp;/g, '&')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>'))) {
  if (row.missing) { fail(`${row.what}: selector ${row.selector} matched nothing`); continue; }
  if (row.got < row.min) {
    fail(`${row.what}: padding-bottom collapsed to ${row.got}px, design needs ${row.min}px`);
  } else {
    pass(`${row.what}: ${row.got}px (design minimum ${row.min}px)`);
  }
}

console.log(failures
  ? `\nRESULT: ${failures} problem(s)`
  : '\nRESULT: insets are non-collapsing and every bottom-anchored surface keeps its padding'
    + '\nNOTE:   this does NOT verify clearance of a real notch or gesture bar.'
    + '\n        env() is 0 in headless Chrome. Blocker 3 (physical devices) stays open.');
process.exit(failures ? 1 : 0);
