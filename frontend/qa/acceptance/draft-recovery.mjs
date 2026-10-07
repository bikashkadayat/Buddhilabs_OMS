/**
 * Phase 111.18/111.19 acceptance test, against the REAL running stack.
 *
 * Types a long memo, KILLS the browser process outright (SIGKILL — the closest
 * a test can get to a power cut), launches a brand-new browser with the same
 * profile, reopens the form, and checks the content came back.
 */
import WebSocket from 'ws';
import { spawn, execSync } from 'node:child_process';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const OUT = process.argv[2];
const APP = 'http://localhost:5173';
const API = 'http://localhost:8001/api/v1';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9400;
// One profile reused across both launches, so IndexedDB survives the kill the
// way a real user's browser profile does.
const profile = mkdtempSync(join(tmpdir(), 'accept-'));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const login = await (await fetch(`${API}/auth/login/`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email: 'demo.maker@example.test', password: 'Local@1234' }),
})).json();

let chrome = null;
async function launch() {
  chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`,
    '--no-first-run', '--disable-gpu', `--user-data-dir=${profile}`,
    '--window-size=1440,1000', 'about:blank'], { stdio: 'ignore' });
  let targets;
  for (let i = 0; i < 60; i++) {
    try { targets = await (await fetch(`http://localhost:${PORT}/json`)).json(); break; }
    catch { await sleep(300); }
  }
  const page = targets.find((t) => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl, { maxPayload: 256 * 1024 * 1024 });
  await new Promise((r) => ws.on('open', r));
  let id = 0; const pending = new Map();
  ws.on('message', (raw) => {
    const m = JSON.parse(raw);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise((r) => {
    const n = ++id; pending.set(n, r); ws.send(JSON.stringify({ id: n, method, params }));
  });
  const evaluate = async (expr) => {
    const r = await send('Runtime.evaluate',
      { expression: expr, awaitPromise: true, returnByValue: true });
    return r.result?.value;
  };
  await send('Page.enable'); await send('Runtime.enable');
  return { send, evaluate, ws };
}

const report = [];
const check = (name, pass, detail = '') => {
  report.push({ name, pass, detail });
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? `  — ${detail}` : ''}`);
};

// ---- Session 1: write a long memo, then kill the browser -------------------
let { send, evaluate } = await launch();
await send('Page.navigate', { url: `${APP}/login` });
await sleep(2000);
await evaluate(`localStorage.setItem('accessToken', ${JSON.stringify(login.access)});
                localStorage.setItem('refreshToken', ${JSON.stringify(login.refresh)}); 1`);

await send('Page.navigate', { url: `${APP}/memos/create` });
await sleep(3500);

// A five-page memo, as the acceptance criterion specifies.
const PARA = 'The existing server estate reached end of vendor support in the last quarter '
  + 'and no longer receives security patches, which places the payroll and attendance '
  + 'databases outside the compliance window agreed with the board. ';
const BODY = PARA.repeat(30);           // ~ five pages of prose
const SUBJECT = 'Server refresh — capital expenditure approval';

const typed = await evaluate(`(() => {
  const setNative = (el, value) => {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const to = document.querySelector('input[aria-label="To"], input[placeholder*="CEO"]');
  const subj = [...document.querySelectorAll('input')].find(
    (i) => (i.getAttribute('aria-label') || '').toLowerCase().includes('subject')
        || (i.closest('label')?.textContent || '').toLowerCase().startsWith('subject'));
  if (to) setNative(to, 'Chief Executive Officer');
  if (subj) setNative(subj, ${JSON.stringify(SUBJECT)});

  // The section bodies are contenteditable (TipTap) or a textarea fallback.
  const areas = [...document.querySelectorAll('textarea')];
  const editables = [...document.querySelectorAll('[contenteditable="true"]')];
  let wrote = 0;
  if (areas.length) { setNative(areas[0], ${JSON.stringify(BODY)}); wrote = areas[0].value.length; }
  else if (editables.length) {
    editables[0].focus();
    editables[0].innerHTML = '<p>' + ${JSON.stringify(BODY)} + '</p>';
    editables[0].dispatchEvent(new InputEvent('input', { bubbles: true }));
    wrote = editables[0].textContent.length;
  }
  return { to: !!to, subject: !!subj, chars: wrote,
           areas: areas.length, editables: editables.length };
})()`);
check('Form accepted a five-page memo', typed.chars > 3000,
  `${typed.chars} characters typed`);

// Wait past the 5s idle trigger so autosave fires.
await sleep(9000);
const indicator = await evaluate(
  `document.querySelector('.draft-indicator')?.textContent || ''`);
check('Save indicator reports Saved', /Saved/i.test(indicator), `showed "${indicator.trim()}"`);

const serverBefore = await (await fetch(`${API}/drafts/memo/new/`, {
  headers: { Authorization: `Bearer ${login.access}` },
})).json();
const serverChars = JSON.stringify(serverBefore.draft?.payload || {}).length;
check('Server holds the snapshot', serverChars > 3000, `${serverChars} bytes stored`);

let shot = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(join(OUT, '1-typed.png'), Buffer.from(shot.data, 'base64'));

// ---- THE CRASH: SIGKILL, no cleanup, no beforeunload, no flush -------------
try { execSync(`pkill -9 -f "remote-debugging-port=${PORT}"`); } catch { /* already gone */ }
chrome.kill('SIGKILL');
await sleep(3000);
console.log('\n--- browser killed with SIGKILL (simulated power loss) ---\n');

// ---- Session 2: brand-new browser, same profile ----------------------------
({ send, evaluate } = await launch());
await send('Page.navigate', { url: `${APP}/login` });
await sleep(2000);
await evaluate(`localStorage.setItem('accessToken', ${JSON.stringify(login.access)});
                localStorage.setItem('refreshToken', ${JSON.stringify(login.refresh)}); 1`);
await send('Page.navigate', { url: `${APP}/memos/create` });
await sleep(4000);

const dialog = await evaluate(`(() => {
  const d = document.querySelector('[role="dialog"]');
  return d ? { present: true, text: d.textContent } : { present: false, text: '' };
})()`);
check('Recovery dialog is offered after the crash', dialog.present,
  dialog.present ? dialog.text.slice(0, 90).replace(/\s+/g, ' ') : 'no dialog');
check('Dialog names the three approved choices',
  /Restore draft/.test(dialog.text) && /Continue editing/.test(dialog.text)
  && /Discard draft/.test(dialog.text));

shot = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(join(OUT, '2-recovery-offered.png'), Buffer.from(shot.data, 'base64'));

// Accept the recovery.
await evaluate(`[...document.querySelectorAll('button')]
  .find((b) => b.textContent.trim() === 'Restore draft')?.click(); 1`);
await sleep(2500);

const recovered = await evaluate(`(() => {
  const inputs = [...document.querySelectorAll('input')].map((i) => i.value).join(' | ');
  const areas = [...document.querySelectorAll('textarea')].map((t) => t.value).join(' ');
  const edit = [...document.querySelectorAll('[contenteditable="true"]')]
    .map((e) => e.textContent).join(' ');
  return { inputs, chars: (areas + edit).length, body: (areas + edit).slice(0, 60) };
})()`);

check('Subject recovered', recovered.inputs.includes(SUBJECT),
  recovered.inputs.slice(0, 80));
check('Recipient recovered', recovered.inputs.includes('Chief Executive Officer'));
check('Full body recovered', recovered.chars > 3000, `${recovered.chars} characters restored`);
check('Content matches what was typed',
  recovered.body.replace(/\s+/g, ' ').startsWith('The existing server estate'),
  recovered.body.slice(0, 50));

shot = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(join(OUT, '3-recovered.png'), Buffer.from(shot.data, 'base64'));

console.log('\n' + JSON.stringify({
  passed: report.filter((r) => r.pass).length,
  failed: report.filter((r) => !r.pass).length,
}, null, 2));

try { execSync(`pkill -9 -f "remote-debugging-port=${PORT}"`); } catch { /* gone */ }
process.exit(report.some((r) => !r.pass) ? 1 : 0);
