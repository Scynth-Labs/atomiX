// Several machines, one binary, side by side.
//
// The page's whole job is to keep one comparison honest, so two rules govern
// it. Nothing on screen may be computed here -- every cycle count comes from
// ax_cycles() and every result from the machine's own console output. And no
// machine is labelled with what the page assumed: each module is asked what it
// is (ax_profile()) and checked against the selection it was staged under,
// because three bundles that were accidentally one bundle would otherwise make
// the most convincing possible version of the wrong claim.
//
// Machines are advanced in lock-step *simulated* cycles rather than in equal
// wall-clock slices. That is the difference between racing the machines and
// racing the host: with an equal cycle budget per slice, the console that
// finishes first is the machine that needed fewer cycles, which is the claim.
//
// The slices are scheduled on a timer rather than on requestAnimationFrame,
// which is where this page differs from the interactive console. That page is
// animating a machine a reader is typing into, so it should run exactly as
// fast as it paints. This one runs a race to completion: rAF stops altogether
// in a hidden tab, so a reader who switched tabs mid-run would come back to
// three machines frozen exactly where they left them.
'use strict';

const ui = {
  machines: document.getElementById('machines'),
  run: document.getElementById('run'),
  state: document.getElementById('race-state'),
  note: document.getElementById('race-note'),
  results: document.getElementById('results'),
  scoreboard: document.getElementById('scoreboard'),
  verdict: document.getElementById('verdict'),
  provenance: document.getElementById('provenance'),
};

const SLICE_MS = 10;       // wall-clock target for one pass over every machine
// The gap between slices. It is not zero on purpose: a zero-delay chain never
// lets the queue drain, so the browser gets no opportunity to paint and the
// panes would jump straight from empty to finished -- which is the one thing
// this page must not do, since watching the consoles diverge *is* the
// comparison. It is also what lets a headless run finish: a timer chain with
// no gap pins virtual time and never terminates.
const SLICE_GAP_MS = 4;
const RECV_MAX = 8192;
const CYCLE_LIMIT = 2e7;   // a run needing this many cycles has hung

const decoder = new TextDecoder('utf-8', { fatal: false });
const machines = [];
let budgetCycles = 20000;  // per machine per frame; then measured
let running = false;

function setState(name) {
  ui.state.textContent = name;
  ui.state.dataset.state = name;
}

// MODULARIZE puts the factory on a global, so the bundles are loaded by
// <script> and picked up by the export name the build gave each one. That name
// is per selection for exactly this reason: a shared one would leave whichever
// bundle loaded last answering for all three.
function loadScript(src) {
  return new Promise((resolve, reject) => {
    const element = document.createElement('script');
    element.src = src;
    element.onload = () => resolve();
    element.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(element);
  });
}

function card(entry, describe) {
  const root = document.createElement('article');
  root.className = 'machine machine-card';
  const capabilities = (describe.capabilities || [])
    .map((c) => `<li>${c}</li>`).join('');
  root.innerHTML = `
    <div class="machine-bar">
      <span class="badge" data-state="loading">loading</span>
      <dl class="readout">
        <div><dt>profile</dt><dd>${entry.name}</dd></div>
        <div><dt>cycles</dt><dd class="cycles">0</dd></div>
      </dl>
    </div>
    <p class="core-title"><code>${entry.core}</code> — ${describe.title || ''}</p>
    <div class="console"><pre class="screen"></pre></div>
    <ul class="capabilities">${capabilities}</ul>`;
  return {
    root,
    badge: root.querySelector('.badge'),
    cycles: root.querySelector('.cycles'),
    screen: root.querySelector('.screen'),
    console: root.querySelector('.console'),
  };
}

function drain(machine) {
  let text = '';
  for (;;) {
    const got = machine.mod._ax_recv(machine.buffer, RECV_MAX);
    if (got <= 0) break;
    text += decoder.decode(
      machine.mod.HEAPU8.subarray(machine.buffer, machine.buffer + got));
    if (got < RECV_MAX) break;
  }
  if (text) {
    machine.text += text;
    machine.term.write(text);
  }
}

function frame() {
  if (!running) return;
  const active = machines.filter((m) => !m.done);
  if (active.length === 0) { finish(); return; }   // never reached in practice

  const start = performance.now();
  for (const machine of active) {
    machine.mod._ax_run(budgetCycles);
    drain(machine);
    machine.cyclesRun = machine.mod._ax_cycles();
    machine.cycles.textContent = machine.cyclesRun.toLocaleString('en-US');
    if (machine.mod._ax_finished()) {
      machine.done = true;
      machine.exitCode = machine.mod._ax_exit_code();
      machine.badge.textContent = machine.exitCode === 0 ? 'halted' : 'failed';
      machine.badge.dataset.state = machine.exitCode === 0 ? 'halted' : 'failed';
    } else if (machine.cyclesRun > CYCLE_LIMIT) {
      machine.done = true;
      machine.exitCode = -1;
      machine.badge.textContent = 'hung';
      machine.badge.dataset.state = 'failed';
    }
    machine.term.render();
    machine.console.scrollTop = machine.console.scrollHeight;
  }
  const elapsed = performance.now() - start;

  // Score in the slice the last machine halted in, not the one after it.
  // Waiting costs a paint in which every pane says "halted" while the page
  // still says "running" and shows no result -- a state a reader will read as
  // the page having lost track of its own machines.
  if (machines.every((m) => m.done)) { finish(); return; }

  // Hold the slice near SLICE_MS by scaling the shared budget, rather than by
  // modelling a rate per machine: the budget has to stay identical across
  // machines for the lock-step to mean anything, so there is one knob.
  if (elapsed > 0.5) {
    const target = budgetCycles * (SLICE_MS / elapsed);
    budgetCycles = Math.max(2000, Math.round(0.7 * budgetCycles + 0.3 * target));
  }
  setTimeout(frame, SLICE_GAP_MS);
}

// Everything reported here is read back out of the machines' own console
// output, which is why the regexes are the only parsing in this file.
function finish() {
  running = false;
  setState('done');
  for (const machine of machines) {
    const measured = /cpu_perf measured: cycles=(\d+)/.exec(machine.text);
    const checksum = /checksum=(0x[0-9a-fA-F]+)/.exec(machine.text);
    machine.measured = measured ? Number(measured[1]) : null;
    machine.checksum = checksum ? checksum[1] : null;
  }

  const measured = machines.map((m) => m.measured).filter((v) => v !== null);
  const slowest = Math.max(...measured, 0);
  ui.scoreboard.innerHTML = '';
  for (const machine of machines) {
    const row = document.createElement('tr');
    const relative = machine.measured ? slowest / machine.measured : 0;
    const width = machine.measured ? (machine.measured / slowest) * 100 : 0;
    row.innerHTML = `
      <td><code>${machine.name}</code></td>
      <td><code>${machine.core}</code></td>
      <td class="numeric">
        <span class="bar" style="width: ${width.toFixed(1)}%"></span>
        <span>${machine.measured ? machine.measured.toLocaleString('en-US') : '—'}</span>
      </td>
      <td class="numeric">${relative ? relative.toFixed(2) + '×' : '—'}</td>
      <td><code>${machine.checksum || '—'}</code></td>`;
    ui.scoreboard.appendChild(row);
  }
  ui.results.hidden = false;

  const checksums = new Set(machines.map((m) => m.checksum));
  const failed = machines.filter((m) => m.exitCode !== 0);
  if (failed.length) {
    ui.verdict.className = 'verdict bad';
    ui.verdict.textContent =
      `${failed.map((m) => m.name).join(', ')} did not halt cleanly.`;
  } else if (checksums.size !== 1) {
    ui.verdict.className = 'verdict bad';
    ui.verdict.textContent =
      'The machines disagree on the result. That is a correctness bug in a ' +
      'core, not a performance difference.';
  } else {
    ui.verdict.className = 'verdict';
    const fastest = machines.reduce((a, b) => (a.measured < b.measured ? a : b));
    const slowestMachine =
      machines.reduce((a, b) => (a.measured > b.measured ? a : b));
    ui.verdict.textContent =
      `Same binary, same answer (${[...checksums][0]}) — so the whole spread ` +
      `is the machine. ${fastest.core} finished the workloads in ` +
      `${fastest.measured.toLocaleString('en-US')} cycles against ` +
      `${slowestMachine.core}'s ` +
      `${slowestMachine.measured.toLocaleString('en-US')}, a ` +
      `${(slowestMachine.measured / fastest.measured).toFixed(2)}× spread ` +
      `from changing one component.`;
  }
  ui.run.textContent = 'Run again';
  ui.run.disabled = false;
}

function start() {
  ui.run.disabled = true;
  ui.results.hidden = true;
  setState('running');
  for (const machine of machines) {
    machine.mod._ax_boot('');
    machine.term.reset();
    machine.term.render();
    machine.text = '';
    machine.done = false;
    machine.exitCode = 0;
    machine.badge.textContent = 'running';
    machine.badge.dataset.state = 'running';
    machine.cycles.textContent = '0';
  }
  running = true;
  setTimeout(frame, SLICE_GAP_MS);
}

ui.run.addEventListener('click', start);

(async function load() {
  try {
    const manifest = await (await fetch('machines/machines.json', { cache: 'no-cache' })).json();
    const payload = new Uint8Array(
      await (await fetch(`machines/${manifest.payload}`, { cache: 'no-cache' })).arrayBuffer());

    for (const entry of manifest.machines) {
      const describe =
        await (await fetch(`machines/${entry.describe}`, { cache: 'no-cache' })).json();
      const view = card(entry, describe);
      ui.machines.appendChild(view.root);

      await loadScript(`machines/${entry.module}`);
      const factory = window[entry.export];
      if (!factory) throw new Error(`${entry.module} defined no ${entry.export}`);
      const mod = await factory();

      // Runtime payload selection, exactly as the single-machine console does
      // it: the model's $readmemh reads this path when ax_boot() constructs the
      // machine, so the same image reaches every one of them.
      mod.FS.writeFile(mod.UTF8ToString(mod._ax_ram_init_path()), payload);

      const reported = mod.UTF8ToString(mod._ax_profile());
      if (reported !== entry.name) {
        throw new Error(
          `a machine staged as ${entry.name} reports itself as ${reported}`);
      }

      machines.push({
        ...entry,
        ...view,
        mod,
        buffer: mod._malloc(RECV_MAX),
        term: new Terminal(view.screen),
        text: '',
        done: false,
        exitCode: 0,
        cyclesRun: 0,
      });
      view.badge.textContent = 'ready';
      view.badge.dataset.state = 'idle';
    }

    ui.provenance.textContent =
      `${machines.length} Verilated machines · ` +
      `${machines.map((m) => m.name).join(', ')} · ` +
      `each confirmed by its own ax_profile() · ` +
      `clocked by components/harness/common/soc_machine.h`;
    setState('ready');
    ui.run.disabled = false;
    start();
  } catch (error) {
    setState('failed');
    ui.note.textContent =
      `Could not stage the machines: ${error.message}. Build them with ` +
      `"make -C sim/web machines", and serve the directory over HTTP.`;
  }
})();
