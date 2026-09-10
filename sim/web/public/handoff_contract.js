// Pure validation and hashing shared by the browser page and its Node check.
// Keeping DOM and machine execution out of this module makes every rejection
// case testable even on hosts that have no browser or Emscripten installation.

export const MANIFEST_SCHEMA = 'org.atomix.browser-experiment-manifest.v1';
const BUNDLE_KEYS = [
  'schema', 'kind', 'id', 'revision', 'summary', 'exported_utc', 'plan',
  'plan_path', 'workload', 'record', 'retrieval', 'reproduce', 'extensions',
];

function require_(condition, message) {
  if (!condition) throw new Error(message);
}

function exactKeys(value, keys, label) {
  require_(value && typeof value === 'object' && !Array.isArray(value),
           `${label} must be an object`);
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  require_(JSON.stringify(actual) === JSON.stringify(expected),
           `${label} fields are malformed`);
}

export function bundlePath(search, fallback) {
  const params = new URLSearchParams(search);
  const selected = params.has('bundle') ? params.get('bundle') : fallback;
  require_(typeof selected === 'string' &&
           /^experiments\/[A-Za-z0-9._-]+\.json$/.test(selected),
           'bundle URL must name one JSON file under experiments/');
  return selected;
}

export function decodeBundle(bytes, maximum) {
  require_(Number.isInteger(maximum) && maximum >= 4096,
           'manifest has an invalid bundle-size limit');
  require_(bytes.byteLength <= maximum,
           `bundle is oversized (${bytes.byteLength} bytes > ${maximum})`);
  let value;
  try {
    value = JSON.parse(new TextDecoder().decode(bytes));
  } catch (error) {
    throw new Error(`bundle is malformed JSON: ${error.message}`);
  }
  return value;
}

function measured(record, metric) {
  const entry = record.measurements && record.measurements[metric];
  require_(entry && entry.status === 'org.atomix.measured' &&
           Number.isSafeInteger(entry.value), `${metric} is not measured`);
  return entry.value;
}

export function validateBundle(bundle, manifest, _source) {
  exactKeys(manifest, ['schema', 'limits', 'default', 'experiments'], 'manifest');
  require_(manifest.schema === MANIFEST_SCHEMA, 'unsupported handoff manifest');
  exactKeys(bundle, BUNDLE_KEYS, 'experiment bundle');
  require_(bundle.kind === 'experiment-bundle' &&
           bundle.schema && bundle.schema.id === 'org.atomix.experiment-bundle' &&
           bundle.schema.major === 1, 'incompatible experiment bundle schema');
  const record = bundle.record;
  require_(record && typeof record.candidate === 'string',
           'bundle has no experiment record candidate');
  const entry = manifest.experiments.find((item) => item.candidate === record.candidate);
  require_(entry, `candidate ${record.candidate} has no staged machine`);
  require_(record.status === 'org.atomix.pass' &&
           record.correctness && record.correctness.status === 'org.atomix.pass',
           'only an oracle-passing experiment can be handed off');

  const candidates = bundle.plan && bundle.plan.candidates;
  const candidate = Array.isArray(candidates)
    ? candidates.find((item) => item.id === record.candidate) : null;
  require_(candidate && candidate.target === entry.target,
           'record and plan select incompatible targets');
  require_(record.identity.target.id === entry.target,
           'record target identity is incompatible with the staged machine');
  require_(record.identity.target.profile_sha256 === entry.profile_sha256,
           'record has a stale profile identity');
  require_(record.identity.implementation.artifact_sha256 === entry.payload_sha256,
           'record has a stale payload identity');
  require_(record.correctness.output_sha256 === entry.output_sha256,
           'record has a stale oracle-output identity');
  require_(measured(record, 'org.atomix.metric.execute-cycles') === entry.execute_cycles,
           'record execute cycles are stale');
  require_(measured(record, 'org.atomix.metric.total-cycles') === entry.total_cycles,
           'record total cycles are stale');
  return entry;
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, '0')).join('');
}

export function exportedBundle(bundle, entry, observation, sourceSha256) {
  const result = structuredClone(bundle);
  result.extensions['org.atomix.browser-run'] = {
    schema: { id: 'org.atomix.browser-run', major: 1, minor: 0 },
    source_bundle_sha256: sourceSha256,
    candidate: entry.candidate,
    identity: {
      machine_profile: entry.machine,
      profile_sha256: entry.profile_sha256,
      payload_sha256: entry.payload_sha256,
    },
    correctness: {
      outputs: observation.outputs,
      output_sha256: observation.outputSha256,
    },
    measurements: {
      execute_cycles: observation.executeCycles,
      total_cycles: observation.totalCycles,
    },
  };
  return result;
}
