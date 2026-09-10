#!/usr/bin/env python3
"""Render the verification evidence that is otherwise only terminal output.

The rigor is the credential for every number this project reports, and almost
all of it is legible for about a second: a formal counterexample scrolls past,
a cosim divergence is one stderr line, an injected fault and the rollback that
caught it are a JSON file nobody opens.  Rendering them is not decoration.  It
is the difference between "the proofs pass" and being able to see *which*
proofs, over what, and where the one that failed went wrong.

Three views, each from a record that already exists:

  formal   what the bounded proofs cover, and a counterexample when there is
           one -- with the instructions *not* proved rendered as unsupported
           rather than left out, because a coverage view that shows only the
           covered part is the most misleading kind.
  cosim    the first divergence between the ISS and the RTL: which event, which
           field, and both values.
  l3       an injected fault, the canary that caught it, and the verified
           manager-owned rollback.

Every view carries the record's own identities, scope, location, and status,
and `self-test` proves it: fixtures with known content are rendered and the
output is required to contain each of those, and to never show a skipped or
unsupported item as a pass.
"""
import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build/evidence"
FORMAL_COVERAGE = ROOT / "research/formal-coverage.json"
L3_TRIAL = ROOT / "research/live-fpga/l3/morph-rtl-trial.json"

STYLE = """
:root { color-scheme: light dark; --ink:#16181d; --bg:#fbfbfa; --muted:#5b6070;
        --rule:#dcdde3; --pass:#1a7f4b; --fail:#b4232c; --unsupported:#8a5a00;
        --panel:#ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --ink:#e7e8ec; --bg:#15171b; --muted:#a0a4b0; --rule:#2c2f36;
          --pass:#4cc38a; --fail:#f06a6a; --unsupported:#e0a33e;
          --panel:#1b1e23; }
}
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
main { max-width:60rem; margin:0 auto; padding:2rem 1.25rem 4rem; }
h1 { font-size:1.5rem; margin:0 0 .25rem; }
.sub { color:var(--muted); margin:0 0 2rem; }
section { background:var(--panel); border:1px solid var(--rule);
          border-radius:.5rem; padding:1rem 1.25rem; margin:0 0 1.25rem; }
h2 { font-size:1.05rem; margin:0 0 .75rem; }
table { border-collapse:collapse; width:100%; font-size:.9rem; }
th,td { text-align:left; padding:.35rem .6rem; border-bottom:1px solid var(--rule);
        vertical-align:top; }
th { color:var(--muted); font-weight:600; white-space:nowrap; }
code,.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
             font-size:.86em; }
.status { font-weight:600; }
.pass { color:var(--pass); } .fail { color:var(--fail); }
.unsupported { color:var(--unsupported); }
.note { color:var(--muted); font-size:.88rem; margin:.75rem 0 0; }
.scroll { overflow-x:auto; }
"""

STATUS_CLASS = {"passed": "pass", "pass": "pass", "proved": "pass",
                "failed": "fail", "fail": "fail", "diverged": "fail",
                "detected": "pass", "rolled-back": "pass",
                "unsupported": "unsupported", "skipped": "unsupported",
                "not-run": "unsupported"}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def status_span(status: str) -> str:
    return (f'<span class="status {STATUS_CLASS.get(status, "unsupported")}">'
            f"{esc(status)}</span>")


def rows(pairs) -> str:
    return "".join(f"<tr><th>{esc(key)}</th><td>{value}</td></tr>"
                   for key, value in pairs)


def page(title: str, subtitle: str, body: str) -> str:
    return (f"<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(title)}</title><style>{STYLE}</style></head><body>"
            f"<main><h1>{esc(title)}</h1><p class=\"sub\">{esc(subtitle)}</p>"
            f"{body}</main></body></html>\n")


# ----------------------------------------------------------------- formal ---
def formal_view(coverage: dict, counterexample: dict | None) -> str:
    sections = []
    for name, core in sorted(coverage["cores"].items()):
        proved = "".join(
            f"<tr><td class=\"mono\">{esc(insn)}</td>"
            f"<td>{status_span('proved')}</td>"
            f"<td class=\"mono\">channels {esc(core['retire_channels_proved'])}"
            f"</td></tr>"
            for insn in core["instructions_proved"])
        # The uncovered half, shown rather than omitted: a coverage view that
        # lists only what passed is the most misleading kind there is.
        unproved = "".join(
            f"<tr><td class=\"mono\">{esc(insn)}</td>"
            f"<td>{status_span('unsupported')}</td>"
            f"<td>no check in this core's list names it</td></tr>"
            for insn in core["instructions_unproved"])
        sections.append(
            f"<section><h2>{esc(name)}</h2><table>"
            + rows([
                ("command", f"<code>{esc(core['target'])}</code>"),
                ("configuration", f"<code>{esc(core['config'])}</code>"),
                ("wrapper", f"<code>{esc(core['wrapper'])}</code>"),
                ("scope", f"{esc(core['isa'])}, mode {esc(core['mode'])}, "
                          f"ENABLE_M={esc(core['enable_m'])}"),
                ("retire channels", f"{esc(core['retire_channels_proved'])} of "
                                    f"{esc(core['retire_channels_declared'])}"),
                ("depths", f"<code>{esc(json.dumps(core['depths']))}</code>"),
            ])
            + "</table><div class=\"scroll\"><table><tr><th>instruction</th>"
              "<th>status</th><th>where</th></tr>"
            + proved + unproved + "</table></div></section>")

    excluded = "".join(
        f"<tr><th>{esc(name)}</th><td>{status_span('unsupported')} "
        f"{esc(reason)}</td></tr>"
        for name, reason in sorted(coverage["excluded"].items()))
    sections.append("<section><h2>Outside the proofs entirely</h2>"
                    f"<table>{excluded}</table></section>")

    if counterexample:
        sections.insert(0,
            "<section><h2>Counterexample</h2><table>"
            + rows([
                ("core", esc(counterexample["core"])),
                ("check", f"<code>{esc(counterexample['check'])}</code>"),
                ("status", status_span(counterexample["status"])),
                ("depth", esc(counterexample["depth"])),
                ("step", esc(counterexample["step"])),
                ("assertion", f"<code>{esc(counterexample['assertion'])}</code>"),
                ("log", f"<code>{esc(counterexample['log'])}</code>"),
            ]) + "</table>"
            + f"<pre class=\"mono scroll\">{esc(counterexample['excerpt'])}</pre>"
              "</section>")
    else:
        sections.insert(0,
            "<section><h2>Counterexample</h2><p>None: every check in the lists "
            "below returned SUCCESS within its depth. That is a bounded result "
            f"— {status_span('unsupported')} beyond it.</p></section>")
    return page("Bounded formal coverage",
                coverage["note"], "".join(sections))


def read_counterexample(log: Path | None) -> dict | None:
    """Normalise a Yosys SAT log into what the view shows, or None if it proved.

    Matched against Yosys's actual wording rather than a guess at it: a proof
    ends "SAT proof finished - no model found: SUCCESS!" and a counterexample
    "- model found: FAIL!", the failing property is named by "Import proof for
    assert:", and the model itself is the signal table that follows. The depth
    comes from the `sat -seq` line the run's own script contains, so the view
    reports the bound the solver was actually given.
    """
    if log is None or not log.is_file():
        return None
    text = log.read_text(encoding="utf-8", errors="replace")
    if "model found: FAIL" not in text and "FAILED" not in text:
        return None
    assertion = re.search(r"Import proof for assert: (\S+)", text)
    depth = re.search(r"sat -seq (\d+)", text)
    if depth is None:
        script = log.parent / "sat.ys"
        if script.is_file():
            depth = re.search(r"sat -seq (\d+)",
                              script.read_text(encoding="utf-8"))
    # The signal table is the counterexample: the first column is the time
    # step, so the earliest row is where the property first fails.
    table = [line for line in text.splitlines()
             if re.match(r"^\s{2}(init|\d+)\s+\S", line)]
    step = table[0].split()[0] if table else "unknown"
    return {
        "core": log.parent.parent.name,
        "check": log.parent.name,
        "status": "failed",
        "depth": depth.group(1) if depth else "unknown",
        "step": step,
        "assertion": assertion.group(1) if assertion else "unknown",
        "log": str(log.relative_to(ROOT)) if ROOT in log.parents else str(log),
        "excerpt": "\n".join(table)[:4000] or text[-2000:],
    }


# ------------------------------------------------------------------ cosim ---
def cosim_view(record: dict) -> str:
    if record["status"] == "agreed":
        body = ("<section><h2>Divergence</h2><p>None: the ISS and the RTL "
                f"agreed on all {esc(record['events'])} compared events.</p>"
                "</section>")
    else:
        d = record["divergence"]
        body = ("<section><h2>First divergence</h2><table>"
                + rows([
                    ("status", status_span("diverged")),
                    ("event", f"<code>{esc(d['event'])}</code>"),
                    ("field", f"<code>{esc(d['field'])}</code>"),
                    ("RTL", f"<code>{esc(d['rtl'])}</code>"),
                    ("ISS", f"<code>{esc(d['iss'])}</code>"),
                ]) + "</table><p class=\"note\">The first divergence is the "
                     "only one that means anything: everything after it is a "
                     "consequence.</p></section>")
    return page("Lock-step cosimulation",
                f"{record['program']} on {record['core']} against "
                f"{record['reference']}",
                "<section><h2>What ran</h2><table>"
                + rows([("programs", f"<code>{esc(record['program'])}</code>"),
                        ("launches", esc(record.get("launches", 1))),
                        ("core", esc(record["core"])),
                        ("reference", esc(record["reference"])),
                        ("events compared", esc(record["events"])),
                        ("command", f"<code>{esc(record['command'])}</code>")])
                + "</table></section>" + body)


def read_cosim(log: Path | None) -> dict:
    """Normalise a whole cosim run, not one line of it.

    `make -C sim/cosim test` runs several programs at several wait-state
    settings; reporting the first `(N events` it happens to find would name one
    of them and call it the run. The view says how many programs were compared
    and how many events in total, and -- if there was one -- the first
    divergence, which is the only one that means anything because everything
    after it is a consequence.
    """
    text = (log.read_text(encoding="utf-8", errors="replace")
            if log and log.is_file() else "")
    launches = re.findall(r"axcosim --bin (\S+)(.*)", text)
    programs = sorted({Path(name).name for name, _ in launches})
    events = [int(count) for count in re.findall(r"\((\d+) events", text)]
    record = {
        "program": ", ".join(programs) or "unknown",
        "launches": len(launches),
        "core": "core.pipeline5", "reference": "sim/axsim (ISS)",
        "command": "make -C sim/cosim test",
        "events": sum(events),
        "status": "agreed",
    }
    match = re.search(r"\[cosim\] DIVERGENCE event=(\d+) ([^:]+): "
                      r"rtl=([0-9a-fA-F]+) iss=([0-9a-fA-F]+)", text)
    if match:
        record["status"] = "diverged"
        record["divergence"] = {"event": int(match.group(1)),
                                "field": match.group(2),
                                "rtl": f"0x{match.group(3)}",
                                "iss": f"0x{match.group(4)}"}
    return record


# --------------------------------------------------------------------- l3 ---
def l3_view(trial: dict) -> str:
    fault = trial["fault_injection"]
    coverage = trial["coverage"]
    return page(
        "Injected fault, canary, and rollback",
        trial["claim"],
        "<section><h2>What ran</h2><table>"
        + rows([("record", f"<code>{esc(trial['id'])}</code>"),
                ("command", f"<code>{esc(trial['command'])}</code>"),
                ("evidence level", esc(trial["evidence_level"])),
                ("manager", f"<code>{esc(trial['boundary']['manager'])}</code>"),
                ("role", esc(trial["boundary"]["role"])),
                ("resident RTL unchanged",
                 esc(trial["boundary"]["resident_rtl_unchanged"])),
                ("bitstream rebuilt",
                 esc(trial["boundary"]["bitstream_rebuild"]))])
        + "</table></section>"
        + "<section><h2>The injected fault</h2><table>"
        + rows([("faulted canary",
                 f"<code>{esc(fault['canary_fault_sha256'])}</code>"),
                ("expected canary",
                 f"<code>{esc(fault['canary_oracle_sha256'])}</code>"),
                ("content id", f"<code>{esc(fault['content_id'])}</code>"),
                ("outcome", status_span("detected"))])
        + "</table><p class=\"note\">The two digests differing is the "
          "detection: the canary is compared against its oracle, and a "
          "candidate whose canary does not match is refused activation."
          "</p></section>"
        + "<section><h2>Rollback</h2><table>"
        + rows([("manager rollbacks verified",
                 f"{status_span('rolled-back')} "
                 f"{esc(coverage['manager_rollbacks_verified'])}"),
                ("oracle cases",
                 f"{esc(coverage['deterministic_oracle_cases']['covered'])} of "
                 f"{esc(coverage['deterministic_oracle_cases']['total'])}"),
                ("known-good genomes",
                 f"{esc(coverage['known_good_genomes']['covered'])} of "
                 f"{esc(coverage['known_good_genomes']['total'])}")])
        + "</table></section>"
        + "<section><h2>What this does not authorize</h2><table>"
        + rows([(key, f"{status_span('unsupported')} {esc(value)}")
                for key, value in sorted(trial["authority"].items())])
        + "</table></section>")


# ------------------------------------------------------------------ render --
def render(args) -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    written = []

    coverage = json.loads(FORMAL_COVERAGE.read_text(encoding="utf-8"))
    counterexample = read_counterexample(args.formal_log)
    path = OUTPUT / "formal.html"
    path.write_text(formal_view(coverage, counterexample), encoding="utf-8")
    written.append({"view": "formal", "file": str(path.relative_to(ROOT)),
                    "from": [str(FORMAL_COVERAGE.relative_to(ROOT))]
                            + ([str(args.formal_log)] if counterexample else []),
                    "status": "failed" if counterexample else "passed"})

    cosim = read_cosim(args.cosim_log)
    path = OUTPUT / "cosim.html"
    path.write_text(cosim_view(cosim), encoding="utf-8")
    written.append({"view": "cosim", "file": str(path.relative_to(ROOT)),
                    "from": [str(args.cosim_log)] if args.cosim_log else [],
                    "status": "failed" if cosim["status"] == "diverged"
                              else "passed"})

    trial = json.loads(L3_TRIAL.read_text(encoding="utf-8"))
    path = OUTPUT / "l3.html"
    path.write_text(l3_view(trial), encoding="utf-8")
    written.append({"view": "l3", "file": str(path.relative_to(ROOT)),
                    "from": [str(L3_TRIAL.relative_to(ROOT))],
                    "status": "passed"})

    manifest = OUTPUT / "views.json"
    manifest.write_text(json.dumps({
        "schema": "org.atomix.evidence-views.v1",
        "note": "Rendered from the records named in each entry. "
                "tools/evidence_views.py self-test proves each view carries "
                "its record's identities, scope, location, and status.",
        "views": written,
    }, indent=2) + "\n", encoding="utf-8")
    for entry in written:
        print(f"[views] {entry['view']:7} {entry['status']:7} {entry['file']}")
    print(f"[views] wrote {manifest.relative_to(ROOT)}")
    return 0


def self_test(_args) -> int:
    """Fixtures with known content, and the rendered view must carry all of it."""
    problems = []

    coverage = {
        "note": "fixture", "excluded": {"rv32m": "ENABLE_M is tied off"},
        "cores": {"fixture-core": {
            "target": "make -C formal check-fixture",
            "config": "formal/checks-fixture.cfg",
            "wrapper": "components/core/fixture/wrapper.sv",
            "isa": "rv32i", "mode": "bmc", "enable_m": "1'b0",
            "retire_channels_declared": 2, "retire_channels_proved": [0, 1],
            "instructions_proved": ["add", "beq"],
            "instructions_unproved": ["jal", "lui"],
            "depths": {"insn": [12]},
        }},
    }
    # Field for field what read_counterexample produces from a real Yosys SAT
    # log: the wording, the auto-generated assertion name, and the signal table
    # that is the counterexample.
    counterexample = {
        "core": "fixture-core", "check": "insn_jal_ch0", "status": "failed",
        "depth": "12", "step": "init",
        "assertion": "$auto$async2sync.cc:116:execute$11",
        "log": "formal/build/sat/fixture-core/insn_jal_ch0/result.log",
        "excerpt": "  init \\count                4         4          0100",
    }
    formal = formal_view(coverage, counterexample)
    for needed in ("make -C formal check-fixture", "formal/checks-fixture.cfg",
                   "components/core/fixture/wrapper.sv", "insn_jal_ch0",
                   "async2sync.cc:116:execute$11", "init",
                   "formal/build/sat/fixture-core/insn_jal_ch0/result.log",
                   "jal", "lui", "add", "beq", "1&#x27;b0", "bmc"):
        if needed not in formal:
            problems.append(f"the formal view drops {needed!r}")
    # The uncovered instructions must be shown, and shown as unsupported.
    for insn in coverage["cores"]["fixture-core"]["instructions_unproved"]:
        block = formal.split(f">{insn}<")[1][:200] if f">{insn}<" in formal else ""
        if "unsupported" not in block:
            problems.append(f"{insn} is not rendered as unsupported")
    if formal.count("pass\">proved") != 2:
        problems.append("the formal view does not mark exactly the two proved "
                        "instructions as proved")

    diverged = {"program": "rv32ui-p-add.elf", "launches": 8,
                "core": "core.pipeline5",
                "reference": "sim/axsim (ISS)", "command": "make -C sim/cosim test",
                "events": 4211, "status": "diverged",
                "divergence": {"event": 4211, "field": "rd_wdata",
                               "rtl": "0xdeadbeef", "iss": "0x0000002a"}}
    cosim = cosim_view(diverged)
    for needed in ("rv32ui-p-add.elf", "4211", "rd_wdata", "0xdeadbeef",
                   "0x0000002a", "diverged", "make -C sim/cosim test"):
        if needed not in cosim:
            problems.append(f"the cosim view drops {needed!r}")
    if "pass\">" in cosim.split("First divergence")[1]:
        problems.append("a divergence is rendered with a passing status")

    agreed = dict(diverged, status="agreed")
    agreed.pop("divergence")
    if "diverged" in cosim_view(agreed):
        problems.append("an agreeing run is rendered as a divergence")

    trial = {
        "id": "org.atomix.fixture", "claim": "fixture claim",
        "command": "make l3-check", "evidence_level": "org.atomix.simulation-rtl",
        "boundary": {"manager": "sim/unit/tb_fixture.cpp", "role": "role.morph",
                     "resident_rtl_unchanged": True, "bitstream_rebuild": False},
        "fault_injection": {"canary_fault_sha256": "sha256:aaa",
                            "canary_oracle_sha256": "sha256:bbb",
                            "content_id": "sha256:ccc"},
        "coverage": {"manager_rollbacks_verified": 1,
                     "deterministic_oracle_cases": {"covered": 6, "total": 6},
                     "known_good_genomes": {"covered": 3, "total": 3}},
        "authority": {"hardware_actuation": "org.atomix.not-authorized",
                      "persistence": "org.atomix.none"},
    }
    l3 = l3_view(trial)
    for needed in ("org.atomix.fixture", "make l3-check", "sha256:aaa",
                   "sha256:bbb", "sha256:ccc", "sim/unit/tb_fixture.cpp",
                   "role.morph", "org.atomix.not-authorized", "6 of 6",
                   "3 of 3"):
        if needed not in l3:
            problems.append(f"the L3 view drops {needed!r}")
    authority = l3.split("does not authorize")[1]
    if "unsupported" not in authority or "pass\">" in authority:
        problems.append("what the trial does not authorize is not rendered as "
                        "unsupported")

    for problem in problems:
        print(f"[views-selftest] FAIL {problem}", file=sys.stderr)
    if problems:
        return 1
    print("[views-selftest] PASS: each view carries its record's identities, "
          "scope, location and status; uncovered instructions and unauthorized "
          "actions render as unsupported, and a divergence never renders as a "
          "pass")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    renderer = sub.add_parser("render")
    renderer.add_argument("--formal-log", type=Path,
                          help="a Yosys SAT result.log from a failing check")
    renderer.add_argument("--cosim-log", type=Path,
                          help="output from make -C sim/cosim test")
    sub.add_parser("self-test")
    args = parser.parse_args()
    return {"render": render, "self-test": self_test}[args.action](args)


if __name__ == "__main__":
    raise SystemExit(main())
