"""Draw how a program works: one diagram per function, built from the exact tree.

- A function that moves a value between named modes (a switch on an enum)
  gets a STATE DIAGRAM: one box per mode, one arrow per change of mode.
- Every other function gets a FLOWCHART: boxes for actions, diamonds for
  decisions, arrows back for loops. This works for any code -- no pattern
  rules needed -- so nothing is ever left out.

Output formats (same graph, two spellings):
- Mermaid text: readable by people and AI; GitHub shows it as a picture.
- Graphviz DOT: turned into an image (SVG/PNG) by the `dot` program.
"""
import textwrap
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .dictum_bridge import A
from . import nodes as N
from .summarize import SummaryWords, _shorten_fields, _input_words, _simple, NoMatch, _all_nodes

WRAP = 32


@dataclass
class Graph:
    title: str
    kind: str = "flow"  # "flow" or "state"
    nodes: List[Tuple[str, str, str]] = field(default_factory=list)  # (id, label, shape)
    edges: List[Tuple[str, str, str]] = field(default_factory=list)  # (src, dst, label)
    notes: List[Tuple[str, str]] = field(default_factory=list)       # (state id, text)
    calls: dict = field(default_factory=dict)                         # node id -> [function names]
    edge_styles: dict = field(default_factory=dict)                   # edge index -> added|removed|changed
    _n: int = 0

    def add(self, label: str, shape: str) -> str:
        self._n += 1
        nid = f"n{self._n}"
        self.nodes.append((nid, label, shape))
        return nid

    def link(self, src: str, dst: str, label: str = ""):
        self.edges.append((src, dst, label))


def _wrap(text: str) -> List[str]:
    out = []
    for part in text.split("\n"):
        out.extend(textwrap.wrap(part, WRAP) or [""])
    return out


# ------------------------------------------------------------------ flowchart

class _Flow:
    """Turns statements into boxes and arrows.
    Each build step returns the 'open ends': (node, arrow label) pairs that
    continue to whatever comes next."""

    def __init__(self, g: Graph, words: SummaryWords):
        self.g = g
        self.w = words
        self.loops: List[dict] = []  # innermost last: {"continue": node, "breaks": [...]}
        self.switch_breaks: List[list] = []

    def note_calls(self, nid, nodes):
        """Remember which functions this box runs (for the master chart)."""
        from .summarize import _all_nodes
        names = []
        for x in _all_nodes([n for n in nodes if n is not None]):
            if isinstance(x, A.FuncCall) and x.name not in names:
                names.append(x.name)
        if names:
            self.g.calls.setdefault(nid, [])
            self.g.calls[nid] += [n for n in names if n not in self.g.calls[nid]]

    def phrase(self, s) -> str:
        w = self.w
        if isinstance(s, A.VarDecl):
            if s.value is None:
                return f"make {s.name}"
            return f"{s.name} starts at {w.value(s.value)}"
        if isinstance(s, N.ExprStmt):
            if isinstance(s.expr, N.MacroCall):
                return f"use {w.expr(s.expr)}"
            return f"work out {w.expr(s.expr)}"
        if isinstance(s, N.Unsupported):
            return f"[not yet supported: {s.what}, line {s.line}]"
        try:
            return w.action(s)
        except NoMatch:
            return w.stmt(s)[0]

    def connect(self, ends, dst):
        for src, label in ends:
            self.g.link(src, dst, label)

    def block(self, stmts, ends):
        """Lay out a list of statements after `ends`; returns the new open ends."""
        batch, batch_nodes = [], []

        def flush(ends):
            if not batch:
                return ends
            box = self.g.add("\n".join(batch), "step")
            self.note_calls(box, batch_nodes)
            self.connect(ends, box)
            batch.clear()
            batch_nodes.clear()
            return [(box, "")]

        for s in stmts:
            if _simple(s) or isinstance(s, (A.VarDecl, N.ExprStmt, N.Unsupported)):
                batch.append(self.phrase(s))
                batch_nodes.append(s)
                continue
            ends = flush(ends)
            ends = self.stmt(s, ends)
        return flush(ends)

    def stmt(self, s, ends):
        g, w = self.g, self.w
        if isinstance(s, A.Return):
            end = g.add("gives back " + w.value(s.value) if s.value is not None else "stops here", "end")
            self.note_calls(end, [s.value])
            self.connect(ends, end)
            return []
        if isinstance(s, A.If):
            d = g.add(w.cond(s.cond) + "?", "decision")
            self.note_calls(d, [s.cond])
            self.connect(ends, d)
            out = self.block(s.then_body, [(d, "yes")])
            if s.else_body:
                out += self.block(s.else_body, [(d, "no")])
            else:
                out.append((d, "no"))
            return out
        if isinstance(s, (A.While, A.Repeat, N.ForLoop)):
            return self.loop(s, ends)
        if isinstance(s, N.DoWhile):
            start = g.add("repeat:", "loop")
            self.connect(ends, start)
            ctx = {"continue": None, "breaks": []}
            self.loops.append(ctx)
            body_ends = self.block(s.body, [(start, "")])
            self.loops.pop()
            d = g.add(w.cond(s.cond) + "?", "decision")
            self.connect(body_ends, d)
            g.link(d, start, "yes, again")
            return [(d, "no")] + ctx["breaks"]
        if isinstance(s, A.Break):
            if getattr(s, "target", "loop") == "switch" and self.switch_breaks:
                self.switch_breaks[-1].extend(ends)
            elif self.loops:
                self.loops[-1]["breaks"].extend((src, lbl or "stop loop") for src, lbl in ends)
            return []
        if isinstance(s, N.Continue):
            if self.loops and self.loops[-1]["continue"]:
                self.connect([(src, lbl or "next round") for src, lbl in ends], self.loops[-1]["continue"])
            return []
        if isinstance(s, N.Switch):
            return self.switch(s, ends)
        box = g.add(self.phrase(s), "step")
        self.note_calls(box, [s])
        self.connect(ends, box)
        return [(box, "")]

    def loop(self, s, ends):
        g, w = self.g, self.w
        step_node = None
        if isinstance(s, A.Repeat):
            head = g.add(f"for each {s.counter} from 0 to {w.expr(s.count)} minus 1", "loop")
            self.connect(ends, head)
            enter, done, back = [(head, "next")], [(head, "finished")], head
        elif isinstance(s, A.While):
            head = g.add(w.cond(s.cond) + "?", "decision")
            self.note_calls(head, [s.cond])
            self.connect(ends, head)
            enter, done, back = [(head, "yes")], [(head, "no")], head
        else:  # ForLoop
            if s.init:
                ends = self.block(s.init, ends)
            head = g.add((w.cond(s.cond) + "?") if s.cond is not None else "repeat forever", "decision")
            self.connect(ends, head)
            enter, done = [(head, "yes")], ([(head, "no")] if s.cond is not None else [])
            if s.step:
                step_node = g.add("\n".join(self.phrase(x) for x in s.step), "step")
                g.link(step_node, head, "")
            back = step_node or head
        ctx = {"continue": back, "breaks": []}
        self.loops.append(ctx)
        body_ends = self.block(s.body, enter)
        self.loops.pop()
        self.connect([(src, lbl) for src, lbl in body_ends], back)
        return done + ctx["breaks"]

    def switch(self, s, ends):
        g, w = self.g, self.w
        d = g.add(f"which value is {w.expr(s.value)}?", "decision")
        self.connect(ends, d)
        out, carry = [], []
        self.switch_breaks.append(out)
        for grp in s.groups:
            labels = [w.expr(x) for x in grp.labels if x is not None]
            label = " or ".join(labels) if labels else ""
            if any(x is None for x in grp.labels):
                label = (label + " or " if label else "") + "anything else"
            entry = [(d, label)] + carry
            if grp.body:
                grp_ends = self.block(grp.body, entry)
            else:
                grp_ends = entry
            if grp.falls_through:
                carry = [(src, lbl or "falls through") for src, lbl in grp_ends]
            else:
                carry = []
                out.extend(grp_ends)
        self.switch_breaks.pop()
        out.extend(carry)
        if not s.has_default:
            out.append((d, "anything else"))
        return out


def flowchart(a: A.Action, facts: Optional[dict] = None) -> Graph:
    record = _record_param(a)
    a = _shorten_fields(a, record)
    w = SummaryWords(record_param=record)
    takes = ", ".join(f"{n} ({_input_words(a, n, t, facts)})" for n, t in a.params) or "nothing"
    g = Graph(title=f"{a.name}", kind="flow")
    start = g.add(f"start {a.name}\ntakes: {takes}", "start")
    ends = _Flow(g, w).block(a.body, [(start, "")])
    if ends:
        end = g.add("end", "end")
        for src, label in ends:
            g.link(src, end, label)
    return g


# ------------------------------------------------------------------ state diagram

def state_diagram(a: A.Action, facts: Optional[dict] = None) -> Optional[Graph]:
    """Only when the whole function is one switch on a named-mode value whose
    cases change that value -- the shape of a machine with modes."""
    record = _record_param(a)
    a = _shorten_fields(a, record)
    if len(a.body) != 1 or not isinstance(a.body[0], N.Switch):
        return None
    sw = a.body[0]
    w = SummaryWords(record_param=record)
    if not str(getattr(sw.value, "ctype", "")).endswith("choice"):
        return None
    var = w.expr(sw.value)
    if any(g.falls_through or any(l is not None and not isinstance(l, A.Identifier) for l in g.labels)
           for g in sw.groups):
        return None
    g = Graph(title=f"{a.name}: how {var} changes\n(no arrow applies = {var} stays the same)", kind="state")
    if any(isinstance(x, A.FuncCall) for x in _all_nodes([sw.value])):
        return None  # the value it checks is worked out by a call: keep the flowchart

    def is_move(s):
        return isinstance(s, (N.Store, A.Assignment)) and \
            (s.target if isinstance(s, A.Assignment) else w.expr(s.target)) == var

    def leaf(stmts):
        acts, moves = [], []
        for s in stmts:
            if isinstance(s, A.Break) and getattr(s, "target", "") == "switch":
                continue
            if not _simple(s):
                raise NoMatch
            for x in _all_nodes([s]):  # calls made in this mode, for the master chart
                if isinstance(x, A.FuncCall):
                    for mode in current_modes:
                        g.calls.setdefault(mode, [])
                        if x.name not in g.calls[mode]:
                            g.calls[mode].append(x.name)
            if is_move(s):
                v = s.value
                if isinstance(v, A.Identifier):
                    moves.append((v.name, None))
                elif isinstance(v, N.Ternary) and isinstance(v.then, A.Identifier) \
                        and isinstance(v.other, A.Identifier):
                    moves.append((v.then.name, w.cond(v.cond)))
                    moves.append((v.other.name, w.negate(v.cond)))  # the real condition, not "otherwise"
                else:
                    raise NoMatch
            else:
                acts.append(w.action(s))
        return acts, moves

    modes = []
    current_modes: List[str] = []
    try:
        for grp in sw.groups:
            names = [l.name for l in grp.labels if l is not None]
            current_modes[:] = names
            if not names:
                raise NoMatch  # a default case has no single box to draw
            for m in names:
                if m not in modes:
                    modes.append(m)
            body = list(grp.body)
            chain = body[-1] if body and isinstance(body[-1], A.If) else None
            first = body[:-1] if chain is not None else body
            if chain is None:
                acts, moves = leaf(first)
                for m in names:
                    if not acts and not moves:
                        g.notes.append((m, "never leaves this mode"))
                    if acts:
                        g.notes.append((m, "every time: " + ", ".join(acts)))
                    for to, extra in moves:
                        g.edges.append((m, to, extra or "always"))
                continue
            pre_acts, pre_moves = leaf(first)
            if pre_moves:
                raise NoMatch
            for m in names:
                if pre_acts:
                    g.notes.append((m, "every time first: " + ", ".join(pre_acts)))
            node, conds_so_far = chain, []
            while True:
                if any(isinstance(x, A.If) for x in node.then_body):
                    raise NoMatch
                cond = w.cond(node.cond)
                when = ("otherwise, if " if conds_so_far else "if ") + cond
                acts, moves = leaf(node.then_body)
                for m in names:
                    for to, extra in moves:
                        # in the order it happens: condition, changes, then any final check
                        label = when + (": " + ", ".join(acts) if acts else "")
                        if extra:
                            label += (", then " if acts else ", ") + "if " + extra
                        g.edges.append((m, to, label))
                    if acts and not moves:
                        g.notes.append((m, when + ": " + ", ".join(acts)))
                conds_so_far.append(cond)
                if len(node.else_body) == 1 and isinstance(node.else_body[0], A.If):
                    node = node.else_body[0]
                    continue
                if node.else_body:
                    acts, moves = leaf(node.else_body)
                    for m in names:
                        for to, extra in moves:
                            g.edges.append((m, to, "otherwise" + (f", then if {extra}" if extra else "")))
                break
    except NoMatch:
        return None
    for m in modes:
        g.nodes.append((m, m, "state"))
    for to in {e[1] for e in g.edges}:
        if to not in modes:
            g.nodes.append((to, to, "state"))
    return g


def _record_param(a) -> Optional[str]:
    recs = [n for n, t in a.params if t.startswith("pointer to ") and t.endswith(" record")]
    return recs[0] if len(recs) == 1 else None


def graphs_for(result) -> List[Graph]:
    out = []
    for item in result.items:
        if isinstance(item, A.Action):
            facts = result.facts.get(item.name)
            out.append(state_diagram(item, facts) or flowchart(item, facts))
    return out


# ------------------------------------------------------------------ Mermaid

def _mm(text: str) -> str:
    lines = _wrap(text)
    safe = []
    for l in lines:
        l = (l.replace("&", "&amp;").replace('"', "#quot;").replace("<", "#lt;")
             .replace(">", "#gt;").replace(";", ",").replace("|", "/"))
        safe.append(l)
    return "<br/>".join(safe)


def to_mermaid(g: Graph) -> str:
    out = [f"%% {line}" for line in g.title.split("\n")]  # every title line must be a comment
    if g.kind == "state":
        out.append("stateDiagram-v2")
        for nid, label, _ in g.nodes:
            out.append(f"    {nid}")
        for src, dst, label in g.edges:
            out.append(f"    {src} --> {dst} : {_mm(label)}")
        for m, text in g.notes:
            out.append(f"    note right of {m} : {_mm(text)}")
        return "\n".join(out) + "\n"
    out.append("flowchart TD")
    shapes = {"start": ('(["', '"])'), "end": ('(["', '"])'), "step": ('["', '"]'),
              "decision": ('{"', '"}'), "loop": ('{{"', '"}}')}
    for nid, label, shape in g.nodes:
        o, c = shapes[shape]
        out.append(f"    {nid}{o}{_mm(label)}{c}")
    for src, dst, label in g.edges:
        out.append(f'    {src} -->|"{_mm(label)}"| {dst}' if label else f"    {src} --> {dst}")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ Graphviz DOT

def _dot(text: str) -> str:
    return "\\n".join(l.replace("\\", "\\\\").replace('"', '\\"') for l in _wrap(text))


def to_dot(g: Graph) -> str:
    out = [f'digraph "{_dot(g.title)}" {{',
           '    graph [rankdir=TB, fontname="Helvetica", labelloc=t, fontsize=16, '
           f'label="{_dot(g.title)}", nodesep=0.4, ranksep=0.45];',
           '    node [fontname="Helvetica", fontsize=12, style=filled, fillcolor="#ffffff", color="#444444"];',
           '    edge [fontname="Helvetica", fontsize=11, color="#555555"];']
    shapes = {"start": 'shape=oval, fillcolor="#e8f0fe"', "end": 'shape=oval, fillcolor="#fde8e8"',
              "step": 'shape=box, style="rounded,filled"', "decision": 'shape=diamond, fillcolor="#fff6d6"',
              "loop": 'shape=hexagon, fillcolor="#e6f4ea"', "state": 'shape=box, style="rounded,filled", '
              'fillcolor="#e8f0fe", fontsize=15, penwidth=2'}
    notes = {}
    for m, text in g.notes:
        notes.setdefault(m, []).append(text)
    for nid, label, shape in g.nodes:
        text = label
        if nid in notes:
            text += "\n\n" + "\n".join(notes[nid])
        out.append(f'    "{nid}" [label="{_dot(text)}", {shapes[shape]}];')
    looks = {"added": ', color="#1e8e3e", fontcolor="#1e8e3e", penwidth=2.4',
             "removed": ', color="#d93025", fontcolor="#d93025", style=dashed, penwidth=2',
             "changed": ', color="#e37400", fontcolor="#b06000", penwidth=2.4'}
    for k, (src, dst, label) in enumerate(g.edges):
        look = looks.get(g.edge_styles.get(k), "")
        if label:
            out.append(f'    "{src}" -> "{dst}" [label="{_dot(label)}"{look}];')
        else:
            out.append(f'    "{src}" -> "{dst}" [label=""{look}];')
    out.append("}")
    return "\n".join(out) + "\n"
