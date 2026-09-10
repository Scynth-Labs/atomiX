#!/usr/bin/env python3
"""Render experiment records as a static site anyone can check.

The point of publishing results is not that they look official.  It is that a
reader who does not trust them can find out.  So every page here is built from
the record files in the repository and nothing else, and every number on it is
one click from the JSON it came from, the method that produced it, the hashes
of the artifact and machine that produced it, and the commands that produce it
again.

The site therefore states what it is built from and what it is not:

- it renders committed records, so it can only ever be as current as they are,
  and the commit each record was made at is printed beside it;
- it applies the same eligibility and comparability rules as the local report,
  because it imports them rather than restating them; and
- it ranks within a measurement domain and refuses to rank across, in exactly
  the places the local report does.

`build` writes the site; `check` regenerates it and requires the result to be
byte-identical to what is on disk, so a page cannot drift from its records.
"""

from __future__ import annotations

import argparse
import filecmp
import html
import json
import shutil
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import experiment_contract as ec
import experiment_report as er
import personality_contract as pc
from execution.contract import ROOT, sha256_bytes

DEFAULT_PLANS = ROOT / "research" / "experiments"
DEFAULT_RECORDS = DEFAULT_PLANS / "records"
DEFAULT_OUTPUT = ROOT / "build" / "pages"
REPOSITORY = "https://github.com/Scynth-Labs/atomiX"

STYLE = """\
:root {
  --bg: #0d1117;
  --panel: #131a23;
  --edge: #243040;
  --ink: #c9d4e2;
  --dim: #7c8899;
  --accent: #56d364;
  --warn: #d29922;
  --mono: ui-monospace, "SF Mono", "JetBrains Mono", "DejaVu Sans Mono", monospace;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 2rem 1.25rem 4rem;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.shell { max-width: 68rem; margin: 0 auto; }
a { color: var(--accent); }
a:hover { color: #7ee787; }
h1 { font-family: var(--mono); font-size: 1.9rem; letter-spacing: -0.02em; margin: 0 0 .3rem; }
h1::after {
  content: ""; display: inline-block; width: .5rem; height: 1.05rem;
  margin-left: .4rem; vertical-align: -.12rem; background: var(--accent);
}
h2 { font-size: 1.15rem; margin: 2.5rem 0 .75rem; }
h3 { font-size: .95rem; margin: 1.75rem 0 .5rem; color: var(--ink); }
p { max-width: 52rem; }
.lede { color: var(--dim); margin: 0 0 1.5rem; max-width: 52rem; }
.claim {
  display: inline-block; font-family: var(--mono); font-size: .75rem;
  text-transform: uppercase; letter-spacing: .06em; padding: .2rem .5rem;
  border: 1px solid var(--edge); border-radius: 4px; color: var(--accent);
  background: #0f151d;
}
.panel {
  border: 1px solid var(--edge); border-radius: 10px; background: var(--panel);
  padding: 1rem 1.1rem; margin: 1rem 0;
}
table { border-collapse: collapse; width: 100%; font-size: .9rem; margin: .5rem 0 0; }
th, td { text-align: left; padding: .4rem .6rem; border-bottom: 1px solid var(--edge); }
th { color: var(--dim); font-weight: 600; font-size: .78rem;
     text-transform: uppercase; letter-spacing: .04em; }
td.num, th.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
td.hash, .hash { font-family: var(--mono); font-size: .8rem; color: var(--dim); }
tr.front td { color: var(--accent); }
.mark { color: var(--accent); font-family: var(--mono); }
.absent td { color: var(--dim); font-style: italic; }
pre {
  font-family: var(--mono); font-size: .82rem; background: #0f151d;
  border: 1px solid var(--edge); border-radius: 8px; padding: .8rem .9rem;
  overflow-x: auto; color: var(--ink);
}
code { font-family: var(--mono); font-size: .88em; }
.warn { color: var(--warn); }
.note { color: var(--dim); font-size: .9rem; }
ul.reasons { list-style: none; padding: 0; margin: .5rem 0 0; }
ul.reasons li { padding: .3rem 0; border-bottom: 1px solid var(--edge); font-size: .9rem; }
ul.reasons li:last-child { border-bottom: 0; }
ul.reasons .who { font-family: var(--mono); color: var(--ink); }
footer { margin-top: 3rem; color: var(--dim); font-size: .85rem; }
.cards { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(19rem, 1fr)); }
.card { border: 1px solid var(--edge); border-radius: 10px; background: var(--panel); padding: 1rem 1.1rem; }
.card h3 { margin-top: 0; }
"""


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def page(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)}</title>\n"
        '<link rel="stylesheet" href="style.css">\n'
        "</head>\n<body>\n"
        f'<main class="shell">\n{body}\n</main>\n</body>\n</html>\n'
    )


def number(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.3f}"


def commands(plan_path: Path, plan: dict[str, Any]) -> str:
    relative = plan_path.relative_to(ROOT) if plan_path.is_relative_to(ROOT) else plan_path
    candidate = plan["candidates"][0]["id"].split(".")[-1]
    return (
        f"git clone {REPOSITORY}\n"
        "cd atomiX\n"
        f"make experiment-run EXPERIMENT_PLAN={relative} \\\n"
        "  EXPERIMENT_RECORDS=build/experiments/records\n"
        f"make experiment-report EXPERIMENT_PLAN={relative} \\\n"
        "  EXPERIMENT_READ=build/experiments/records\n"
        "\n# or take one result and rebuild it from its own description:\n"
        f"make experiment-export EXPERIMENT_PLAN={relative} CANDIDATE={candidate}\n"
        "make experiment-reproduce"
    )


def data_file(output: Path, name: str, document: dict[str, Any]) -> tuple[str, str]:
    """Copy one JSON document into the site and return its link and digest."""
    text = json.dumps(document, indent=2) + "\n"
    (output / "data").mkdir(parents=True, exist_ok=True)
    (output / "data" / name).write_text(text)
    return f"data/{name}", sha256_bytes(text.encode())


def provenance(records: dict[str, dict[str, Any]]) -> str:
    commits = {
        (record["source"]["commit"], record["source"]["dirty"],
         record["source"]["diff_sha256"])
        for record in records.values()
    }
    rows = []
    # Sort on the whole identity, not just the commit: several records can
    # share a commit and differ by working-tree diff, and a partial key leaves
    # their order to set iteration -- which makes the page differ between two
    # builds of the same records.
    ordered = sorted(commits, key=lambda item: (item[0] or "", bool(item[1]),
                                                item[2] or ""))
    for commit, dirty, diff in ordered:
        if commit is None:
            rows.append(
                '<li><span class="who">no source identity</span> &mdash; recorded '
                "outside a git checkout, so the tree it ran from cannot be named"
                "</li>"
            )
            continue
        state = "with uncommitted changes" if dirty else "clean tree"
        detail = f", diff {esc(diff[:12])}" if diff else ""
        rows.append(
            f'<li><span class="who">{esc(commit[:12])}</span> '
            f"&mdash; {esc(state)}{detail}</li>"
        )
    stamps = sorted(record["environment"]["timestamp_utc"] for record in records.values())
    when = f"{esc(stamps[0])} to {esc(stamps[-1])}" if stamps else "unknown"
    return (
        '<h2>Where these numbers came from</h2>\n<div class="panel">\n'
        f'<p class="note">Recorded {when}, at:</p>\n'
        f'<ul class="reasons">\n{chr(10).join(rows)}\n</ul>\n'
        '<p class="note">A record made on a tree with uncommitted changes carries '
        "the hash of that diff, so it identifies a state even when that state was "
        "never committed. It does not make the state retrievable from this "
        "repository.</p>\n</div>"
    )


def domain_tables(plan: dict[str, Any], records: dict[str, dict[str, Any]],
                  eligible: list[str]) -> tuple[str, list[str]]:
    parts: list[str] = []
    ranked: list[str] = []
    for domain in sorted({metric["domain"] for metric in plan["metrics"]}):
        metrics = er.rankable_metrics(plan, domain)
        if not metrics:
            continue
        rows: dict[str, dict[str, float]] = {}
        absent: list[tuple[str, str]] = []
        for name in eligible:
            values = {metric["id"]: er.measured(records[name], metric["id"])
                      for metric in metrics}
            if all(value is not None for value in values.values()):
                rows[name] = values
                continue
            missing = next(metric for metric in metrics if values[metric["id"]] is None)
            entry = records[name]["measurements"].get(missing["id"])
            absent.append((
                name,
                "does not apply to this kind of target"
                if entry and entry["status"] == "org.atomix.inapplicable"
                else f"no measurement of {er.short(missing['id'])}",
            ))
        title = er.DOMAIN_TITLES.get(domain, domain)
        parts.append(f"<h3>{esc(title)}</h3>")
        if not rows and absent and all(
                reason.startswith("does not apply") for _, reason in absent):
            parts.append('<p class="note">No candidate in this experiment is a kind '
                         "of target that has such a quantity.</p>")
            continue
        if not rows:
            parts.append('<p class="note">No candidate has evidence in this '
                         "domain.</p>")
        else:
            ranked.append(domain)
            front = er.pareto(rows, metrics)
            head = "".join(
                f'<th class="num">{esc(er.short(metric["id"]))}</th>' for metric in metrics
            )
            body = []
            for name in sorted(rows, key=lambda key: [rows[key][m["id"]] for m in metrics]):
                cells = "".join(
                    f'<td class="num">{esc(number(rows[name][metric["id"]]))}</td>'
                    for metric in metrics
                )
                mark = '<td class="mark">best</td>' if name in front else "<td></td>"
                css = ' class="front"' if name in front else ""
                body.append(
                    f'<tr{css}><td><a href="#{esc(er.short(name))}">'
                    f"{esc(er.short(name))}</a></td>{cells}{mark}</tr>"
                )
            parts.append(
                "<table>\n<thead><tr><th>candidate</th>"
                f'{head}<th>pareto</th></tr></thead>\n'
                f"<tbody>\n{chr(10).join(body)}\n</tbody>\n</table>"
            )
        for name, reason in absent:
            parts.append(
                f'<p class="note"><span class="hash">{esc(er.short(name))}</span> '
                f"&mdash; {esc(reason)}</p>"
            )
    return "\n".join(parts), ranked


def identity_section(records: dict[str, dict[str, Any]], eligible: list[str],
                     links: dict[str, str]) -> str:
    rows = []
    for name in eligible:
        record = records[name]
        identity = record["identity"]
        tools = "<br>".join(
            esc(version) for version in sorted(record["environment"]["tools"].values())
        )
        rows.append(
            f'<tr id="{esc(er.short(name))}">'
            f'<td><a href="{esc(links[name])}">{esc(er.short(name))}</a></td>'
            f'<td class="hash">{esc(identity["implementation"]["artifact_sha256"] or "none")}</td>'
            f'<td class="hash">{esc(identity["target"]["build_sha256"] or "none")}</td>'
            f'<td class="hash">{esc(identity["target"]["profile_sha256"] or "none")}</td>'
            f'<td class="note">{tools}</td></tr>'
        )
    return (
        "<h2>What produced each row</h2>\n"
        '<p class="note">The payload column is the implementation that ran; the '
        "machine column is the model or host it ran on. Candidates sharing a payload "
        "hash ran the same bytes. Each name links to the record itself.</p>\n"
        "<table>\n<thead><tr><th>candidate</th><th>payload</th><th>machine</th>"
        "<th>profile</th><th>tools</th></tr></thead>\n"
        f"<tbody>\n{chr(10).join(rows)}\n</tbody>\n</table>"
    )


def methods_section(records: dict[str, dict[str, Any]], eligible: list[str]) -> str:
    seen: dict[str, str] = {}
    for name in eligible:
        for metric_id, value in records[name]["measurements"].items():
            if value["status"] == "org.atomix.measured":
                seen.setdefault(metric_id, value["method"])
    rows = [
        f'<tr><td class="hash">{esc(er.short(metric_id))}</td>'
        f"<td>{esc(method)}</td></tr>"
        for metric_id, method in sorted(seen.items())
    ]
    return (
        "<h2>How each number was measured</h2>\n<table>\n"
        "<thead><tr><th>metric</th><th>method</th></tr></thead>\n"
        f"<tbody>\n{chr(10).join(rows)}\n</tbody>\n</table>"
    )


def plan_page(plan_path: Path, output: Path) -> dict[str, Any]:
    plan, workload, cases = er.load_plan(plan_path)
    records = er.load_records(DEFAULT_RECORDS, plan)
    state = er.load_state(DEFAULT_RECORDS, plan)
    eligible, excluded = er.eligibility(plan, records, state)

    slug = plan["id"].split(".")[-1]
    plan_link, plan_digest = data_file(output, f"{slug}.json", plan)
    workload_link, workload_digest = data_file(
        output, f"workload-{workload['id'].split('.')[-1]}-r{workload['revision']}.json",
        workload,
    )
    links = {
        name: data_file(output, f"record-{er.short(name)}.json", record)[0]
        for name, record in records.items()
    }

    tables, ranked = domain_tables(plan, records, eligible)
    excluded_rows = "\n".join(
        f'<li><span class="who">{esc(er.short(name))}</span> &mdash; {esc(reason)}</li>'
        for name, reason in excluded
    ) or '<li class="note">Nothing was excluded.</li>'

    incomparable = ""
    if len(ranked) > 1:
        names = ", ".join(esc(er.short(domain)) for domain in ranked)
        incomparable = (
            '<div class="panel">\n<p class="warn">These tables are not comparable '
            f"with each other: {names}.</p>\n"
            "<p>They are different quantities measured by different means. No row "
            "under one heading may be divided by a row under another, and nothing "
            "in this repository computes a combined score from them. A model cycle "
            "count describes a design; an elapsed time describes a machine that had "
            "other work to do.</p>\n</div>"
        )

    context_metrics = [
        metric for metric in plan["metrics"]
        if metric["direction"] == "org.atomix.context-only" and any(
            er.measured(records[name], metric["id"]) is not None for name in eligible
        )
    ]
    context = ""
    if context_metrics and eligible:
        head = "".join(
            f'<th class="num">{esc(er.short(metric["id"]))}</th>'
            for metric in context_metrics
        )
        rows = []
        for name in eligible:
            cells = ""
            for metric in context_metrics:
                value = er.measured(records[name], metric["id"])
                cells += (f'<td class="num">'
                          f'{esc("--" if value is None else number(value))}</td>')
            rows.append(f"<tr><td>{esc(er.short(name))}</td>{cells}</tr>")
        context = (
            "<h2>Context, recorded but not ranked</h2>\n<table>\n"
            f"<thead><tr><th>candidate</th>{head}</tr></thead>\n"
            f"<tbody>\n{chr(10).join(rows)}\n</tbody>\n</table>"
        )

    body = f"""<header>
  <p class="note"><a href="index.html">&larr; all experiments</a></p>
  <h1>{esc(plan["summary"])}</h1>
  <p class="lede">
    <span class="claim">{esc(er.short(plan["claim"]))}</span>
    {esc(er.CLAIM_TITLES.get(plan["claim"], ""))}
  </p>
</header>

<div class="panel">
  <p><strong>Workload</strong>
    <a href="{esc(workload_link)}">{esc(workload["id"])}</a> revision
    {esc(workload["revision"])},
    {len(cases)} case(s): {esc(", ".join(case["name"] for case in cases))}.
    Oracle: <code>{esc(workload["oracle"]["kind"])}</code>.</p>
  <p><strong>Plan</strong>
    <a href="{esc(plan_link)}">{esc(plan["id"])}</a> revision
    {esc(plan["revision"])},
    <span class="hash">{esc(plan_digest[:16])}</span>.</p>
  <p class="note">{len(eligible)} of {len(ec.plan_candidates(plan))} candidates are
    eligible to be compared. A candidate is eligible only if it ran and passed the
    workload's oracle; correctness is a gate here, not a column.</p>
</div>

<h2>Results</h2>
{tables}

{incomparable}

{context}

<h2>Excluded, and why</h2>
<ul class="reasons">
{excluded_rows}
</ul>

{identity_section(records, eligible, links)}

{methods_section(records, eligible)}

{provenance(records)}

<h2>Check it yourself</h2>
<pre>{esc(commands(plan_path, plan))}</pre>
<p class="note">The run writes its own records; compare them with the ones this
page was built from, linked above. A rebuild on a different compiler produces
different bytes and the same results, and <code>make experiment-reproduce</code>
says so explicitly rather than treating it as a failure.</p>

<footer>
  <p>Built from the records in
  <a href="{esc(REPOSITORY)}/tree/main/research/experiments/records">research/experiments/records</a>.
  This page can only be as current as they are.</p>
</footer>"""
    (output / f"{slug}.html").write_text(page(plan["summary"], body))
    return {
        "slug": slug,
        "plan": plan,
        "eligible": len(eligible),
        "total": len(ec.plan_candidates(plan)),
        "excluded": len(excluded),
    }


def index_page(output: Path, summaries: list[dict[str, Any]]) -> None:
    cards = []
    for summary in summaries:
        plan = summary["plan"]
        cards.append(f"""<div class="card">
  <h3><a href="{esc(summary["slug"])}.html">{esc(plan["summary"])}</a></h3>
  <p class="lede">{esc(er.CLAIM_TITLES.get(plan["claim"], plan["claim"]))}</p>
  <p class="note">{summary["eligible"]} of {summary["total"]} candidates eligible;
    {summary["excluded"]} excluded.</p>
</div>""")
    body = f"""<header>
  <h1>atomiX experiment results</h1>
  <p class="lede">
    One workload, several implementations and machines, and the evidence for what
    each one did. These pages are generated from the record files in the
    repository, so nothing here is typed by hand.
  </p>
</header>

<div class="cards">
{chr(10).join(cards)}
</div>

<h2>How to disbelieve this</h2>
<div class="panel">
  <p>Every result on these pages is one click from the JSON record that
  produced it. A
  record names the exact artifact and machine it ran on by hash, the tools that
  built them, the commit it was taken at, and the method behind each
  measurement. If any of that is missing, the number is not evidence and the
  page says so instead of showing it.</p>
  <p>To check a result rather than read it:</p>
  <pre>git clone {esc(REPOSITORY)}
cd atomiX
make experiment-run          # runs the plan through its adapters
make experiment-report       # compares what came back</pre>
  <p>The native leg needs only Python and a C compiler. The RTL legs need
  Verilator, and the three-core comparison also needs a RISC-V toolchain. No
  board is required for anything shown here.</p>
</div>

<h2>What these results are not</h2>
<div class="panel">
  <p>There is no overall score. Results are ranked within a measurement domain
  and never across one: model cycles describe a design, host elapsed time
  describes a particular machine on a particular afternoon, and simulator wall
  time describes Verilator. No arithmetic in this repository converts between
  them, and the pages say so where the temptation would be greatest.</p>
  <p>Nothing here is physical-hardware evidence. These are simulation and
  native-execution records. A cycle count is not a frequency, and a model that
  fits in a simulator says nothing about what fits on a device.</p>
</div>

<footer>
  <p><a href="{esc(REPOSITORY)}">Repository</a> &middot;
  <a href="{esc(REPOSITORY)}/blob/main/docs/experiment-alpha.md">Run one yourself</a> &middot;
  <a href="{esc(REPOSITORY)}/blob/main/research/experiments/README.md">The plan format</a></p>
</footer>"""
    (output / "index.html").write_text(page("atomiX experiment results", body))


def build(plans: Path, output: Path) -> int:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    (output / "style.css").write_text(STYLE)
    # GitHub Pages runs Jekyll by default, which ignores files and directories
    # beginning with an underscore and rewrites some paths. This site is plain
    # HTML that is already exactly what it should be.
    (output / ".nojekyll").write_text("")

    summaries = []
    for path in sorted(plans.glob("*.json")):
        document = pc.load_document(path)
        if document.get("kind") != "experiment-plan":
            continue
        summaries.append(plan_page(path, output))
    if not summaries:
        raise pc.ContractError(f"no experiment plans found in {plans}")
    index_page(output, summaries)
    print(f"experiment pages: built {len(summaries) + 1} pages in "
          f"{output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")
    return 0


def differing(left: Path, right: Path) -> list[str]:
    comparison = filecmp.dircmp(left, right)
    result = list(comparison.left_only) + list(comparison.right_only)
    result += [name for name in comparison.common_files
               if not filecmp.cmp(left / name, right / name, shallow=False)]
    for name, sub in comparison.subdirs.items():
        result += [f"{name}/{item}" for item in differing(left / name, right / name)]
        del sub
    return sorted(result)


VOID_ELEMENTS = {"meta", "link", "br", "hr", "img", "input", "source"}


class Wellformed(HTMLParser):
    """Enough of a parser to catch a page this generator broke.

    Nothing here renders the site before it is published, so a stray unclosed
    tag would ship silently and take the rest of the page with it. This is not
    a validator; it is the specific check that every element opened is closed
    in the right order.
    """

    def __init__(self, name: str) -> None:
        super().__init__(convert_charrefs=True)
        self.name = name
        self.stack: list[str] = []
        self.problems: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_ELEMENTS:
            return
        if not self.stack:
            self.problems.append(f"</{tag}> with nothing open")
        elif self.stack[-1] != tag:
            self.problems.append(f"</{tag}> closes <{self.stack[-1]}>")
            self.stack.pop()
        else:
            self.stack.pop()

    def finish(self) -> list[str]:
        if self.stack:
            self.problems.append(f"unclosed {', '.join(f'<{tag}>' for tag in self.stack)}")
        return self.problems


def wellformed(path: Path) -> None:
    parser = Wellformed(path.name)
    parser.feed(path.read_text())
    parser.close()
    problems = parser.finish()
    if problems:
        raise pc.ContractError(f"{path.name}: {'; '.join(problems[:3])}")


def check(plans: Path, output: Path) -> int:
    """The site must be exactly what the records generate, and say what it must.

    Two separate claims. The first is that no page drifted from its records:
    the site is regenerated into a scratch directory and required to be
    byte-identical, so an edited page is a failure rather than a nicer-looking
    truth. The second is that the rules the report enforces survived being
    rendered -- an excluded candidate is still named, and a page showing more
    than one measurement domain still says they cannot be compared.
    """
    if not output.is_dir():
        raise pc.ContractError(
            f"{output} does not exist; run `make experiment-pages` first"
        )
    with tempfile.TemporaryDirectory(prefix="ax-pages-") as scratch:
        fresh = Path(scratch) / "pages"
        build(plans, fresh)
        drift = differing(output, fresh)
    if drift:
        raise pc.ContractError(
            "the published site does not match what the records generate: "
            + ", ".join(drift[:6])
        )

    for path in sorted(output.glob("*.html")):
        wellformed(path)

    checked = 0
    for path in sorted(plans.glob("*.json")):
        document = pc.load_document(path)
        if document.get("kind") != "experiment-plan":
            continue
        plan, _, _ = er.load_plan(path)
        records = er.load_records(DEFAULT_RECORDS, plan)
        eligible, excluded = er.eligibility(
            plan, records, er.load_state(DEFAULT_RECORDS, plan)
        )
        text = (output / f"{plan['id'].split('.')[-1]}.html").read_text()
        for name in eligible + [name for name, _ in excluded]:
            if er.short(name) not in text:
                raise pc.ContractError(
                    f"{plan['id']}: the page omits candidate {er.short(name)}"
                )
        ranked = [
            domain for domain in {metric["domain"] for metric in plan["metrics"]}
            if er.rankable_metrics(plan, domain) and any(
                all(er.measured(records[name], metric["id"]) is not None
                    for metric in er.rankable_metrics(plan, domain))
                for name in eligible
            )
        ]
        if len(ranked) > 1 and "not comparable with each other" not in text:
            raise pc.ContractError(
                f"{plan['id']}: the page ranks {len(ranked)} measurement domains "
                "without saying they cannot be compared"
            )
        for record in records.values():
            digest = record["identity"]["implementation"]["artifact_sha256"]
            if digest and digest not in text:
                raise pc.ContractError(
                    f"{plan['id']}: the page shows a result without the artifact "
                    "hash that produced it"
                )
        checked += 1
    print(f"experiment pages: PASS ({checked} experiment pages match their records "
          "and carry their own caveats)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("build", "render the site"),
                            ("check", "require the site to match its records")):
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--plans", type=Path, default=DEFAULT_PLANS)
        sub.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.command == "build":
            return build(args.plans, args.output)
        return check(args.plans, args.output)
    except pc.ContractError as exc:
        print(f"experiment pages: FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
