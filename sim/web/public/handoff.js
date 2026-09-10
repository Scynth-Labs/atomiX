// Open one AX-03 bundle, bind it to exactly one staged machine, run it, and
// append the browser observation without changing any native-replay field.
import {
  bundlePath, canonicalJson, decodeBundle, exportedBundle, sha256Hex,
  validateBundle,
} from './handoff_contract.js';

const ui = {
  state: document.getElementById('handoff-state'),
  candidate: document.getElementById('handoff-candidate'),
  note: document.getElementById('handoff-note'),
  profile: document.getElementById('handoff-profile'),
  profileSha: document.getElementById('handoff-profile-sha'),
  payloadSha: document.getElementById('handoff-payload-sha'),
  outputSha: document.getElementById('handoff-output-sha'),
  execute: document.getElementById('handoff-execute'),
  total: document.getElementById('handoff-total'),
  machineState: document.getElementById('machine-state'),
  machineName: document.getElementById('machine-name'),
  screen: document.getElementById('handoff-screen'),
  export: document.getElementById('export'),
};

const RECV_MAX = 8192;
const SLICE = 200000;
const decoder = new TextDecoder('utf-8', { fatal: false });

function state(name, text) {
  ui.state.dataset.state = name;
  ui.state.textContent = text || name;
}

function fail(error) {
  state('failed', 'refused');
  ui.machineState.dataset.state = 'failed';
  ui.machineState.textContent = 'not run';
  ui.note.textContent = `Refused: ${error.message}`;
  ui.export.disabled = true;
  ui.export.dataset.nativeReplay = 'unavailable';
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error(`could not load ${src}`));
    document.head.appendChild(script);
  });
}

function run(mod, maximum) {
  return new Promise((resolve, reject) => {
    const buffer = mod._malloc(RECV_MAX);
    let text = '';
    const term = new Terminal(ui.screen);
    const drain = () => {
      for (;;) {
        const got = mod._ax_recv(buffer, RECV_MAX);
        if (got <= 0) break;
        const chunk = decoder.decode(mod.HEAPU8.subarray(buffer, buffer + got));
        text += chunk;
        term.write(chunk);
      }
      term.render();
    };
    mod._ax_boot('');
    ui.machineState.dataset.state = 'running';
    ui.machineState.textContent = 'running';
    const frame = () => {
      try {
        mod._ax_run(SLICE);
        drain();
        const cycles = mod._ax_cycles();
        if (mod._ax_finished()) {
          const exitCode = mod._ax_exit_code();
          mod._free(buffer);
          if (exitCode !== 0) reject(new Error(`machine exited ${exitCode}`));
          else resolve({ text, cycles });
          return;
        }
        if (cycles > maximum) {
          mod._free(buffer);
          reject(new Error(`machine exceeded its ${maximum}-cycle record bound`));
          return;
        }
        setTimeout(frame, 2);
      } catch (error) {
        mod._free(buffer);
        reject(error);
      }
    };
    setTimeout(frame, 2);
  });
}

function download(bundle, candidate) {
  const blob = new Blob([`${JSON.stringify(bundle, null, 2)}\n`],
                        { type: 'application/json' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `${candidate.split('.').at(-1)}-browser.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}

(async function main() {
  try {
    const manifestResponse = await fetch('handoff.json', { cache: 'no-cache' });
    if (!manifestResponse.ok) throw new Error('handoff manifest is not staged');
    const manifest = await manifestResponse.json();
    const source = bundlePath(window.location.search, manifest.default);
    const bundleResponse = await fetch(source, { cache: 'no-cache' });
    if (!bundleResponse.ok) throw new Error(`could not load ${source}`);
    const bytes = new Uint8Array(await bundleResponse.arrayBuffer());
    const bundle = decodeBundle(bytes, manifest.limits.max_bundle_bytes);
    const entry = validateBundle(bundle, manifest, source);
    const sourceSha256 = await sha256Hex(bytes);

    ui.candidate.textContent = entry.candidate;
    ui.profile.textContent = entry.machine;
    ui.profileSha.textContent = entry.profile_sha256;
    ui.payloadSha.textContent = entry.payload_sha256;
    ui.outputSha.textContent = entry.output_sha256;
    ui.machineName.textContent = entry.machine;
    state('running');

    const payloadResponse = await fetch(entry.payload, { cache: 'no-cache' });
    if (!payloadResponse.ok) throw new Error(`could not load ${entry.payload}`);
    const payload = new Uint8Array(await payloadResponse.arrayBuffer());
    const payloadSha256 = await sha256Hex(payload);
    if (payloadSha256 !== entry.payload_sha256) {
      throw new Error(`stale payload bytes ${payloadSha256} != ${entry.payload_sha256}`);
    }

    await loadScript(entry.module);
    const factory = window[entry.export];
    if (!factory) throw new Error(`${entry.module} defined no ${entry.export}`);
    const mod = await factory();
    mod.FS.writeFile(mod.UTF8ToString(mod._ax_ram_init_path()), payload);
    const profile = mod.UTF8ToString(mod._ax_profile());
    if (profile !== entry.machine) {
      throw new Error(`machine reports ${profile}, record requires ${entry.machine}`);
    }
    const result = await run(mod, entry.max_cycles);
    const measured = /cpu_perf measured: cycles=(\d+) checksum=0x([0-9a-fA-F]+)/
      .exec(result.text);
    if (!measured || !/cpu_perf: PASS/.test(result.text)) {
      throw new Error('machine produced no passing cpu_perf result');
    }
    const executeCycles = Number(measured[1]);
    const outputs = { checksum: [Number.parseInt(measured[2], 16)] };
    const outputDocument = { [entry.case]: outputs };
    const outputSha256 = await sha256Hex(
      new TextEncoder().encode(canonicalJson(outputDocument)));
    if (canonicalJson(outputs) !== canonicalJson(entry.outputs) ||
        outputSha256 !== entry.output_sha256) {
      throw new Error('oracle output does not match the experiment record');
    }
    if (executeCycles !== entry.execute_cycles || result.cycles !== entry.total_cycles) {
      throw new Error(
        `deterministic cycles changed: ${executeCycles}/${result.cycles} != ` +
        `${entry.execute_cycles}/${entry.total_cycles}`);
    }

    ui.execute.textContent = executeCycles.toLocaleString('en-US');
    ui.total.textContent = result.cycles.toLocaleString('en-US');
    ui.machineState.dataset.state = 'halted';
    ui.machineState.textContent = 'halted';
    state('passed');
    ui.note.textContent =
      'Profile, payload, oracle output, workload cycles, and total cycles match the record.';
    const observation = { outputs, outputSha256, executeCycles,
                          totalCycles: result.cycles };
    const ready = exportedBundle(bundle, entry, observation, sourceSha256);
    ui.export.disabled = false;
    ui.export.dataset.nativeReplay = 'ready';
    ui.export.addEventListener('click', () => download(ready, entry.candidate));
  } catch (error) {
    fail(error);
  }
})();
