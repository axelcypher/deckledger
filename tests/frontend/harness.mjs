// What the browser tests share: the app on a free port with the test catalogue, and a headless
// Chrome driven over the DevTools protocol. No dependencies -- Node 22 has fetch, WebSocket and a
// test runner built in.
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const CHROME_CANDIDATES = [
  process.env.CHROME_BIN,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
];
export const chromePath = () => CHROME_CANDIDATES.find(candidate => candidate && existsSync(candidate));

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once('error', reject);
    probe.listen(0, '127.0.0.1', () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
  });
}

function stop(child) {
  if (!child || child.exitCode !== null) return;
  // Flask's reloader is off, so the process has no children; a plain kill is enough everywhere.
  child.kill();
}

export async function startServer() {
  const port = await freePort();
  const python = process.env.DECKLEDGER_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  const output = [];
  const child = spawn(python, [path.join(HERE, 'serve.py'), String(port)], {
    cwd: path.resolve(HERE, '..', '..'), env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONUTF8: '1' },
  });
  child.stdout.on('data', chunk => output.push(String(chunk)));
  child.stderr.on('data', chunk => output.push(String(chunk)));
  const base = `http://127.0.0.1:${port}`;
  for (let attempt = 0; attempt < 120; attempt++) {
    if (child.exitCode !== null) break;
    try { if ((await fetch(`${base}/health`)).ok) return { base, output, stop: () => stop(child) }; } catch {}
    await sleep(250);
  }
  stop(child);
  throw new Error(`The app did not start:\n${output.join('')}`);
}

export async function startBrowser({ width = 1400, height = 950, mobile = false } = {}) {
  const profile = mkdtempSync(path.join(tmpdir(), 'dl-ui-'));
  const flags = ['--headless=new', '--remote-debugging-port=0', `--user-data-dir=${profile}`, `--window-size=${width},${height}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--mute-audio'];
  if (process.platform === 'linux') flags.push('--no-sandbox', '--disable-dev-shm-usage');
  const chrome = spawn(chromePath(), [...flags, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] });
  const debugPort = await new Promise((resolve, reject) => {
    let log = '';
    const timer = setTimeout(() => reject(new Error(`Chrome did not report its debugging port:\n${log}`)), 30000);
    chrome.stderr.on('data', chunk => {
      log += chunk;
      const match = log.match(/DevTools listening on ws:\/\/127\.0\.0\.1:(\d+)\//);
      if (match) { clearTimeout(timer); resolve(Number(match[1])); }
    });
    chrome.once('exit', () => { clearTimeout(timer); reject(new Error(`Chrome exited early:\n${log}`)); });
  });
  let target;
  for (let attempt = 0; attempt < 40 && !target; attempt++) {
    try { target = (await (await fetch(`http://127.0.0.1:${debugPort}/json`)).json()).find(item => item.type === 'page'); } catch {}
    if (!target) await sleep(250);
  }
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve); socket.addEventListener('error', reject); });

  let nextId = 0;
  const pending = new Map();
  // Everything that would show up red in the browser console: uncaught errors, rejected
  // promises nobody handled, console.error, and answers from the server with a 5xx status.
  const problems = [];
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); return; }
    if (message.method === 'Runtime.exceptionThrown') {
      const details = message.params.exceptionDetails;
      problems.push(`exception: ${details.exception?.description || details.text}`);
    } else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
      problems.push(`console.error: ${message.params.args.map(arg => arg.value ?? arg.description).join(' ')}`);
    } else if (message.method === 'Network.responseReceived' && message.params.response.status >= 500) {
      problems.push(`HTTP ${message.params.response.status}: ${message.params.response.url}`);
    }
  });
  const send = (method, params = {}) => new Promise(resolve => { pending.set(++nextId, resolve); socket.send(JSON.stringify({ id: nextId, method, params })); });
  for (const domain of ['Page', 'Runtime', 'Network']) await send(`${domain}.enable`);
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
  if (mobile) await send('Emulation.setTouchEmulationEnabled', { enabled: true });
  // Headless Chrome takes this from the machine it runs on; the tests decide for themselves.
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] });

  const page = {
    problems,
    send,
    /** Runs an expression in the page (it may be a promise) and returns its JSON-able value. */
    async evaluate(expression) {
      const { result } = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(`${result.exceptionDetails.exception?.description || result.exceptionDetails.text}\n  in: ${expression}`);
      return result.result.value;
    },
    /** Polls until the expression is truthy; returns that value. */
    async waitFor(expression, { timeout = 15000, message } = {}) {
      const deadline = Date.now() + timeout;
      for (;;) {
        let value, failure = '';
        try { value = await page.evaluate(expression); } catch (error) { failure = `
  last attempt failed: ${error.message}`; }
        if (value) return value;
        if (Date.now() > deadline) throw new Error(`Timed out waiting for: ${message || expression}${failure}
  view: ${await page.evaluate(`document.body.dataset.route+' / '+document.querySelector('#content')?.innerText.slice(0,160)`).catch(() => '?')}`);
        await sleep(60);
      }
    },
    async goto(url) {
      await send('Page.navigate', { url });
      await page.waitFor(`document.readyState==='complete'`);
    },
    async login(base, username, password) {
      await page.goto(`${base}/login`);
      await page.evaluate(`(()=>{document.querySelector('[name=username]').value=${JSON.stringify(username)};document.querySelector('[name=password]').value=${JSON.stringify(password)};document.querySelector('form').submit()})()`);
      // The app is ready once the bootstrap data is in and the dashboard has been drawn.
      await page.waitFor(`typeof state==='object'&&state.boot&&document.querySelector('[data-dashboard-route]')`, { message: 'the app to start after login' });
    },
    /** Opens a view and waits until it has replaced what was on screen and is done loading. */
    async route(route, data) {
      // Every view draws itself by replacing the content, so a marked element disappearing is
      // the sign that the new one is there -- also when the same view is opened again.
      await page.evaluate(`(document.querySelector('#content').firstElementChild?.setAttribute('data-previous-view',''),routeTo(${JSON.stringify(route)}${data === undefined ? '' : `,${JSON.stringify(data)}`}),1)`);
      await page.waitFor(`(()=>{const view=document.querySelector('#content');return !view.querySelector('[data-previous-view],.page-loader')&&view.children.length>0})()`, { message: `the ${route} view to finish loading` });
    },
    /** Whether the system asks for reduced motion ('reduce') or not ('no-preference'). */
    async motion(value) {
      await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value }] });
    },
    async offline(offline) {
      await send('Network.emulateNetworkConditions', { offline, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    },
    async close() {
      try { socket.close(); } catch {}
      chrome.kill();
      await sleep(400);
      try { rmSync(profile, { recursive: true, force: true }); } catch {}
    },
  };
  return page;
}
