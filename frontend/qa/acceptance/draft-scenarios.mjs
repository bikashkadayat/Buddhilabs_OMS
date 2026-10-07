/** Phase 111.18 — the remaining loss scenarios, against the real stack. */
import WebSocket from 'ws';
import { spawn, execSync } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const OUT = process.argv[2];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const API = 'http://localhost:8001/api/v1', APP = 'http://localhost:5173';
let PORT = 9500;

const login = await (await fetch(`${API}/auth/login/`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ email: 'demo.maker@example.test', password: 'Local@1234' }),
})).json();
const auth = { Authorization: `Bearer ${login.access}` };

const results = [];
const check = (n, pass, d = '') => {
  results.push({ n, pass }); console.log(`${pass ? 'PASS' : 'FAIL'}  ${n}${d ? `  — ${d}` : ''}`);
};

async function session(profile, port) {
  const chrome = spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ['--headless=new', `--remote-debugging-port=${port}`, '--no-first-run', '--disable-gpu',
      `--user-data-dir=${profile}`, '--window-size=1440,1000', 'about:blank'], { stdio: 'ignore' });
  let targets;
  for (let i = 0; i < 60; i++) {
    try { targets = await (await fetch(`http://localhost:${port}/json`)).json(); break; }
    catch { await sleep(300); }
  }
  const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl,
    { maxPayload: 2e8 });
  await new Promise((r) => ws.on('open', r));
  let id = 0; const p = new Map();
  ws.on('message', (raw) => {
    const m = JSON.parse(raw);
    if (m.id && p.has(m.id)) { p.get(m.id)(m.result); p.delete(m.id); }
  });
  const send = (method, params = {}) => new Promise((r) => {
    const n = ++id; p.set(n, r); ws.send(JSON.stringify({ id: n, method, params }));
  });
  const ev = async (e) => (await send('Runtime.evaluate',
    { expression: e, awaitPromise: true, returnByValue: true })).result?.value;
  await send('Page.enable'); await send('Runtime.enable');
  await send('Page.navigate', { url: `${APP}/login` }); await sleep(2000);
  await ev(`localStorage.setItem('accessToken',${JSON.stringify(login.access)});
            localStorage.setItem('refreshToken',${JSON.stringify(login.refresh)});1`);
  return { send, ev, chrome, port };
}

const fill = (labels) => `(() => {
  const set=(el,v)=>{const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto,'value').set.call(el,v);
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));};
  const want = ${JSON.stringify(labels)};
  let n = 0;
  for (const [label, value] of Object.entries(want)) {
    // These forms label fields three different ways: aria-label (Memo),
    // an enclosing <label><span> (Minute, Circular), or a placeholder.
    const want2 = label.toLowerCase();
    const el = [...document.querySelectorAll('input,textarea')].find((i) => {
      const aria = (i.getAttribute('aria-label')||'').toLowerCase();
      const span = (i.closest('label')?.querySelector('span')?.textContent||'')
        .split('*').join('').trim().toLowerCase();
      return aria === want2 || span === want2;
    });
    if (el) { set(el, value); n++; }
  }
  return n;
})()`;

// ---------------------------------------------------------------- Minute
{
  await fetch(`${API}/drafts/minute/new/`, { method: 'DELETE', headers: auth });
  const profile = mkdtempSync(join(tmpdir(), 'min-'));
  let s = await session(profile, ++PORT);
  await s.send('Page.navigate', { url: `${APP}/minutes/create` }); await sleep(3500);
  const n = await s.ev(fill({ Subject: 'Quarterly ICT review meeting' }));
  await sleep(9000);
  const stored = await (await fetch(`${API}/drafts/minute/new/`, { headers: auth })).json();
  check('Minute autosaves to the server', Boolean(stored.draft),
    `${n} field(s) filled, ${JSON.stringify(stored.draft?.payload || {}).length} bytes`);

  try { execSync(`pkill -9 -f "remote-debugging-port=${s.port}"`); } catch { /* gone */ }
  s.chrome.kill('SIGKILL'); await sleep(2500);

  s = await session(profile, ++PORT);
  await s.send('Page.navigate', { url: `${APP}/minutes/create` }); await sleep(4000);
  const dlg = await s.ev(`document.querySelector('[role="dialog"]')?.textContent || ''`);
  check('Minute offers recovery after a crash', /Unsaved minute found/.test(dlg));
  await s.ev(`[...document.querySelectorAll('button')]
    .find(b=>b.textContent.trim()==='Restore draft')?.click();1`);
  await sleep(2000);
  const back = await s.ev(`[...document.querySelectorAll('input')].map(i=>i.value).join('|')`);
  check('Minute content recovered', back.includes('Quarterly ICT review meeting'));
  const shot = await s.send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(OUT, 'minute-recovered.png'), Buffer.from(shot.data, 'base64'));
  try { execSync(`pkill -9 -f "remote-debugging-port=${s.port}"`); } catch { /* gone */ }
}

// ---------------------------------------------------------------- Circular
{
  await fetch(`${API}/drafts/circular/new/`, { method: 'DELETE', headers: auth });
  const profile = mkdtempSync(join(tmpdir(), 'cir-'));
  let s = await session(profile, ++PORT);
  await s.send('Page.navigate', { url: `${APP}/circulars/create` }); await sleep(3500);
  const n = await s.ev(fill({ Subject: 'Office closure for Dashain' }));
  await sleep(9000);
  const stored = await (await fetch(`${API}/drafts/circular/new/`, { headers: auth })).json();
  check('Circular autosaves to the server', Boolean(stored.draft),
    `${n} field(s) filled, ${JSON.stringify(stored.draft?.payload || {}).length} bytes`);

  try { execSync(`pkill -9 -f "remote-debugging-port=${s.port}"`); } catch { /* gone */ }
  s.chrome.kill('SIGKILL'); await sleep(2500);

  s = await session(profile, ++PORT);
  await s.send('Page.navigate', { url: `${APP}/circulars/create` }); await sleep(4000);
  const dlg = await s.ev(`document.querySelector('[role="dialog"]')?.textContent || ''`);
  check('Circular offers recovery after a crash', /Unsaved circular found/.test(dlg));
  await s.ev(`[...document.querySelectorAll('button')]
    .find(b=>b.textContent.trim()==='Restore draft')?.click();1`);
  await sleep(2000);
  const back = await s.ev(`[...document.querySelectorAll('input')].map(i=>i.value).join('|')`);
  check('Circular content recovered', back.includes('Office closure for Dashain'));
  const shot = await s.send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(OUT, 'circular-recovered.png'), Buffer.from(shot.data, 'base64'));
  try { execSync(`pkill -9 -f "remote-debugging-port=${s.port}"`); } catch { /* gone */ }
}

// ---------------------------------------------------- Offline, then reconnect
{
  await fetch(`${API}/drafts/memo/new/`, { method: 'DELETE', headers: auth });
  const profile = mkdtempSync(join(tmpdir(), 'off-'));
  const s = await session(profile, ++PORT);
  await s.send('Page.navigate', { url: `${APP}/memos/create` }); await sleep(3500);

  await s.send('Network.enable');
  await s.send('Network.emulateNetworkConditions',
    { offline: true, latency: 0, downloadThroughput: 0, uploadThroughput: 0 });
  await s.ev(`window.dispatchEvent(new Event('offline'));1`);

  await s.ev(fill({ Subject: 'Written while disconnected' }));
  await sleep(9000);
  const offlineChip = await s.ev(`document.querySelector('.draft-indicator')?.textContent || ''`);
  check('Offline is reported, not a silent failure', /Offline/i.test(offlineChip),
    `showed "${offlineChip.trim()}"`);
  const during = await (await fetch(`${API}/drafts/memo/new/`, { headers: auth })).json();
  check('Nothing reached the server while offline', during.draft === null);

  await s.send('Network.emulateNetworkConditions',
    { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await s.ev(`window.dispatchEvent(new Event('online'));1`);
  await sleep(5000);
  const after = await (await fetch(`${API}/drafts/memo/new/`, { headers: auth })).json();
  check('Work syncs automatically on reconnect',
    JSON.stringify(after.draft?.payload || {}).includes('Written while disconnected'));
  const shot = await s.send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(OUT, 'offline-synced.png'), Buffer.from(shot.data, 'base64'));
  try { execSync(`pkill -9 -f "remote-debugging-port=${s.port}"`); } catch { /* gone */ }
}

// ------------------------------------------------- Session expiry / other device
{
  const stored = await (await fetch(`${API}/drafts/memo/new/`, { headers: auth })).json();
  check('Draft survives on the server for another device / after logout',
    Boolean(stored.draft),
    'server copy is independent of any browser');
}

console.log('\n' + JSON.stringify({
  passed: results.filter((r) => r.pass).length,
  failed: results.filter((r) => !r.pass).length,
}, null, 2));
process.exit(results.some((r) => !r.pass) ? 1 : 0);
