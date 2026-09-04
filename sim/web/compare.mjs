// Several machines, one binary, side by side -- headless.
//
// This is the evidence behind the comparison page, and its self-check. The
// argument for a component system is not that a build can be reconfigured; it
// is that reconfiguring it changes the machine in a way you can measure. So
// every selection here runs the *same* `cpu_perf` image to completion and
// reports its own cycle count, and the program's checksum must come out
// identical on all of them: same computation, different machine.
//
// The failure this must catch is subtler than a wrong number. Three bundles
// staged under three labels can silently be one bundle -- MODULARIZE puts the
// module factory on a global, so a page that loaded three of them under one
// export name would keep only the last, and would then run one machine three
// times while labelling it three ways. Every machine is therefore asked what
// it is (`ax_profile()`) and checked against the label it was staged under,
// and identical cycle counts are treated as a failure rather than a curiosity.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const machinesDir = join(here, 'public', 'machines');

let manifest;
try {
  manifest = JSON.parse(readFileSync(join(machinesDir, 'machines.json'), 'utf8'));
} catch (error) {
  process.stderr.write(
    `[compare] no staged machines (${error.message})\n` +
    `[compare] build them first: make -C sim/web machines\n`);
  process.exit(2);
}

const SLICE = 200000;
const CYCLE_LIMIT = 2e7;   // a run needing this many cycles has hung
const RECV_MAX = 8192;

function fail(message, transcript) {
  process.stderr.write(`[compare] FAIL ${message}\n`);
  if (transcript) process.stderr.write(`--- console ---\n${transcript}\n---------------\n`);
  process.exit(1);
}

// Boot one staged machine on the shared payload and run it to the finisher.
async function run(entry, payload) {
  const { default: create } = await import(join(machinesDir, entry.module));
  const mod = await create();

  // Runtime payload selection, the same way the page does it: the model's
  // `$readmemh` reads this path when ax_boot() constructs the machine, so the
  // image is chosen here rather than at elaboration.
  const path = mod.UTF8ToString(mod._ax_ram_init_path());
  mod.FS.writeFile(path, payload);

  const reported = mod.UTF8ToString(mod._ax_profile());
  if (reported !== entry.name) {
    fail(`machine staged as ${entry.name} reports itself as ${reported}; ` +
         `the bundles are not the selections they are labelled with`);
  }

  const buffer = mod._malloc(RECV_MAX);
  let text = '';
  const drain = () => {
    for (;;) {
      const got = mod._ax_recv(buffer, RECV_MAX);
      if (got <= 0) break;
      for (let i = 0; i < got; ++i) text += String.fromCharCode(mod.HEAPU8[buffer + i]);
    }
  };

  mod._ax_boot('');
  const start = performance.now();
  while (!mod._ax_finished() && mod._ax_cycles() < CYCLE_LIMIT) {
    if (mod._ax_run(SLICE) === 0) break;   // parked with nothing to wake it
    drain();
  }
  const ms = performance.now() - start;
  drain();

  const result = {
    ...entry,
    cycles: mod._ax_cycles(),
    finished: mod._ax_finished() === 1,
    exitCode: mod._ax_exit_code(),
    ms,
    text,
  };
  mod._free(buffer);
  mod._ax_shutdown();
  return result;
}

const payload = readFileSync(join(machinesDir, manifest.payload));
const results = [];
for (const entry of manifest.machines) results.push(await run(entry, payload));

// --- What the run has to have produced --------------------------------------
for (const r of results) {
  if (!r.finished) fail(`${r.name} never reached the finisher (${r.cycles} cycles)`, r.text);
  if (r.exitCode !== 0) fail(`${r.name} exited ${r.exitCode}`, r.text);
  if (!/cpu_perf: PASS/.test(r.text)) fail(`${r.name} did not report cpu_perf: PASS`, r.text);
  const match = /checksum=(0x[0-9a-fA-F]+)/.exec(r.text);
  if (!match) fail(`${r.name} printed no checksum`, r.text);
  r.checksum = match[1];
  // `cpu_perf measured:` counts only the workloads, excluding startup and the
  // printing around them, which is the number worth comparing across cores.
  const measured = /cpu_perf measured: cycles=(\d+)/.exec(r.text);
  r.measured = measured ? Number(measured[1]) : r.cycles;
}

// The same program, so the same answer -- on every machine. A differing
// checksum would mean the cores disagree about RV32IM, which is a correctness
// bug, not a performance result.
const checksums = new Set(results.map((r) => r.checksum));
if (checksums.size !== 1) {
  fail(`the machines disagree on the result: ${
    results.map((r) => `${r.name}=${r.checksum}`).join(', ')}`);
}

// Distinct machines produce distinct cycle counts on the same binary. Equal
// ones mean the same bundle was staged twice, which is precisely the mistake a
// side-by-side page would present most convincingly.
const counts = new Set(results.map((r) => r.measured));
if (counts.size !== results.length) {
  fail(`two machines ran the binary in the same number of cycles: ${
    results.map((r) => `${r.name}=${r.measured}`).join(', ')}`);
}

// --- Report ------------------------------------------------------------------
const slowest = Math.max(...results.map((r) => r.measured));
const width = Math.max(...results.map((r) => r.name.length));
process.stdout.write(
  `[compare] ${results.length} machines, one binary (${manifest.payload}), ` +
  `checksum ${results[0].checksum}\n`);
for (const r of results) {
  process.stdout.write(
    `[compare]   ${r.name.padEnd(width)}  ${String(r.core).padEnd(16)} ` +
    `${String(r.measured).padStart(8)} cycles  ` +
    `${(slowest / r.measured).toFixed(2)}x  ` +
    `(boot to halt ${r.cycles} in ${r.ms.toFixed(0)} ms)\n`);
}
process.stdout.write(
  '[compare] PASS same binary, same result, and a different machine under each label\n');
