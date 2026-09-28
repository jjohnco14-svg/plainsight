"""One master chart of the whole program.

Every function's own diagram (flowchart or state diagram) becomes a group
inside one picture. Where a box runs another function, a dashed arrow goes
into that function's group. Shared variables get their own boxes, with
dotted arrows from the functions that change or read them (from the facts).

Programs too big for one readable picture get an OVERVIEW instead: one box
per function, arrows for who runs whom, and the shared variables. The
per-function diagrams hold the detail.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .dictum_bridge import A
from .graph import Graph, flowchart, state_diagram, _wrap, _mm, _dot
from .summarize import summarize_function

MAX_FUNCTIONS = 12
MAX_BOXES = 140


@dataclass
class Master:
    title: str
    overview: bool = False
    clusters: List[Tuple[str, str]] = field(default_factory=list)       # (cluster id, title)
    nodes: List[Tuple[str, str, str, str]] = field(default_factory=list)  # (id, label, shape, cluster or "")
    edges: List[Tuple[str, str, str, str]] = field(default_factory=list)  # (src, dst, label, style)


def _order(actions, result) -> List[A.Action]:
    """main first, then functions nobody calls, then the rest in call order."""
    called = {c for f in result.facts.values() for c in f.get("calls_local", [])}
    names = [a.name for a in actions]
    first = [n for n in names if n == "main"] + [n for n in names if n not in called and n != "main"]
    order, seen = [], set()

    def visit(n):
        if n in seen or n not in names:
            return
        seen.add(n)
        order.append(n)
        for c in result.facts.get(n, {}).get("calls_local", []):
            visit(c)
    for n in first + names:
        visit(n)
    by_name = {a.name: a for a in actions}
    return [by_name[n] for n in order]


def build(result, name: str = "program") -> Master:
    actions = _order([x for x in result.items if isinstance(x, A.Action)], result)
    local = {a.name for a in actions}
    graphs = {a.name: (state_diagram(a, result.facts.get(a.name)) or flowchart(a, result.facts.get(a.name)))
              for a in actions}
    total = sum(len(g.nodes) for g in graphs.values())
    shared = [x for x in result.items if isinstance(x, A.VarDecl) and not getattr(x, "is_extern", False)]
    if len(actions) > MAX_FUNCTIONS or total > MAX_BOXES:
        return _overview(actions, result, shared, name)

    m = Master(title=f"How {name} works")
    entry: Dict[str, str] = {}
    for a in actions:
        g = graphs[a.name]
        cid = f"cluster_{a.name}"
        m.clusters.append((cid, a.name))
        pre = f"{a.name}__"
        if g.kind == "state":
            var = g.title.split("how ", 1)[1].split(" changes")[0]
            start = pre + "start"
            m.nodes.append((start, f"start {a.name}\nlooks at the current {var}", "start", cid))
            notes = {}
            for mode, text in g.notes:
                notes.setdefault(mode, []).append(text)
            for nid, label, _ in g.nodes:
                text = label + ("\n" + "\n".join(notes[nid]) if nid in notes else "")
                m.nodes.append((pre + nid, text, "state", cid))
                m.edges.append((start, pre + nid, f"if now {nid}", "solid"))
            for src, dst, label in g.edges:
                m.edges.append((pre + src, pre + dst, label, "solid"))
            m.nodes.append((pre + "note", f"no arrow applies = {var} stays the same", "note", cid))
            entry[a.name] = start
        else:
            for nid, label, shape in g.nodes:
                m.nodes.append((pre + nid, label, shape, cid))
                if shape == "start":
                    entry[a.name] = pre + nid
            for src, dst, label in g.edges:
                m.edges.append((pre + src, pre + dst, label, "solid"))
    # dashed: a box runs another function of this program
    for a in actions:
        for nid, callees in graphs[a.name].calls.items():
            for c in callees:
                if c in local and c in entry:
                    m.edges.append((f"{a.name}__{nid}", entry[c], f"runs {c}", "dashed"))
    _shared_boxes(m, shared, result, entry)
    return m


def _shared_boxes(m, shared, result, entry):
    for v in shared:
        m.nodes.append((f"shared__{v.name}", f"shared: {v.name}\n({v.type})", "data", ""))
    names = {v.name for v in shared}
    for fname, f in result.facts.items():
        if fname not in entry:
            continue
        for g in f.get("writes_globals", []):
            if g in names:
                m.edges.append((entry[fname], f"shared__{g}", "changes", "dotted"))
        for g in f.get("reads_globals", []):
            if g in names and g not in f.get("writes_globals", []):
                m.edges.append((entry[fname], f"shared__{g}", "reads", "dotted"))


def _overview(actions, result, shared, name) -> Master:
    m = Master(title=f"How {name} works (overview: one box per function)", overview=True)
    entry = {}
    for a in actions:
        facts = result.facts.get(a.name, {})
        # names only: with this many functions, anything more makes the picture unreadable
        shape = "start" if a.name == "main" else "step"
        m.nodes.append((a.name, a.name, shape, ""))
        entry[a.name] = a.name
    for a in actions:
        for c in result.facts.get(a.name, {}).get("calls_local", []):
            if c in entry:
                m.edges.append((a.name, c, "runs", "dashed"))
    # shared variables as one legend box: lines across 100+ boxes are unreadable
    if shared:
        rows = []
        for v in shared:
            changed = [f for f, x in result.facts.items() if v.name in x.get("writes_globals", [])]
            read = [f for f, x in result.facts.items()
                    if v.name in x.get("reads_globals", []) and f not in changed]
            parts = []
            if changed:
                parts.append("changed by " + ", ".join(changed))
            if read:
                parts.append("read by " + ", ".join(read))
            rows.append(f"{v.name}: " + ("; ".join(parts) if parts else "not used by any function"))
        m.nodes.append(("shared_legend", "Shared variables\n" + "\n".join(rows), "note", ""))
    return m


# ------------------------------------------------------------------ writers

def to_dot(m: Master) -> str:
    out = [f'digraph "{_dot(m.title)}" {{',
           f'    graph [rankdir={"LR" if m.overview else "TB"}, compound=true, fontname="Helvetica", labelloc=t, fontsize=18, '
           f'label="{_dot(m.title)}", nodesep=0.35, ranksep=0.4];',
           '    node [fontname="Helvetica", fontsize=11, style=filled, fillcolor="#ffffff", color="#444444"];',
           '    edge [fontname="Helvetica", fontsize=10, color="#555555"];']
    shapes = {"start": 'shape=oval, fillcolor="#e8f0fe"', "end": 'shape=oval, fillcolor="#fde8e8"',
              "step": 'shape=box, style="rounded,filled"', "decision": 'shape=diamond, fillcolor="#fff6d6"',
              "loop": 'shape=hexagon, fillcolor="#e6f4ea"',
              "state": 'shape=box, style="rounded,filled", fillcolor="#e8f0fe", penwidth=2',
              "data": 'shape=cylinder, fillcolor="#f3e8fd"', "note": 'shape=note, fillcolor="#fafafa", fontsize=10'}
    by_cluster: Dict[str, list] = {}
    for nid, label, shape, cid in m.nodes:
        by_cluster.setdefault(cid, []).append(f'"{nid}" [label="{_dot(label)}", {shapes[shape]}];')
    titles = dict(m.clusters)
    for cid, _ in m.clusters:
        out.append(f'    subgraph "{cid}" {{')
        out.append(f'        label="{_dot(titles[cid])}"; style="rounded"; color="#8a8a8a"; fontsize=14;')
        out.extend("        " + x for x in by_cluster.get(cid, []))
        out.append("    }")
    out.extend("    " + x for x in by_cluster.get("", []))
    styles = {"solid": "", "dashed": ', style=dashed, color="#1a73e8", fontcolor="#1a73e8", penwidth=1.6',
              "dotted": ', style=dotted, color="#9334e6", fontcolor="#9334e6"'}
    for src, dst, label, style in m.edges:
        if style == "dotted":
            # pin "changes"/"reads" to the shared-variable end, so it can't look
            # like it belongs to a box the arrow merely passes
            lab = f'headlabel="{_dot(label)}", labeldistance=2.2, labelangle=25'
            extra = ", constraint=false"
        else:
            lab = f'label="{_dot(label)}"' if label else 'label=""'
            extra = ""
        out.append(f'    "{src}" -> "{dst}" [{lab}{styles[style]}{extra}];')
    out.append("}")
    return "\n".join(out) + "\n"


def to_mermaid(m: Master) -> str:
    out = [f"%% {m.title}", "flowchart TD"]
    shapes = {"start": ('(["', '"])'), "end": ('(["', '"])'), "step": ('["', '"]'),
              "decision": ('{"', '"}'), "loop": ('{{"', '"}}'), "state": ('("', '")'),
              "data": ('[("', '")]'), "note": ('>"', '"]')}
    by_cluster: Dict[str, list] = {}
    for nid, label, shape, cid in m.nodes:
        o, c = shapes[shape]
        by_cluster.setdefault(cid, []).append(f"{nid}{o}{_mm(label)}{c}")
    titles = dict(m.clusters)
    for cid, _ in m.clusters:
        out.append(f'    subgraph {cid} ["{_mm(titles[cid])}"]')
        out.extend("        " + x for x in by_cluster.get(cid, []))
        out.append("    end")
    out.extend("    " + x for x in by_cluster.get("", []))
    arrows = {"solid": "-->", "dashed": "-.->", "dotted": "-.->"}
    for src, dst, label, style in m.edges:
        a = arrows[style]
        out.append(f'    {src} {a}|"{_mm(label)}"| {dst}' if label else f"    {src} {a} {dst}")
    return "\n".join(out) + "\n"
