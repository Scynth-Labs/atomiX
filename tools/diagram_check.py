#!/usr/bin/env python3
"""Structural checks on the repository's mermaid diagrams.

Diagrams are documentation that can break silently: a mermaid block with a
typo renders as an error box on GitHub, and nothing in a normal build looks at
it.  This is the cheap guard -- it runs with no toolchain and no network, and
catches the mistakes that actually occur: an unterminated fence, a missing or
misspelled diagram type, a `class` naming a style that was never defined or a
node that does not exist, and a label containing characters mermaid parses as
syntax unless the label is quoted.

It is deliberately not a parser.  For a real render, `npx @mermaid-js/mermaid-cli`
draws every block to SVG, which is worth doing before publishing but needs a
network and several hundred megabytes; this check is what runs every time.
"""
import re, sys, pathlib

NODE_DECL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*[\[\({>]')
SUBGRAPH = re.compile(r'^\s*subgraph\s+([A-Za-z_][A-Za-z0-9_]*)', re.M)
CLASSDEF = re.compile(r'^\s*classDef\s+([A-Za-z_][A-Za-z0-9_]*)', re.M)
CLASSUSE = re.compile(r'^\s*class\s+([A-Za-z0-9_,]+)\s+([A-Za-z_][A-Za-z0-9_]*)')
EDGE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*(?:<?[-=.]{2,3}[->]?|\|)')

def blocks(path):
    lines = path.read_text().splitlines()
    inside, buf, start = False, [], 0
    for n, line in enumerate(lines, 1):
        if line.strip() == '```mermaid':
            inside, buf, start = True, [], n
        elif inside and line.strip() == '```':
            yield start, buf
            inside = False
        elif inside:
            buf.append(line)
    if inside:
        yield start, None            # unterminated

problems = 0
count = 0
for path in sorted(pathlib.Path('.').glob('**/*.md')):
    if '.git' in path.parts or 'gator-tools' in path.parts:
        continue
    for start, body in blocks(path):
        count += 1
        loc = f"{path}:{start}"
        if body is None:
            print(f"{loc}: unterminated ```mermaid block"); problems += 1; continue
        text = "\n".join(body)
        if not body or not body[0].strip().startswith(('flowchart', 'graph',
                                                       'sequenceDiagram',
                                                       'stateDiagram', 'classDiagram',
                                                       'erDiagram', 'gantt', 'pie',
                                                       'journey', 'timeline')):
            print(f"{loc}: first line is not a diagram type: {body[0][:40]!r}")
            problems += 1
        nodes = set(NODE_DECL.findall(text)) | set(SUBGRAPH.findall(text))
        styles = set(CLASSDEF.findall(text))
        for line in body:
            m = CLASSUSE.match(line)
            if not m:
                continue
            names, style = m.group(1).split(','), m.group(2)
            if style not in styles:
                print(f"{loc}: class uses undefined style {style!r}"); problems += 1
            for name in names:
                if name and name not in nodes:
                    print(f"{loc}: class names unknown node {name!r}"); problems += 1
        # Labels must be quoted when they contain characters mermaid parses.
        for line in body:
            for label in re.findall(r'\[([^\]]*)\]', line):
                if label.startswith('"'):
                    continue
                if any(c in label for c in '()/,:'):
                    print(f"{loc}: unquoted label with syntax chars: {label[:40]!r}")
                    problems += 1
print(f"checked {count} mermaid blocks; {problems} problem(s)")
sys.exit(1 if problems else 0)
