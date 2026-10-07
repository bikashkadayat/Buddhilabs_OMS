/**
 * Shared headless-Chrome launcher for the QA harness.
 *
 * WHY THIS EXISTS
 *
 * Two portability faults made the responsive gate unusable off Linux, and both
 * failed in ways that looked like product regressions rather than tooling:
 *
 *  1. The binary path was hardcoded to /usr/bin/google-chrome. On any machine
 *     without that exact path every width reported "page did not render" — a
 *     loud FAIL that reads as a real layout break.
 *
 *  2. Chrome >=132 on macOS writes the --dump-dom output and then does NOT
 *     exit. spawnSync waits for process exit, so the harness hung forever
 *     instead of failing. The DOM was correct and already on stdout; only the
 *     process teardown never came.
 *
 * So: resolve the binary across platforms, and treat "</html> has arrived on
 * stdout" as the completion signal rather than process exit.
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const CANDIDATES = [
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
  '/snap/bin/chromium',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
];

/** Absolute path to a usable Chrome, or exit(2) with what was tried. */
export function resolveChrome() {
  const override = process.env.CHROME;
  if (override) {
    if (!existsSync(override)) {
      console.error(`CHROME=${override} but no such file.`);
      process.exit(2);
    }
    return override;
  }
  const found = CANDIDATES.find((p) => existsSync(p));
  if (!found) {
    console.error('No Chrome/Chromium found. Set CHROME=/path/to/chrome and re-run.');
    console.error(`Looked in:\n  ${CANDIDATES.join('\n  ')}`);
    process.exit(2);
  }
  return found;
}

/**
 * Render `url` at `width`x`height` and resolve with the dumped DOM.
 *
 * Resolves on "</html>" rather than on exit, because Chrome on macOS does not
 * exit after --dump-dom. Always kills the child and removes its throwaway
 * profile, so neither leaks when a page is slow.
 */
export function dumpDom(chrome, url, { width, height, virtualTime = 4000, timeoutMs = 30000, extraArgs = [] }) {
  return new Promise((resolve) => {
    const profile = mkdtempSync(join(tmpdir(), 'nif-chrome-'));
    const child = spawn(chrome, [
      '--headless', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
      `--user-data-dir=${profile}`, `--virtual-time-budget=${virtualTime}`,
      ...(width && height ? [`--window-size=${width},${height}`] : []),
      '--dump-dom', ...extraArgs, url,
    ], { stdio: ['ignore', 'pipe', 'ignore'] });

    let buf = '';
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { child.kill('SIGKILL'); } catch { /* already gone */ }
      // Chrome may still be flushing its profile as we unlink it; retry rather
      // than crash the whole gate on an ENOTEMPTY race.
      try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); } catch { /* best effort */ }
      resolve(value);
    };

    const timer = setTimeout(() => finish(buf), timeoutMs);
    child.stdout.on('data', (d) => { buf += d; if (buf.includes('</html>')) finish(buf); });
    child.on('error', () => finish(''));
    child.on('exit', () => finish(buf));
  });
}
