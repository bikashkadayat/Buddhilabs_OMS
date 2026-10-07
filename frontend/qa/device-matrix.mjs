#!/usr/bin/env node
/**
 * Phase 100.2 — multi-engine device certification.
 *
 * WHAT THIS IS, AND WHAT IT IS NOT
 *
 * It is NOT a physical device. No phone was plugged in, no cloud device farm was reached,
 * and nothing here can tell you how the app feels in a hand. Blocker 3 as written asks for
 * that, and this file does not close it.
 *
 * What it IS: the real RENDERING ENGINE behind each browser the brief names, driven at real
 * device metrics. That distinction matters more than it might sound, because layout bugs
 * are engine bugs — `100dvh`, `position: sticky` inside a scroll container, flex min-width
 * resolution and safe-area arithmetic are decided by the engine, not by the phone. Every
 * defect Phase 100 and 100.1 found was an engine-level layout fault.
 *
 *   Chrome Android     -> Chromium   (same engine)
 *   Samsung Internet   -> Chromium   (Samsung Internet is Chromium-based)
 *   Firefox Android    -> Gecko      (same engine; the Android build differs in chrome UI)
 *   Safari iPhone      -> WebKit     (same engine family as iOS Safari)
 *
 * The gap that remains after this: browser CHROME behaviour (the address bar actually
 * retracting, the real on-screen keyboard, iOS Safari's bottom bar) and true hardware
 * safe-area insets. Those are simulated here — honestly labelled as such — and simulation
 * of an input is not observation of a device.
 *
 * WHAT IT CHECKS, PER ENGINE x DEVICE:
 *
 *   1. safe-area insets at REAL device values.  env() is 0 in every headless browser, so
 *      the built CSS is re-served with env(safe-area-inset-*) rewritten to var(--sai-*),
 *      which this harness then sets to the actual insets of the device being emulated
 *      (iPhone 14 Pro: 59/34; Pixel gesture bar: 24; etc.). That exercises the SAME
 *      arithmetic the device would -- max(20px, 34px) -> 34px -- and answers the question
 *      Phase 100.1 could not: does anything get pushed out of view once the insets are
 *      non-zero.
 *   2. address bar collapse and expand, as a viewport-height change.
 *   3. on-screen keyboard, as a viewport-height reduction with a field focused.
 *   4. portrait and landscape.
 *   5. touch-target sizes and horizontal overflow, via the existing probes.
 *   6. accessible names for every interactive control, from the engine's own
 *      accessibility tree -- not from the DOM, so it is what a screen reader would be told.
 *
 * Usage:  node qa/device-matrix.mjs [outdir]
 *         PW_ROOT=/path/to/playwright/install   (defaults to the scratch install)
 */
import { spawn, spawnSync } from 'node:child_process';
import { readdirSync, readFileSync, writeFileSync, mkdirSync, rmSync, cpSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(process.argv[2] || join(HERE, '..', 'device-out'));
const PORT = Number(process.env.PORT || 5403);

/* Playwright is intentionally NOT a dependency of this project: it pulls ~400MB of
   browser binaries, and the CI gate added in Phase 100.1 runs on the system Chrome.
   This harness is a certification tool, run deliberately, so it resolves Playwright
   from wherever it happens to be installed. */
const PW_ROOT = process.env.PW_ROOT
  || '/tmp/claude-1000/-home-dell-Desktop-NIFN-OMS/cdf78048-e08b-4243-9230-c3c7cc264fa0/scratchpad/pw';
const require = createRequire(join(PW_ROOT, 'package.json'));
const { chromium, firefox, webkit, devices } = require('playwright');

/* --------------------------------------------------------------------------
   Devices, with their REAL safe-area insets.
   Values are the published CSS-pixel insets for each handset in portrait.
   Android gesture navigation reports a bottom inset only; the notch/Island
   devices report a top inset too, and both side insets in landscape.
   -------------------------------------------------------------------------- */
const MATRIX = [
  { device: 'iPhone 14 Pro',   label: 'iPhone 14 Pro (Dynamic Island)', insets: { top: 59, bottom: 34, left: 0, right: 0 } },
  { device: 'iPhone SE',       label: 'iPhone SE (no notch)',           insets: { top: 20, bottom: 0,  left: 0, right: 0 } },
  { device: 'Pixel 7',         label: 'Pixel 7 (gesture bar)',          insets: { top: 24, bottom: 24, left: 0, right: 0 } },
  { device: 'Galaxy S24',      label: 'Galaxy S24 (gesture bar)',       insets: { top: 24, bottom: 24, left: 0, right: 0 } },
  { device: 'Galaxy S9+',      label: 'Galaxy S9+ (3-button nav, 320px)', insets: { top: 24, bottom: 48, left: 0, right: 0 } },
  /* Landscape puts the notch on a SIDE, which is the case the page gutter has to
     clear -- the one that makes text run under the camera if it is missed. */
  { device: 'iPhone 14 Pro landscape', label: 'iPhone 14 Pro landscape (side notch)',
    insets: { top: 0, bottom: 21, left: 59, right: 59 } },
];

const ENGINES = [
  { name: 'Chromium (Chrome Android, Samsung Internet)', key: 'chromium', type: chromium },
  { name: 'Gecko (Firefox Android)',                     key: 'firefox',  type: firefox },
  { name: 'WebKit (Safari iPhone)',                      key: 'webkit',   type: webkit },
];

/* The workflows Phase 100.2 names. `click` opens the modal or panel that carries the
   action, using the harness mechanism added in Phase 100.1. */
const SCENARIOS = [
  { module: 'Memo',      screen: 'memo-actionable',       click: 'Support',     name: 'Support memo' },
  { module: 'Memo',      screen: 'memo-actionable',       click: 'Reject',      name: 'Reject memo' },
  { module: 'Memo',      screen: 'memo-actionable',       click: 'Withdraw',    name: 'Withdraw memo' },
  { module: 'Memo',      screen: 'memo-create',                                 name: 'Create memo' },
  { module: 'Memo',      screen: 'memo-detail',                                 name: 'Archived memo detail' },
  { module: 'Minute',    screen: 'create-minute',                               name: 'Create minute' },
  { module: 'Minute',    screen: 'minute-detail',                               name: 'Minute detail (approve/ack)' },
  { module: 'Minute',    screen: 'decision-register',                           name: 'Decision closure register' },
  { module: 'Minute',    screen: 'action-register',                             name: 'Action closure register' },
  { module: 'Minute',    screen: 'acknowledgement-register',                    name: 'Acknowledge minute' },
  { module: 'Circular',  screen: 'circular-create',                             name: 'Create circular' },
  { module: 'Circular',  screen: 'circular-detail',                             name: 'Issue / broadcast / acknowledge' },
  { module: 'Leave',     screen: 'leave-apply',                                 name: 'Apply for leave' },
  { module: 'Leave',     screen: 'leave-pending',                               name: 'Recommend queue' },
  { module: 'Leave',     screen: 'leave-review-drawer', click: 'Reject',        name: 'Approve / reject leave' },
  { module: 'Leave',     screen: 'leave-my-applications', click: 'Preview PDF', name: 'Leave documents' },
  { module: 'Inventory', screen: 'inventory-requests',                          name: 'Asset request' },
  { module: 'Inventory', screen: 'inventory-maintenance',                       name: 'Maintenance' },
  { module: 'Inventory', screen: 'inventory-dashboard',                         name: 'Inventory dashboard' },
  { module: 'Notifs',    screen: 'notifications',        click: 'Preferences',  name: 'Notification preferences' },
];

// ---------------------------------------------------------------------------
// Serve dist-qa with an extra stylesheet that makes env() controllable.
// ---------------------------------------------------------------------------
const distQa = join(HERE, '..', 'dist-qa');
let builtCss;
try {
  const f = readdirSync(join(distQa, 'assets')).find((n) => n.startsWith('visual-qa') && n.endsWith('.css'));
  builtCss = readFileSync(join(distQa, 'assets', f), 'utf8');
} catch {
  console.error('No built QA harness — run `npm run qa:build` first.');
  process.exit(2);
}

/* Only the DECLARATIONS that mention an inset are re-emitted, so this is an override
   layer rather than a second copy of a 190KB stylesheet. Appended last, so at equal
   specificity it wins -- which is exactly how a cascade override is supposed to work
   and keeps the original rules (and their comments) untouched. */
const insetRules = [];
{
  // Match `selector-list { ... }` blocks that contain an inset, keeping the selector.
  const blocks = builtCss.match(/[^{}]+\{[^{}]*env\(safe-area-inset-[^{}]*\}/g) || [];
  for (const block of blocks) {
    const at = block.indexOf('{');
    const selector = block.slice(0, at).trim();
    const body = block.slice(at + 1, -1);
    const decls = body.split(';').filter((d) => d.includes('env(safe-area-inset'));
    if (!selector || !decls.length) continue;
    insetRules.push(`${selector}{${decls.join(';').replace(
      /env\(safe-area-inset-(\w+)\)/g, 'var(--sai-$1)')}}`);
  }
}
if (!insetRules.length) {
  console.error('No safe-area rules found in the built CSS — nothing to simulate.');
  process.exit(2);
}
/* Media queries are lost by the flat match above, so each override is re-wrapped in the
   mobile query the originals live in. Every safe-area rule in index.css sits inside
   `@media (max-width: 1023px)`, and every device in the matrix is narrower than that. */
const INSET_CSS = `@media (max-width: 1023px){${insetRules.join('\n')}}`;

const work = join(OUT, '_serve');
rmSync(OUT, { recursive: true, force: true });
mkdirSync(work, { recursive: true });
cpSync(distQa, work, { recursive: true });
writeFileSync(join(work, 'safe-area-sim.css'), INSET_CSS);

const server = spawn('python3', ['-m', 'http.server', String(PORT), '--directory', work], { stdio: 'ignore' });
process.on('exit', () => server.kill());
{
  const deadline = Date.now() + 10_000;
  for (;;) {
    if (spawnSync('curl', ['-sf', `http://127.0.0.1:${PORT}/visual-qa.html`]).status === 0) break;
    if (Date.now() > deadline) { console.error('server did not start'); process.exit(2); }
    spawnSync('sleep', ['0.1']);
  }
}

// ---------------------------------------------------------------------------
// In-page assertions. Everything is measured against the VISUAL viewport, because
// that is what the user can actually see once insets and the keyboard are involved.
// ---------------------------------------------------------------------------
const AUDIT = ({ insets }) => {
  const out = { findings: [], counts: {} };
  const vh = window.innerHeight;
  const vw = window.innerWidth;

  /** Nearest ancestor (up to and including `stop`) that actually scrolls vertically. */
  const scrollableAncestorY = (el, stop) => {
    for (let n = el.parentElement; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (/auto|scroll/.test(s.overflowY) && n.scrollHeight > n.clientHeight + 1) return n;
      if (n === stop) break;
    }
    return null;
  };

  const describe = (el) => {
    const cls = (el.className || '').toString().trim().split(/\s+/).slice(0, 2).join('.');
    const text = (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 34);
    return `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ''}${text ? ` "${text}"` : ''}`;
  };

  // --- 0. reset scroll, so every state measures from the same baseline ------
  // AUDIT runs four times per page (portrait, address bar expanded, collapsed,
  // keyboard) and section 4 SCROLLS controls into view. Without this reset the second
  // state inherits the first's scroll position, and a control parked mid-scroll gets
  // reported as sitting under an inset when it is simply where the last check left it.
  // That is what produced the leave drawer's phantom "Approve under the gesture bar".
  document.querySelectorAll('*').forEach((el) => {
    if (el.scrollTop) el.scrollTop = 0;
  });
  window.scrollTo(0, 0);

  // --- 1. horizontal growth -------------------------------------------------
  // Measured on #root, NOT documentElement. The QA harness appends its own findings
  // box (`<pre id="overflow-report">`) to document.body, and long JSON lines give it a
  // scrollWidth of ~600px that no wrapping can break — which inflated the document and
  // reported 40 phantom overflows, all on screens that had real findings to print.
  // The app is #root; the report box is the instrument.
  const root = document.getElementById('root');
  if (root && root.scrollWidth > vw + 1) {
    out.findings.push({ kind: 'page-scrolls-sideways', need: root.scrollWidth, have: vw });
  }

  // --- 2. anything sitting under a hardware inset --------------------------
  // The safe area is the viewport minus the insets. A control whose box intrudes into
  // that margin is under the notch, the Island or the gesture bar. Only STICKY/FIXED
  // elements are judged: a normally-flowed control scrolls out from under the bar,
  // a pinned one never does.
  const pinned = [...document.querySelectorAll('*')].filter((el) => {
    const p = getComputedStyle(el).position;
    return p === 'fixed' || p === 'sticky';
  });
  out.counts.pinned = pinned.length;
  for (const el of pinned) {
    const b = el.getBoundingClientRect();
    if (b.width === 0 || b.height === 0) continue;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    // Does it carry interactive content? A decorative pinned bar under the gesture
    // bar is cosmetic; a BUTTON under it is a lost action, which is the defect.
    const actions = el.querySelectorAll('button, a[href], input, select, textarea, [role=button]');
    if (!actions.length) continue;
    for (const a of actions) {
      const ab = a.getBoundingClientRect();
      if (ab.width === 0 || ab.height === 0) continue;
      // ONLY controls that are currently on screen. A pinned container (the leave
      // drawer's fixed overlay, say) scrolls its own body, so a button at y=1377 in a
      // 660px viewport is simply scrolled away — not sitting under the gesture bar.
      // Without this the check reported 246 findings that were all the same mistake.
      if (ab.bottom < 0 || ab.top > vh) continue;
      // And skip anything inside a container that genuinely scrolls. A modal body in
      // landscape with the keyboard up has ~190px of height for a header, a labelled
      // textarea and two buttons; the textarea WILL extend past the fold, and the
      // body scrolls so the user reaches it. That is a physical constraint of the
      // viewport, not a layout fault — the defect this check exists for is a control
      // pinned under the hardware bar with no way to move it.
      if (scrollableAncestorY(a, el)) continue;
      if (ab.bottom > vh - insets.bottom + 1) {
        out.findings.push({ kind: 'action-under-bottom-inset', el: describe(a),
          bottom: Math.round(ab.bottom), safeBottom: Math.round(vh - insets.bottom) });
      }
      if (ab.top < insets.top - 1) {
        out.findings.push({ kind: 'action-under-top-inset', el: describe(a),
          top: Math.round(ab.top), safeTop: insets.top });
      }
      if (ab.left < insets.left - 1 || ab.right > vw - insets.right + 1) {
        out.findings.push({ kind: 'action-under-side-inset', el: describe(a),
          left: Math.round(ab.left), right: Math.round(ab.right),
          safeLeft: insets.left, safeRight: Math.round(vw - insets.right) });
      }
    }
  }

  // --- 3. touch targets ----------------------------------------------------
  const seen = new Set();
  const boxInputs = new Set(['checkbox', 'radio']);
  for (const el of document.querySelectorAll(
    'button, a[href], input:not([type=hidden]), select, textarea, [role=button]')) {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || el.disabled) continue;
    if (el.tagName === 'A' && s.display === 'inline') continue;
    if (el.closest('.sr-only')) continue;
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height) continue;
    const min = (el.tagName === 'INPUT' && boxInputs.has(el.type)) ? 24 : 44;
    if (b.height < min - 0.5 || b.width < min - 0.5) {
      const key = `${el.tagName}.${el.className}|${Math.round(b.width)}x${Math.round(b.height)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.findings.push({ kind: 'touch-target-too-small', el: describe(el),
        w: Math.round(b.width), h: Math.round(b.height), need: min });
    }
  }

  // --- 4. is the primary action reachable at all? --------------------------
  // The question is not "is it on screen now" but "can the user GET to it". So each
  // candidate is scrolled into view first and judged afterwards.
  //
  // `scrollIntoView` rather than scrolling the document: a modal and the leave drawer
  // scroll their OWN body, and driving document.scrollingElement leaves them exactly
  // where they were. Judging them then reports every drawer button as unreachable,
  // which is what the first run of this harness did — 48 findings, all wrong.
  const all = [...document.querySelectorAll('button:not([disabled]), [role=button]')]
    .filter((el) => {
      const s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden'
        && el.getBoundingClientRect().height > 0 && !el.closest('.sr-only');
    });
  out.counts.actions = all.length;
  const unreachable = [];
  const inSafeArea = (b) => b.top >= insets.top - 1 && b.bottom <= vh - insets.bottom + 1
    && b.left >= insets.left - 1 && b.right <= vw - insets.right + 1;
  for (const el of all) {
    // Fast path: already fully inside the safe area, so there is nothing to prove and
    // no need to scroll. Scrolling every control unconditionally made a full matrix
    // run exceed ten minutes; almost all of them are already fine.
    if (inSafeArea(el.getBoundingClientRect())) continue;
    // `center`, not `nearest`. `nearest` scrolls the element just far enough to touch
    // the viewport EDGE — which is where the insets are — so a perfectly reachable
    // button at the foot of a page reports as unreachable simply because that is where
    // `nearest` parked it. `center` asks the real question: can the user bring this
    // control into the middle of the safe area? Anything that still cannot is either
    // pinned under an inset or in a container too short to scroll.
    el.scrollIntoView({ block: 'center', inline: 'center' });
    const b = el.getBoundingClientRect();
    // After being scrolled to, it must sit inside the SAFE area — not merely inside
    // the viewport. A button that lands under the gesture bar is one the user can see
    // and cannot reliably tap.
    if (b.bottom > vh - insets.bottom + 1 || b.top < insets.top - 1
        || b.top > vh || b.bottom < 0) {
      unreachable.push({ el: describe(el), top: Math.round(b.top),
                         bottom: Math.round(b.bottom) });
    }
  }
  if (unreachable.length) {
    out.findings.push({ kind: 'action-unreachable-after-scroll',
      count: unreachable.length, examples: unreachable.slice(0, 3),
      safeTop: insets.top, safeBottom: Math.round(vh - insets.bottom) });
  }

  out.chars = (document.getElementById('root')?.textContent || '').trim().length;
  out.viewport = { w: vw, h: vh };
  return out;
};

// ---------------------------------------------------------------------------
// Run
// ---------------------------------------------------------------------------
const results = [];
const skipped = [];
console.log('Phase 100.2 — multi-engine device certification');
console.log(`${MATRIX.length} device profiles x ${SCENARIOS.length} scenarios per engine\n`);

let crashed = 0;
for (const engine of ENGINES) {
  let browser;
  try {
    browser = await engine.type.launch();
  } catch (e) {
    const why = /missing dependencies/i.test(String(e))
      ? 'host is missing system libraries for this engine'
      : String(e).split('\n')[0];
    skipped.push({ engine: engine.name, why });
    console.log(`${engine.name}\n  SKIPPED — ${why}\n`);
    continue;
  }
  console.log(`${engine.name}  [${browser.version()}]`);

  /* WebKit under WPE with no GPU is fragile: it logs EGL failures and occasionally
     takes the whole browser process down mid-run, which killed the first three-engine
     attempt outright on `newPage`. A dead browser is recoverable — relaunch it and
     carry on — whereas an uncaught throw discards the results of the two engines that
     already passed. */
  const ensureBrowser = async () => {
    if (browser && browser.isConnected()) return browser;
    try { await browser?.close(); } catch { /* already gone */ }
    browser = await engine.type.launch();
    return browser;
  };

  for (const profile of MATRIX) {
    const descriptor = devices[profile.device];
    await ensureBrowser();
    const context = await browser.newContext({
      ...descriptor,
      // Firefox does not implement touch emulation; forcing it throws at context
      // creation and would cost the whole engine rather than one flag.
      hasTouch: engine.key === 'firefox' ? false : descriptor.hasTouch,
      isMobile: engine.key === 'firefox' ? false : descriptor.isMobile,
    });
    // The inset values for the device under test, applied to every page in the context.
    await context.addInitScript(({ insets }) => {
      window.__INSETS__ = insets;
    }, { insets: profile.insets });

    let deviceFindings = 0;
    for (const scenario of SCENARIOS) {
      let page;
      try {
        page = await context.newPage();
      } catch (e) {
        // The context died with its browser. Record it and move on: a lost scenario
        // is a gap in coverage, not a reason to lose the whole matrix.
        results.push({ engine: engine.key, engineName: engine.name, device: profile.label,
                       module: scenario.module, scenario: scenario.name,
                       screen: scenario.screen, total: 1,
                       error: `engine crashed: ${String(e).split('\n')[0].slice(0, 120)}` });
        deviceFindings += 1;
        crashed += 1;
        break;              // the rest of this context is gone too
      }
      const url = `http://127.0.0.1:${PORT}/visual-qa.html?screen=${scenario.screen}`
        + (scenario.click ? `&click=${encodeURIComponent(scenario.click)}` : '');
      try {
        await page.goto(url, { waitUntil: 'load', timeout: 30_000 });
        // The harness stamps this AFTER its own probe has run, which is later than load.
        await page.waitForSelector('#overflow-report', { timeout: 30_000 });
        // Apply the simulated insets: the override sheet plus the values themselves.
        await page.addStyleTag({ url: `http://127.0.0.1:${PORT}/safe-area-sim.css` });
        await page.addStyleTag({ content: `:root{
          --sai-top:${profile.insets.top}px; --sai-bottom:${profile.insets.bottom}px;
          --sai-left:${profile.insets.left}px; --sai-right:${profile.insets.right}px; }` });
        await page.waitForTimeout(180);

        const base = await page.evaluate(AUDIT, { insets: profile.insets });
        const row = { engine: engine.key, engineName: engine.name, device: profile.label,
                      module: scenario.module, scenario: scenario.name,
                      screen: scenario.screen, states: {} };
        row.states.portrait = base;

        // --- browser chrome states -----------------------------------------
        // The address bar retracting on scroll and the keyboard opening are BOTH just
        // a change in visible height as far as layout is concerned. Simulating them as
        // viewport resizes exercises the same code path (100dvh, sticky bottom bars)
        // without a real toolbar. That is the honest limit of this check.
        const vp = page.viewportSize();
        for (const [state, height] of [
          ['addressBarExpanded', Math.round(vp.height * 0.88)],   // toolbar showing
          ['addressBarCollapsed', vp.height],                     // toolbar retracted
          ['keyboardOpen', Math.round(vp.height * 0.55)],         // ~45% eaten by the IME
        ]) {
          await page.setViewportSize({ width: vp.width, height });
          await page.waitForTimeout(120);
          row.states[state] = await page.evaluate(AUDIT, { insets: profile.insets });
        }
        await page.setViewportSize(vp);

        // --- accessibility tree ---------------------------------------------
        // The ARIA tree, not the DOM: an accessible NAME is what a screen reader
        // announces, and it can be absent even when the DOM looks fine (an icon-only
        // button whose only content is an <svg>). `page.accessibility` was removed in
        // Playwright 1.62; `ariaSnapshot()` is its replacement and renders a named
        // control as `- button "Save"` and an unnamed one as a bare `- button`.
        const INTERACTIVE_ROLES = ['button', 'link', 'checkbox', 'radio', 'textbox',
                                   'combobox', 'menuitem', 'switch', 'slider', 'spinbutton'];
        const snapshot = await page.locator('body').ariaSnapshot();
        const unnamed = [];
        for (const line of snapshot.split('\n')) {
          // `- role "name"` / `- role "name": text` / `- role` / `- role:` .
          const m = /^\s*-\s+([a-z]+)(\s*"[^"]*")?/.exec(line);
          if (!m) continue;
          const [, role, name] = m;
          if (!INTERACTIVE_ROLES.includes(role)) continue;
          if (!name || !name.trim().replace(/"/g, '')) unnamed.push(role);
        }
        row.unnamedControls = unnamed;
        if (unnamed.length) {
          row.states.portrait.findings.push({
            kind: 'control-without-accessible-name',
            roles: [...new Set(unnamed)], count: unnamed.length,
          });
        }

        const total = Object.values(row.states).reduce((n, s) => n + s.findings.length, 0);
        row.total = total;
        deviceFindings += total;
        results.push(row);

        if (total) {
          // Viewport-clipped, not fullPage: a minute detail page is ~40,000px tall and
          // both engines refuse to rasterise past 32767, which failed 8 runs as an
          // "error" that was really a screenshot limit. The viewport is also the more
          // useful evidence here — the question is what the user sees, not the whole page.
          await page.screenshot({
            path: join(OUT, `${engine.key}-${profile.device.replace(/[^\w]+/g, '_')}`
              + `-${scenario.screen}${scenario.click ? `-${scenario.click.replace(/\W+/g, '')}` : ''}.png`),
          });
        }
      } catch (e) {
        results.push({ engine: engine.key, engineName: engine.name, device: profile.label,
                       module: scenario.module, scenario: scenario.name,
                       screen: scenario.screen, error: String(e).split('\n')[0].slice(0, 160),
                       total: 1 });
        deviceFindings += 1;
      } finally {
        await page.close();
      }
    }
    await context.close();
    console.log(`  ${profile.label.padEnd(38)} ${deviceFindings ? `${deviceFindings} finding(s)` : 'clean'}`);
  }
  await browser.close();
  console.log('');
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------
mkdirSync(OUT, { recursive: true });
writeFileSync(join(OUT, 'device-matrix.json'),
  JSON.stringify({ results, skipped, matrix: MATRIX, scenarios: SCENARIOS }, null, 1));

const withFindings = results.filter((r) => r.total > 0);
const kinds = {};
for (const r of withFindings) {
  if (r.error) { kinds.error = (kinds.error || 0) + 1; continue; }
  for (const s of Object.values(r.states || {})) {
    for (const f of s.findings) kinds[f.kind] = (kinds[f.kind] || 0) + 1;
  }
}

console.log('='.repeat(66));
console.log(`runs: ${results.length}   clean: ${results.length - withFindings.length}`
  + `   with findings: ${withFindings.length}`);
if (Object.keys(kinds).length) {
  console.log('\nfindings by kind:');
  for (const [k, n] of Object.entries(kinds).sort((a, b) => b[1] - a[1])) {
    console.log(`  ${String(n).padStart(5)}  ${k}`);
  }
} else {
  console.log('\nno findings in any engine, on any device profile, in any chrome state');
}
if (crashed) {
  console.log(`\n${crashed} scenario(s) lost to an engine crash — coverage gap, not a defect.`);
}
if (skipped.length) {
  console.log('\nENGINES NOT TESTED:');
  for (const s of skipped) console.log(`  ${s.engine}\n    ${s.why}`);
}
console.log(`\nreport: ${join(OUT, 'device-matrix.json')}`);
console.log('NOTE: emulated engines at real device metrics with simulated insets.');
console.log('      This is NOT a physical device and does not close Blocker 3.');
process.exit(withFindings.length ? 1 : 0);
