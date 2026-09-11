# Experiment-alpha pilot report

Copy this file for one independent AX-04 reproduction. Keep the prompts and
replace every italic instruction. Accepted reports go in
`research/experiments/pilots/` as described by that directory's README. A result
is useful even when a command fails; record the failure and any workaround
instead of polishing it away.

## Participant and checkout

- Participant: *name or stable pseudonym*
- Relationship to implementation: *confirm that you did not implement the
  experiment runner or walkthrough*
- Date and host OS/architecture: *date; OS; CPU architecture*
- Path attempted: *A — native only, or B — native and RTL*
- Source revision: *full output of `git rev-parse HEAD`*
- Initial `git status --short`: *empty, or list/explain every entry*
- Checkout/setup help received: *none, or who/what helped*

## Prerequisites and timing

- Tools already installed: *tool names and versions*
- Tools installed for the pilot: *tool names, versions, and installation time*
- Prerequisite/setup time: *duration before the first `make`, including reading*
- First-run build time: *wall-clock duration of the first experiment run*
- Interaction time: *active time after the first run, excluding compilation*
- Time to first understood comparison: *build plus interaction up to that point*
- Met the 15-minute target after prerequisites: *yes/no; if no, where time went*

## What happened

- First comparison: *command, outcome, and what you understood from it*
- Declared choice changed: *command and the plan/execution choice it changed*
- Effect of the change: *what reran or was reused, and what changed in the record*
- Record replayed: *command, record path, and pass/failure outcome*
- Tradeoff shown by the comparison: *in your own words*
- Evidence limitation: *one claim these records do not support, in your own words*

## Friction and help

- Failures and warnings: *every failure, including recovered failures; write
  `none observed` only if there were none*
- Workarounds: *commands or edits used, or none*
- Help needed after starting: *person, document, search, or none*
- Most confusing step: *what it was and what would have made it clearer*
- Suggested change before the next pilot: *one concrete improvement, or none*

## Participant conclusion

*In two or three sentences, say whether you could reproduce and explain the
result without unsupported hardware or cross-domain performance claims.*
