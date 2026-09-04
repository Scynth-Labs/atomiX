// Browser console for the WebAssembly SoC.
//
// Everything here is presentation and scheduling. The machine itself lives in
// the WASM module and is clocked by the same shared runner code the native
// `make sim` checks use, so this file must never be tempted into simulating
// anything -- if a number on screen did not come out of ax_cycles(), it does
// not belong on screen.
//
// The terminal is in terminal.js, shared with the side-by-side page.
'use strict';

// ---------------------------------------------------------------------------
// Machine
// ---------------------------------------------------------------------------
const ui = {
  screen: document.getElementById('screen'),
  console: document.getElementById('console'),
  state: document.getElementById('state'),
  profile: document.getElementById('profile'),
  cycles: document.getElementById('cycles'),
  bootTime: document.getElementById('boot-time'),
  rate: document.getElementById('rate'),
  reboot: document.getElementById('reboot'),
  provenance: document.getElementById('provenance'),
};

const term = new Terminal(ui.screen);

// A frame's worth of simulation, chosen so the page keeps painting: the slice
// is re-sized from the measured rate rather than fixed, because the same
// bundle runs on machines that differ by more than an order of magnitude.
const FRAME_BUDGET_MS = 8;
// Boot gets a larger budget than a steady-state frame. There is nothing to
// interact with yet and nothing to animate, and at a frame-friendly slice the
// scheduler -- not the simulation -- would dominate: ~40 ms of work spread one
// 8 ms slice per 16.7 ms frame turns an imperceptible boot into a visible one.
const BOOT_BUDGET_MS = 40;
// When the machine is parked in WFI there is nothing to run and nothing to
// paint, so the page stops asking for animation frames and waits on a timer
// instead: requestAnimationFrame fires 60 times a second whether or not it is
// wanted, and 60 wake-ups a second to discover that a hart is still asleep is
// most of what an idle tab costs. A keystroke restores full speed immediately
// rather than waiting for the next tick.
const IDLE_POLL_MS = 250;
const IDLE_SLICE = 512;

const RECV_MAX = 8192;

let mod = null;
let recvBuffer = 0;
let cyclesPerMs = 500;      // seeded, then measured
let running = false;
let bootStart = 0;
let bootMs = 0;
let sawPrompt = false;
let pendingInput = 0;
let lastReadout = 0;

function setState(name) {
  ui.state.textContent = name;
  ui.state.dataset.state = name;
}

const decoder = new TextDecoder('utf-8', { fatal: false });

function drain() {
  let text = '';
  for (;;) {
    const got = mod._ax_recv(recvBuffer, RECV_MAX);
    if (got <= 0) break;
    text += decoder.decode(mod.HEAPU8.subarray(recvBuffer, recvBuffer + got));
    if (got < RECV_MAX) break;
  }
  if (text) {
    term.write(text);
    pendingInput = 0;
  }
  return text.length;
}

function idle() {
  if (pendingInput !== 0) return false;
  // The machine's own signal, and the one that means it: the CPU is parked in
  // WFI, so nothing can happen until an input changes. True of any program
  // that waits, prompt or not.
  if (mod._ax_cpu_idle() === 1) return true;
  // Fallback for the one window where that signal is not yet available. The
  // console driver only parks once the console interrupt has proved itself,
  // and nothing proves it until the first keystroke -- so between boot and the
  // user's first key the shell really is spinning. That is exactly the wait a
  // reader spends looking at the page, so recognising the prompt is worth it.
  // Text-matching is the weaker signal and is deliberately second: it guesses
  // from output, where ax_cpu_idle knows.
  return sawPrompt && term.currentLine().endsWith('aXos> ');
}

let idleTimer = 0;

function schedule() {
  if (!running) return;
  if (idle()) {
    idleTimer = setTimeout(() => { idleTimer = 0; frame(performance.now()); },
                           IDLE_POLL_MS);
  } else {
    requestAnimationFrame(frame);
  }
}

// A keystroke must not wait out an idle timer.
function wake() {
  if (!idleTimer) return;
  clearTimeout(idleTimer);
  idleTimer = 0;
  frame(performance.now());
}

function frame(now) {
  if (!running) return;

  const parked = idle();
  const budgetMs = sawPrompt ? FRAME_BUDGET_MS : BOOT_BUDGET_MS;
  // A parked machine still gets a trickle so simulated time keeps moving --
  // ax_run returns as soon as it parks again, so this costs almost nothing.
  const budget = parked
    ? IDLE_SLICE
    : Math.max(1000, Math.round(cyclesPerMs * budgetMs));

  const start = performance.now();
  const ran = mod._ax_run(budget);
  const elapsed = performance.now() - start;
  if (elapsed > 0.5 && !parked && ran === budget) {
    // Only measure a slice that ran to completion: one cut short by the CPU
    // parking would report the machine as slower than it is.
    // Exponential smoothing, so one slow frame from a background tab or a GC
    // pause does not permanently shrink the slice.
    cyclesPerMs = 0.8 * cyclesPerMs + 0.2 * (ran / elapsed);
  }

  drain();

  if (!sawPrompt && term.currentLine().endsWith('aXos> ')) {
    sawPrompt = true;
    bootMs = performance.now() - bootStart;
    ui.bootTime.textContent = `${bootMs.toFixed(0)} ms`;
    ui.console.focus();
  }

  if (mod._ax_finished()) {
    running = false;
    const code = mod._ax_exit_code();
    setState(code === 0 ? 'halted' : 'failed');
    term.write(`\n[machine halted, exit code ${code}]\n`);
    term.render();
    updateReadout();
    return;
  }

  term.render();
  if (now - lastReadout > 100) {
    lastReadout = now;
    updateReadout();
  }
  setState(idle() ? 'idle' : 'running');
  schedule();
}

function updateReadout() {
  ui.cycles.textContent = mod._ax_cycles().toLocaleString('en-US');
  // cyclesPerMs is only re-measured on non-idle frames, so this always shows
  // the machine's real throughput rather than the throttled idle rate -- and
  // it must be shown unconditionally: a machine that boots and then waits at
  // the prompt is idle at every readout, so a rate hidden while idle is a rate
  // that never appears at all.
  ui.rate.textContent = `${(cyclesPerMs / 1000).toFixed(2)}M cycles/s`;
}

function boot() {
  mod._ax_boot('');
  term.reset();
  sawPrompt = false;
  pendingInput = 0;
  bootMs = 0;
  bootStart = performance.now();
  ui.bootTime.textContent = '…';
  setState('running');
  running = true;
  requestAnimationFrame(frame);
}

function send(byte) {
  if (!running) return;
  mod._ax_send(byte);
  pendingInput += 1;
  wake();
}

function sendText(text) {
  for (const ch of text) {
    const code = ch.codePointAt(0);
    if (code === 10 || code === 13 || (code >= 32 && code < 127)) send(code);
  }
}

ui.console.addEventListener('keydown', (event) => {
  if (event.altKey || event.metaKey) return;
  let byte = -1;
  if (event.ctrlKey) {
    // Ctrl-U is the shell's kill-line; Ctrl-C and Ctrl-D are passed through so
    // the machine decides what they mean rather than the page.
    const letter = event.key.toLowerCase();
    if (letter >= 'a' && letter <= 'z') byte = letter.charCodeAt(0) - 96;
    // Leave paste, and copy-with-a-selection, to the browser: a console the
    // user cannot copy text out of is a worse console than one without Ctrl-C.
    if (letter === 'v' || (letter === 'c' && window.getSelection().toString())) return;
  } else if (event.key.startsWith('Arrow')) {
    // Exactly what a serial terminal sends, because that is what the games
    // decode: inventing a private encoding here would make the page the one
    // place the arrow keys behave differently from the board.
    const letter =
      { ArrowUp: 'A', ArrowDown: 'B', ArrowRight: 'C', ArrowLeft: 'D' }[event.key];
    if (!letter) return;
    event.preventDefault();
    send(27);
    send(91);
    send(letter.charCodeAt(0));
    return;
  } else if (event.key === 'Enter') {
    byte = 13;
  } else if (event.key === 'Backspace') {
    byte = 8;
  } else if (event.key === 'Tab') {
    byte = 9;
  } else if (event.key.length === 1) {
    byte = event.key.charCodeAt(0);
  }
  if (byte < 0) return;
  event.preventDefault();
  send(byte);
});

ui.console.addEventListener('paste', (event) => {
  event.preventDefault();
  sendText(event.clipboardData.getData('text'));
});

ui.console.addEventListener('mouseup', () => {
  if (!window.getSelection().toString()) ui.console.focus();
});

ui.reboot.addEventListener('click', () => {
  boot();
  ui.console.focus();
});

// ---------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------
(async function start() {
  term.write('[web] loading the machine…\n');
  term.render();
  try {
    mod = await createAxSoc();
    recvBuffer = mod._malloc(RECV_MAX);

    // Runtime payload selection: the model's `$readmemh` reads this path when
    // the machine is constructed, so writing the image here -- not at compile
    // time -- is what decides which program boots. One compiled machine, any
    // payload the page cares to fetch.
    const path = mod.UTF8ToString(mod._ax_ram_init_path());
    const response = await fetch('payload.hex', { cache: 'no-cache' });
    if (!response.ok) throw new Error(`payload.hex: HTTP ${response.status}`);
    mod.FS.writeFile(path, new Uint8Array(await response.arrayBuffer()));

    const profile = mod.UTF8ToString(mod._ax_profile());
    ui.profile.textContent = profile;
    ui.provenance.textContent =
      `Verilated ${profile} · RAM image ${path} loaded at run time · ` +
      `clocked by components/harness/common/soc_machine.h`;
    ui.reboot.disabled = false;
    term.reset();
    boot();
  } catch (error) {
    setState('failed');
    term.write(`\n[web] could not start the machine: ${error.message}\n` +
               `[web] this page must be served over HTTP; file:// cannot fetch the module.\n`);
    term.render();
  }
})();
