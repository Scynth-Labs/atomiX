// Headless boot of the WebAssembly machine under Node.
//
// This is the WASM counterpart of a batch `make sim` run, and it exists to
// answer one question with a number rather than a guess: booting an entire
// computer costs about 25 ms natively, and if WebAssembly makes that 4x worse
// it is still imperceptible, while 40x worse would mean the browser console is
// not worth building. Running it here rather than in a tab keeps the
// measurement out of reach of frame scheduling, layout, and a terminal widget.
//
// Because it drives the machine through exactly the API the page uses, it also
// serves as the page's self-check: if this passes, the page's machine works.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const { default: createAxSoc } = await import(join(here, 'public', 'axsoc.js'));

const args = process.argv.slice(2);
const bench = args.includes('--bench');
// Sustained native cycles/s measured on *this* host, if the caller took the
// trouble. Without it the only available comparison is against a baseline
// recorded on another machine, which conflates "WebAssembly is slower" with
// "this laptop is slower" -- and those have opposite consequences.
const nativeRate = Number(
  (args.find((a) => a.startsWith('--native-rate=')) || '').split('=')[1]) || 0;

// The boot cycle count recorded in sim/web/README.md and the checklist, for
// the default profile and payload. It is here purely to notice drift: the
// count is a property of the RTL and the payload, and a page quoting a number
// the machine no longer produces is exactly what this milestone exists to
// prevent. It is not a pass condition -- changing the RTL is allowed.
const RECORDED_BOOT_CYCLES = 27509;

const SLICE = 200000;      // cycles per ax_run call
const CYCLE_LIMIT = 5e6;   // a boot that needs this many cycles has hung

const module = await createAxSoc();

// Runtime payload selection: the model's `$readmemh` reads this path when
// ax_boot() constructs it, so writing the image first is what decides which
// program this machine runs. Nothing is baked into the WASM.
const payloadPath = module.UTF8ToString(module._ax_ram_init_path());
module.FS.writeFile(payloadPath, readFileSync(join(here, 'public', 'payload.hex')));

const profile = module.UTF8ToString(module._ax_profile());

// One reused scratch buffer: allocating per drain would measure the allocator.
const RECV_MAX = 4096;
const recvBuffer = module._malloc(RECV_MAX);

let console_text = '';
function drain() {
  for (;;) {
    const got = module._ax_recv(recvBuffer, RECV_MAX);
    if (got <= 0) break;
    for (let i = 0; i < got; ++i) console_text += String.fromCharCode(module.HEAPU8[recvBuffer + i]);
  }
}

function send(text) {
  for (const ch of text) module._ax_send(ch.charCodeAt(0));
}

// Clock until `marker` appears on the console, or give up. Returns the exact
// cycle on which the marker completed, which is the number worth comparing --
// so this steps output byte by output byte rather than in coarse slices. A
// slice sized for a browser frame is several times a whole boot, which would
// make "boot to prompt" a measurement of the slice size instead.
function runUntil(marker, limit = CYCLE_LIMIT) {
  const from = console_text.length;
  const start = module._ax_cycles();
  while (module._ax_cycles() - start < limit) {
    if (module._ax_run_to_output(SLICE) === 0) break;
    drain();
    if (console_text.indexOf(marker, from) >= 0) return module._ax_cycles();
    if (module._ax_finished()) break;
  }
  return -1;
}

function fail(message) {
  process.stderr.write(`[web] FAIL ${message}\n`);
  if (console_text) process.stderr.write(`--- console ---\n${console_text}\n---------------\n`);
  process.exit(1);
}

// Two passes, because one measurement cannot answer both questions honestly.
// The first steps output byte by output byte to find the exact cycle the
// prompt completed -- accurate, but paying a JS call per console byte, which
// is measurement cost the page never pays. The second boots a fresh machine
// and clocks exactly that many cycles the way a page does, in slices, which is
// what "booting a computer costs N ms" actually means here.
module._ax_boot('');
const bootCycles = runUntil('aXos> ');
if (bootCycles < 0) fail('never reached the aXos shell prompt');

module._ax_boot('');
console_text = '';
const wallStart = performance.now();
for (let done = 0; done < bootCycles; ) done += module._ax_run(Math.min(SLICE, bootCycles - done));
const bootMs = performance.now() - wallStart;
drain();
if (console_text.indexOf('aXos> ') < 0) fail('replayed boot did not reproduce the prompt');

// The same continuity proof the native interactive session uses. A machine
// that reboots between commands reports irq=1 twice; only one continuous
// machine reports irq=1 then irq=2, so this is what distinguishes a session
// from two batch runs -- and it has to hold in the browser too, or the page is
// showing something other than what it claims.
send('role\n');
if (runUntil('irq=1') < 0) fail('first role job did not report irq=1');
send('role\n');
if (runUntil('irq=2') < 0) fail('second role job did not report irq=2 (machine did not persist)');

const cyclesPerSecond = bootCycles / (bootMs / 1000);

process.stdout.write(`[web] profile ${profile}, payload ${payloadPath}\n`);
process.stdout.write(
  `[web] boot to prompt: ${bootCycles} cycles in ${bootMs.toFixed(1)} ms ` +
  `(${(cyclesPerSecond / 1e6).toFixed(2)}M cycles/s)\n`);
if (nativeRate > 0) {
  process.stdout.write(
    `[web] same-host native: ${(nativeRate / 1e6).toFixed(2)}M cycles/s -> ` +
    `WASM is ${(nativeRate / cyclesPerSecond).toFixed(2)}x slower, ` +
    `native boot ${(bootCycles / nativeRate * 1000).toFixed(1)} ms ` +
    `vs WASM ${bootMs.toFixed(1)} ms\n`);
}

if (bootCycles !== RECORDED_BOOT_CYCLES) {
  // Not a failure: the RTL and the payload are allowed to move, and a
  // non-default KERNEL_CONFIG legitimately boots in a different number of
  // cycles. Saying so is the point -- the recorded figure in sim/web/README.md
  // and docs/design-checklist.md now needs re-recording, and silence here is
  // how those documents would come to describe a machine that no longer exists.
  process.stdout.write(
    `[web] note: boot cycles differ from the recorded ${RECORDED_BOOT_CYCLES}; ` +
    `re-record it, or check which payload is staged\n`);
}

if (bench) {
  // Sustained rate over repeated boots.
  //
  // It used to clock a running machine for a fixed budget, which stopped
  // working the moment WFI began parking the hart: at an idle prompt ax_run
  // returns immediately and forever, so that loop would never terminate. That
  // is the measurement telling the truth -- there is no throughput to sustain
  // on a machine that is deliberately doing nothing -- so the workload has to
  // be real work. A boot is the most representative one available.
  const benchCycles = 2e6;
  const start = performance.now();
  let ran = 0;
  while (ran < benchCycles) {
    module._ax_boot('');
    let booted = 0;
    for (;;) {
      const step = module._ax_run(SLICE);
      if (step === 0) break;            // parked: this boot is done
      booted += step;
      ran += step;
      if (ran >= benchCycles) break;
    }
    drain();
    if (booted === 0) break;            // nothing ran at all; do not spin
  }
  const ms = performance.now() - start;
  process.stdout.write(
    `[web] sustained: ${(ran / (ms / 1000) / 1e6).toFixed(2)}M cycles/s ` +
    `over ${ran} cycles\n`);
}

process.stdout.write('[web] PASS headless WASM boot reached the aXos shell and kept one machine across commands\n');
module._free(recvBuffer);
module._ax_shutdown();
