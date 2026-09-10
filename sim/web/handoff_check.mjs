// Contract-only AX-08 checks. The real-browser run lives in page_check.mjs;
// these refusals remain available when that optional dependency is absent.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bundlePath, decodeBundle, exportedBundle, validateBundle } from
  './public/handoff_contract.js';

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = join(here, 'public');
const manifest = JSON.parse(readFileSync(join(publicDir, 'handoff.json'), 'utf8'));
const source = bundlePath('', manifest.default);
const bytes = readFileSync(join(publicDir, source));
const valid = decodeBundle(bytes, manifest.limits.max_bundle_bytes);
const entry = validateBundle(valid, manifest, source);

function refused(label, mutate, phrase) {
  try {
    const copy = structuredClone(valid);
    mutate(copy);
    validateBundle(copy, manifest, source);
  } catch (error) {
    if (error.message.includes(phrase)) {
      process.stdout.write(`[handoff] PASS ${label} refused\n`);
      return;
    }
    throw error;
  }
  throw new Error(`${label} was accepted`);
}

try {
  decodeBundle(new TextEncoder().encode('{broken'), manifest.limits.max_bundle_bytes);
  throw new Error('malformed bundle was accepted');
} catch (error) {
  if (!error.message.includes('malformed')) throw error;
  process.stdout.write('[handoff] PASS malformed bundle refused\n');
}
try {
  decodeBundle(new Uint8Array(manifest.limits.max_bundle_bytes + 1),
               manifest.limits.max_bundle_bytes);
  throw new Error('oversized bundle was accepted');
} catch (error) {
  if (!error.message.includes('oversized')) throw error;
  process.stdout.write('[handoff] PASS oversized bundle refused\n');
}
refused('incompatible target', (copy) => { copy.record.identity.target.id = 'other'; },
        'incompatible');
refused('stale profile', (copy) => {
  copy.record.identity.target.profile_sha256 = '0'.repeat(64);
}, 'stale profile');
refused('stale payload', (copy) => {
  copy.record.identity.implementation.artifact_sha256 = '0'.repeat(64);
}, 'stale payload');

const observation = {
  outputs: entry.outputs,
  outputSha256: entry.output_sha256,
  executeCycles: entry.execute_cycles,
  totalCycles: entry.total_cycles,
};
const exported = exportedBundle(valid, entry, observation, '0'.repeat(64));
validateBundle(exported, manifest, source);
process.stdout.write(
  `[handoff] PASS valid ${entry.candidate} remains a native experiment bundle after export\n`);
