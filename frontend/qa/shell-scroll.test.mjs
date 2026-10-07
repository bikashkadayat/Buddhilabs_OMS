#!/usr/bin/env node
/**
 * Phase 100.16 — app-shell scroll reachability, as an automated gate.
 *
 * WHY THIS IS SEPARATE FROM THE OVERFLOW HARNESS
 *
 * qa/capture.sh mounts each screen inside `<div class="shell" style="height:auto">`,
 * which is correct for the question IT asks (does content overflow sideways) but makes
 * it structurally blind to the question asked here: `height:auto` is precisely the
 * property whose absence broke mobile. The harness reported all 25 screens clean at
 * 360px while every control below ~565px was unreachable on a real phone.
 *
 * So this test renders the REAL shell chrome, unmodified, with a page taller than the
 * viewport, and asserts the only thing that actually matters: after the user has tried
 * every scroll gesture available, can they reach the bottom of the page?
 *
 * Run: node qa/shell-scroll.test.mjs     (needs `npm run qa:build` first, for the CSS)
 */
import { spawn, spawnSync } from 'node:child_process';
import { resolveChrome, dumpDom } from './chrome.mjs';
import { mkdtempSync, writeFileSync, rmSync, readdirSync, copyFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const CHROME = resolveChrome();
const PORT = Number(process.env.PORT || 5399);

/* The brief's widths. 1440 and 1024 are here as REGRESSION guards, not mobile cases:
   desktop scrolls `.main` rather than the document, and this must not silently flip
   to document scrolling when someone edits the mobile block. */
const WIDTHS = [1440, 1024, 768, 480, 430, 390, 375, 360];
const VIEWPORT_H = 780;

const PAGE = (css) => `<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<style>${css}</style></head><body><div id="root">
  <header class="header"><div class="hd-brand">NIF</div><div class="hd-right">R</div></header>
  <div class="shell">
    <nav class="sidebar"></nav>
    <main class="main"><div class="page">
      <h1>TOP</h1>
      <div style="height:2400px"></div>
      <div class="memo-actionbar"><button class="btn btn-primary">APPROVE</button></div>
      <h2 id="bottom">BOTTOM</h2>
    </div></main>
  </div>
</div><pre id="out"></pre><script>
const main = document.querySelector('.main');
const bottom = document.getElementById('bottom');
const bar = document.querySelector('.memo-actionbar');
// Exhaust every scroll mechanism a user has: the inner pane and the document.
main.scrollTop = 1e6; window.scrollTo(0, 1e6);
document.getElementById('out').textContent = JSON.stringify({
  innerH: innerHeight,
  // Reachable = the bottom of the page has been brought into the viewport.
  bottomReached: bottom.getBoundingClientRect().top <= innerHeight,
  // The pinned action bar must sit inside the viewport, not below it.
  actionBarTop: Math.round(bar.getBoundingClientRect().top),
  actionBarVisible: bar.getBoundingClientRect().top <= innerHeight
                 && bar.getBoundingClientRect().bottom >= 0,
  // Sideways growth is a separate failure and worth catching in the same pass.
  pageScrollsSideways: document.documentElement.scrollWidth > innerWidth + 1,
});
</script></body></html>`;

// --- locate the built CSS -----------------------------------------------------
const distDir = join(HERE, '..', 'dist-qa', 'assets');
let cssFile;
try {
  cssFile = readdirSync(distDir).find((f) => f.startsWith('visual-qa') && f.endsWith('.css'));
} catch { /* handled below */ }
if (!cssFile) {
  console.error('No built CSS in dist-qa/assets — run `npm run qa:build` first.');
  process.exit(2);
}

const work = mkdtempSync(join(tmpdir(), 'nif-shell-'));
copyFileSync(join(distDir, cssFile), join(work, 'app.css'));
const css = `@import url("./app.css");`;
writeFileSync(join(work, 'index.html'), PAGE(css));

const server = spawn('python3', ['-m', 'http.server', String(PORT), '--directory', work],
  { stdio: 'ignore' });
const cleanup = () => { server.kill(); rmSync(work, { recursive: true, force: true }); };
process.on('exit', cleanup);

// Wait for the port rather than sleeping a guess.
const deadline = Date.now() + 10_000;
for (;;) {
  const probe = spawnSync('curl', ['-sf', `http://127.0.0.1:${PORT}/index.html`]);
  if (probe.status === 0) break;
  if (Date.now() > deadline) { console.error('server did not start'); process.exit(2); }
  spawnSync('sleep', ['0.1']);
}

let failures = 0;
console.log(`Phase 100.16 — app-shell scroll reachability (viewport height ${VIEWPORT_H})\n`);

for (const width of WIDTHS) {
  const dom = await dumpDom(CHROME, `http://127.0.0.1:${PORT}/index.html`,
    { width, height: VIEWPORT_H });

  const match = /<pre id="out">(.*?)<\/pre>/s.exec(dom);
  if (!match) {
    console.log(`  ${String(width).padStart(4)}px  FAIL — page did not render`);
    failures += 1;
    continue;
  }
  const r = JSON.parse(match[1]);
  const problems = [];
  if (!r.bottomReached) problems.push('bottom of page UNREACHABLE by any scroll');
  if (!r.actionBarVisible) problems.push(`action bar off-viewport (top=${r.actionBarTop})`);
  if (r.pageScrollsSideways) problems.push('page scrolls sideways');

  if (problems.length) {
    failures += 1;
    console.log(`  ${String(width).padStart(4)}px  FAIL — ${problems.join('; ')}`);
  } else {
    console.log(`  ${String(width).padStart(4)}px  ok   — bottom reachable, `
      + `action bar at y=${r.actionBarTop} of ${r.innerH}`);
  }
}

console.log(failures
  ? `\nRESULT: ${failures} width(s) failed`
  : '\nRESULT: every width scrolls to the bottom with all actions in view');
process.exit(failures ? 1 : 0);
