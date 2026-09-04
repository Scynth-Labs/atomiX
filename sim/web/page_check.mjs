// The pages, checked in a real browser.
//
// boot.mjs and compare.mjs verify the *machines* through the same C API the
// pages drive them with, which is the part that carries the evidence. What
// they cannot see is the page: module loading by <script> and export name,
// asset paths, the scheduling loop, and whether any of the numbers reach the
// screen. Those have their own ways to be wrong, and every one of them is
// invisible to a headless Node run.
//
// So this drives the served pages in headless Chromium and reads the rendered
// DOM back. It is deliberately a skip rather than a failure when no browser is
// installed: this whole tier is optional and load-bearing for nothing, and a
// check that cannot run must not be reported as one that passed.
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = join(here, 'public');

// Chromium under WSL is usually the Windows one: the browser is on the other
// side of the filesystem boundary, but WSL forwards a listening socket to the
// Windows loopback, so a page served from here is reachable there.
const CANDIDATES = [
  process.env.AX_BROWSER,
  'chromium', 'chromium-browser', 'google-chrome', 'google-chrome-stable',
  '/mnt/c/Program Files/Google/Chrome/Application/chrome.exe',
  '/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/mnt/c/Program Files/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean);

function findBrowser() {
  for (const candidate of CANDIDATES) {
    if (candidate.includes('/')) {
      if (existsSync(candidate)) return candidate;
    } else if (spawnSync(candidate, ['--version'], { stdio: 'ignore' }).status === 0) {
      return candidate;
    }
  }
  return null;
}

const browser = findBrowser();
if (!browser) {
  process.stdout.write(
    '[page] SKIPPED no Chromium found (set AX_BROWSER=/path/to/chrome).\n' +
    '[page] The machines are still checked by `make -C sim/web check` and `compare`.\n');
  process.exit(0);
}

for (const required of ['index.html', 'axsoc.js', 'machines/machines.json']) {
  if (!existsSync(join(publicDir, required))) {
    process.stderr.write(
      `[page] FAIL ${required} is not staged; run \`make -C sim/web build machines\` first\n`);
    process.exit(1);
  }
}

const freePort = () => new Promise((resolve) => {
  const probe = createServer();
  probe.listen(0, '127.0.0.1', () => {
    const { port } = probe.address();
    probe.close(() => resolve(port));
  });
});

const port = await freePort();
const server = spawn('python3', ['-m', 'http.server', String(port), '--directory', publicDir],
                     { stdio: 'ignore' });
// A Windows browser cannot open a Linux path, and Chrome refuses a UNC one, so
// under WSL the profile has to live on the Windows side of the boundary even
// though everything else here is on this one.
function profileDir() {
  if (!browser.startsWith('/mnt/')) {
    const path = mkdtempSync(join(tmpdir(), 'ax-page-'));
    return { arg: path, path };
  }
  // spawnSync returns no stdout at all when the program is missing, so this
  // reads defensively: a Windows browser we cannot hand a Windows-side profile
  // to is a browser we cannot drive, which is a skip and not a failure.
  const temp = (spawnSync('cmd.exe', ['/c', 'echo %TEMP%'],
                          { cwd: '/mnt/c', encoding: 'utf8' }).stdout || '').trim();
  if (!temp) return null;
  // Unique per run. A profile a previous run left locked -- an interrupted
  // check strands the browser holding it -- would otherwise make every later
  // run fail to start, which reads as a broken check rather than as leftover
  // state, and the tempting fix for that is killing browsers by name. A
  // developer's own windows are none of a test's business.
  const arg = `${temp}\\ax-page-${process.pid}-${Date.now().toString(36)}`;
  const path = (spawnSync('wslpath', ['-u', arg], { encoding: 'utf8' }).stdout || '').trim();
  return { arg, path: path || null };
}

const profile = profileDir();
if (!profile) {
  process.stdout.write(
    `[page] SKIPPED ${browser} needs a Windows-side profile directory and %TEMP% ` +
    'could not be resolved.\n');
  server.kill();
  process.exit(0);
}
const cleanup = () => {
  server.kill();
  // Best effort: a stranded browser still holds files here, and failing to
  // tidy up is not a reason to fail a check that has already answered.
  try {
    if (profile.path) rmSync(profile.path, { recursive: true, force: true });
  } catch { /* the OS will reclaim it */ }
};
process.on('exit', cleanup);

// Virtual time is what carries a page past its own scheduling: the browser
// fast-forwards the clock whenever the task queue drains, so a run that would
// take a second of wall time is dumped complete. It relies on the pages
// yielding between slices, which both of them do -- a timer chain with no gap
// pins virtual time and never terminates.
//
// The browser runs headless, in a throwaway profile of its own, and nothing
// here ever kills a browser process: a developer's own windows are none of a
// test's business. Set AX_BROWSER to choose which one.
const FLAGS = [
  '--headless=new', '--disable-gpu', '--no-first-run', '--hide-scrollbars',
  `--user-data-dir=${profile.arg}`,
  '--virtual-time-budget=600000',
  '--dump-dom',
];

function render(path) {
  const result = spawnSync(browser, [...FLAGS, `http://localhost:${port}/${path}`],
                           { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  if (result.status !== 0) {
    throw new Error(`browser exited ${result.status} on /${path}\n${
      (result.stderr || '').split('\n').slice(-8).join('\n')}`);
  }
  return result.stdout.replace(/\r/g, '');
}

const problems = [];
function require_(condition, message) {
  if (!condition) problems.push(message);
}
const strip = (html) => html.replace(/<[^>]+>/g, '')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');

// --- The single-machine console ---------------------------------------------
{
  const dom = render('');
  const screen = /<pre class="screen"[^>]*>([\s\S]*?)<\/pre>/.exec(dom);
  const state = /id="state" data-state="([a-z]+)"/.exec(dom);
  require_(screen && strip(screen[1]).includes('aXos>'),
           'the console page never reached the aXos shell prompt');
  require_(state && ['idle', 'running'].includes(state[1]),
           `the console page ended in state ${state ? state[1] : 'unknown'}`);
  // The label has to come from the machine, not from the page's assumptions.
  const manifestProfile = /id="profile"[^>]*>([^<]*)/.exec(dom);
  require_(manifestProfile && manifestProfile[1].trim().length > 0,
           'the console page showed no profile');
}

// --- The side-by-side page ---------------------------------------------------
{
  const manifest = JSON.parse(
    readFileSync(join(publicDir, 'machines', 'machines.json'), 'utf8'));
  const dom = render('compare.html');
  const state = /id="race-state" data-state="([a-z]+)"/.exec(dom);
  require_(state && state[1] === 'done',
           `the run did not finish in the page (state ${state ? state[1] : 'unknown'})`);

  const body = /<tbody id="scoreboard">([\s\S]*?)<\/tbody>/.exec(dom);
  const rows = body ? [...body[1].matchAll(/<tr>([\s\S]*?)<\/tr>/g)] : [];
  require_(rows.length === manifest.machines.length,
           `${manifest.machines.length} machines staged but ${rows.length} scored`);

  const scored = rows.map((row) => {
    const cells = [...row[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)]
      .map((cell) => strip(cell[1]).trim());
    return { name: cells[0], cycles: cells[2], checksum: cells[4] };
  });
  for (const [i, entry] of manifest.machines.entries()) {
    require_(scored[i] && scored[i].name === entry.name,
             `row ${i} is ${scored[i] ? scored[i].name : 'missing'}, expected ${entry.name}`);
  }
  require_(new Set(scored.map((s) => s.checksum)).size === 1,
           `the page shows disagreeing checksums: ${scored.map((s) => s.checksum).join(', ')}`);
  require_(new Set(scored.map((s) => s.cycles)).size === scored.length,
           `the page shows two machines with the same cycle count: ` +
           scored.map((s) => `${s.name}=${s.cycles}`).join(', '));
  require_(/<span class="bar"/.test(dom), 'the scoreboard rendered no bars');

  if (problems.length === 0) {
    process.stdout.write(`[page] scoreboard: ${
      scored.map((s) => `${s.name} ${s.cycles}`).join(' | ')}\n`);
  }
}

if (problems.length) {
  for (const problem of problems) process.stderr.write(`[page] FAIL ${problem}\n`);
  process.exit(1);
}
process.stdout.write(
  `[page] PASS both pages render their machines in ${browser.split(/[\\/]/).pop()}\n`);
// The static server is a live child handle, so the event loop has work left
// even though the check is over. Say so explicitly rather than hanging.
process.exit(0);
