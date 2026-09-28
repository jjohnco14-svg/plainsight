"""Lock what code does now, and show what an edit changed.

REBUILT on 2026-09-27 after the original was accidentally overwritten. It
follows the original's recorded outline (every function name, argument and
docstring) and passes its tests (tests/test_lock_diff_strength.py), but it
is new code, not the original text.

snapshot: the current behaviour, written as plans a person can approve.
  - a mode machine -> its mode diagram (proven exact by the round-trip tests)
  - a function of plain numbers -> examples, recorded by RUNNING the code at
    the inputs where its answer changes (around every number in it)
  - a function that only fills in a record -> one example from a zeroed record
  Anything else is listed with the reason, never silently skipped.

diff OLD.c NEW.c: snapshot OLD, check NEW against it -> every behaviour change
as a concrete before/now example; for mode machines also a picture with added
arrows green, removed red and changed orange; and changes in side effects.
"""
import datetime
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .dictum_bridge import A
from . import nodes as N
from .harness import HarnessError, Machine, find_function, find_machine, run_cases

LOCK_VERSION = 1


@dataclass
class Snapshot:
    name: str
    lock: Optional[dict]
    diagram: Optional[str]
    reason: str = ""


def _fmt_value(v, ctype) -> str:
    if "bool" in (ctype or "").lower():
        return "yes" if v else "no"
    return str(int(v)) if float(v).is_integer() else repr(float(v))


# ------------------------------------------------------------------ what to run

class _NotComparable(Exception):
    """The function can't be run the same way any more (different inputs,
    record fields or modes), so its recorded cases can't be replayed."""


def _target(result, name) -> Machine:
    """A mode machine if the function changes a record with named modes; a
    record filler if it only fills in a record; otherwise a plain function."""
    fns = {x.name: x for x in result.items if isinstance(x, A.Action)}
    if name not in fns:
        raise HarnessError(f"the code has no function called '{name}'")
    a = fns[name]
    recs = [(n, t) for n, t in a.params if t.startswith("pointer to ") and t.endswith(" record")]
    if len(recs) == 1:
        for e in [x for x in result.items if isinstance(x, N.EnumDecl)]:
            try:
                return find_machine(result, name, [n for n, _ in e.options])
            except HarnessError:
                continue
        return _record_filler(result, a, recs[0][0], recs[0][1])
    return find_function(result, name)


def _record_filler(result, a, rec_param, words) -> Machine:
    from .harness import _inputs, _scalar, _shared, _returns
    shapes = {x.name: x for x in result.items if isinstance(x, A.Shape)}
    shape = shapes.get(words[len("pointer to "):-len(" record")])
    if shape is None or not getattr(shape, "c_name", None):
        raise HarnessError(f"can't find the record type that {a.name} fills in")
    field_words = dict(shape.fields)
    fields = [(n, q) for n, q, d in shape.c_fields if _scalar(q, d, field_words.get(n, ""))]
    return Machine(function=a.name, record_param=rec_param, record_c=shape.c_name, fields=fields,
                   inputs=_inputs(a, rec_param), param_order=[n for n, _, _ in a.c_params],
                   mode_field=None, modes={}, shared=_shared(result), returns=_returns(a),
                   skipped=[n for n, _, _ in shape.c_fields if n not in dict(fields)])


def _cases_for(result, m: Machine, name: str, extra_numbers=()) -> List[Dict[str, float]]:
    from .check import _cases, _code_numbers
    from .plan import Plan
    numbers = list(_code_numbers(result, name)) + list(extra_numbers)
    around = sorted({0, 1, -1} | {c + d for c in numbers for d in (-1, 0, 1)})
    choices = {}
    for n, ctype in m.fields + (m.shared or []) + m.inputs:
        if n == m.mode_field:
            continue
        choices[n] = [0, 1] if "bool" in ctype.lower() else around
    if m.record_param and not m.mode_field and not m.inputs:
        # a function that only fills in a record: one example, from a zeroed record
        return [{n: 0 for n in choices}]
    plan = Plan(function=name, modes=list(m.modes) if m.mode_field else [],
                kind="machine" if m.mode_field else "rules")
    return _cases(m, plan, choices)


def _machine_dict(m: Machine) -> dict:
    return {"record_param": m.record_param, "record_c": m.record_c, "fields": m.fields,
            "inputs": m.inputs, "param_order": m.param_order, "mode_field": m.mode_field,
            "modes": m.modes, "shared": m.shared or [], "returns": m.returns}


# ------------------------------------------------------------------ snapshot

def snapshot_function(result, a, source_path, stamp) -> Tuple[Optional[dict], Optional[str], str]:
    """(lock dict or None, mode-diagram plan text or None, reason if nothing could be made)."""
    from .graph import state_diagram, to_mermaid
    if a.name == "main":
        return None, None, "main runs the whole program; lock the functions it uses instead"
    try:
        m = _target(result, a.name)
    except HarnessError as e:
        return None, None, str(e)
    if not m.fields and not m.inputs and not (m.shared or []):
        return None, None, "it takes nothing and changes nothing a test can see"
    cases = _cases_for(result, m, a.name)
    ins = [{**c, m.mode_field: m.modes[c[m.mode_field]]} if m.mode_field else c for c in cases]
    try:
        outs = run_cases(source_path, m, ins)
    except HarnessError as e:
        return None, None, str(e)
    back = {v: k for k, v in m.modes.items()}
    recorded = []
    for c, o in zip(cases, outs):
        o = dict(o)
        if m.mode_field:
            o[m.mode_field] = back.get(int(o[m.mode_field]), o[m.mode_field])
        recorded.append({"in": c, "out": o})
    lock = {"plainsight_lock": LOCK_VERSION, "function": a.name, "source": os.path.basename(source_path),
            "created": stamp, "kind": "machine" if m.mode_field else
            ("record" if m.record_param else "function"),
            "target": _machine_dict(m), "facts": result.facts.get(a.name, {}), "cases": recorded}
    diagram = None
    g = state_diagram(a, result.facts.get(a.name))
    if g is not None:
        diagram = (f"%% SNAPSHOT of what {a.name} in {os.path.basename(source_path)} does NOW -- approve it\n"
                   f"%% only if this is what it SHOULD do.\n%% function: {a.name}\n" + to_mermaid(g))
    return lock, diagram, ""


def save_lock(lock, path):
    with open(path, "w") as fh:
        json.dump(lock, fh, indent=1)
        fh.write("\n")


def load_lock(path):
    with open(path) as fh:
        lock = json.load(fh)
    if not isinstance(lock, dict) or lock.get("plainsight_lock") != LOCK_VERSION \
            or "function" not in lock or "cases" not in lock or "target" not in lock:
        raise ValueError("not a Plainsight lock (or from a different version)")
    return lock


def snapshot(result, source_path, only=None):
    """[(function, lock, diagram)], [(function, reason)]"""
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    made, skipped = [], []
    for a in [x for x in result.items if isinstance(x, A.Action)]:
        if only and a.name != only:
            continue
        lock, diagram, reason = snapshot_function(result, a, source_path, stamp)
        if lock is None:
            skipped.append((a.name, reason))
        else:
            made.append((a.name, lock, diagram))
    if only and not made and not skipped:
        skipped.append((only, "the code has no function with that name"))
    return made, skipped


# ------------------------------------------------------------------ replaying a lock

def check_lock(result, source_path, lock, extra_args=None) -> List[str]:
    """Re-run every recorded case on the current code; changes as before/now examples."""
    name = lock["function"]
    try:
        m = _target(result, name)
    except HarnessError as e:
        raise _NotComparable(str(e))
    t = lock["target"]
    was = ([f for f, _ in t["fields"]], [i for i, _ in t["inputs"]], t["mode_field"], bool(t["returns"]))
    now = ([f for f, _ in m.fields], [i for i, _ in m.inputs], m.mode_field, bool(m.returns))
    if was != now:
        raise _NotComparable(f"it takes or keeps different things now (before: fields {was[0]}, inputs "
                             f"{was[1]}; now: fields {now[0]}, inputs {now[1]})")
    shared_now = {g for g, _ in (m.shared or [])}
    m.shared = [(g, c) for g, c in t["shared"] if g in shared_now]
    missing = [g for g, _ in t["shared"] if g not in shared_now]
    if missing:
        raise _NotComparable(f"shared variables it used are gone: {', '.join(missing)}")
    if m.mode_field:
        gone = sorted(set(t["modes"]) - set(m.modes))
        if gone:
            raise _NotComparable(f"these modes no longer exist: {', '.join(gone)}")
    cases = [c["in"] for c in lock["cases"]]
    ins = [{**c, m.mode_field: m.modes[c[m.mode_field]]} if m.mode_field else c for c in cases]
    outs = run_cases(source_path, m, ins, extra_args)
    back = {v: k for k, v in m.modes.items()}
    before = [c["out"] for c in lock["cases"]]
    after = []
    for o in outs:
        o = dict(o)
        if m.mode_field:
            o[m.mode_field] = back.get(int(o[m.mode_field]), f"(value {o[m.mode_field]})")
        after.append(o)
    return _compare(m, cases, before, after)


# ------------------------------------------------------------------ comparing

def _num(v) -> str:
    return str(int(v)) if isinstance(v, (int, float)) and float(v).is_integer() else str(v)


def _differs(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return a != b
    return abs(float(a) - float(b)) > 1e-9


def _compare(m: Machine, cases, outs_old, outs_new) -> List[str]:
    """Group every difference; describe each group by its plainest example."""
    ctypes = dict(m.fields + (m.shared or []) + m.inputs)
    groups: Dict[tuple, list] = {}
    for c, o, n in zip(cases, outs_old, outs_new):
        keys = sorted((k for k in o if k in n and _differs(o[k], n[k])),
                      key=lambda k: (k != m.mode_field, k != "(gives back)", k))
        if not keys:
            continue
        mode = c.get(m.mode_field) if m.mode_field else None
        key = (mode, tuple(keys)) + ((o[m.mode_field], n[m.mode_field]) if m.mode_field in keys else ())
        groups.setdefault(key, []).append((c, o, n))
    out = []
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda it: (sum(1 for k, v in it[0].items() if k != m.mode_field and v < 0),
                                   sum(abs(v) for k, v in it[0].items() if k != m.mode_field)))
        c, o, n = items[0]
        mode = c.get(m.mode_field) if m.mode_field else None
        shown = ", ".join(f"{k} = {_fmt_value(v, ctypes.get(k, ''))}" for k, v in c.items() if k != m.mode_field)
        lines = [f"In {mode}, with {shown}:" if mode else (f"With {shown}:" if shown else "Every time:")]
        for k in key[1]:
            if k == m.mode_field:
                b, a = o[k], n[k]
                was = f"it stayed in {mode}" if b == mode else f"it went to {b}"
                now = f"it stays in {mode}" if a == mode else f"it goes to {a}"
                lines.append(f"  before, {was}; now {now}.")
            elif k == "(gives back)":
                lines.append(f"  before, it gave back {_num(o[k])}; now it gives back {_num(n[k])}.")
            else:
                lines.append(f"  {k}: before, it ended at {_num(o[k])}; now it ends at {_num(n[k])}.")
        lines.append(f"  (the same change in {len(items)} of {len(cases)} recorded cases)")
        out.append("\n".join(lines))
    return out


def _effect_changes(before: dict, after: dict) -> List[str]:
    """Side effects the recorded cases can't show: printing, memory, other functions, shared data."""
    out = []
    for label, key in (("changes shared variable", "writes_globals"), ("reads shared variable", "reads_globals")):
        b, a = set(before.get(key, [])), set(after.get(key, []))
        for x in sorted(a - b):
            out.append(f"now also {label} {x}")
        for x in sorted(b - a):
            out.append(f"no longer {label} {x}")
    b = set(before.get("calls_local", []) + before.get("calls_external", []))
    a = set(after.get("calls_local", []) + after.get("calls_external", []))
    for x in sorted(a - b):
        out.append(f"now also runs {x}")
    for x in sorted(b - a):
        out.append(f"no longer runs {x}")
    if before.get("side_effects") != after.get("side_effects"):
        out.append(f"side effects: before {before.get('side_effects')}, now {after.get('side_effects')}")
    return out


def _merge(old, new, name):
    """One mode diagram: unchanged arrows grey, added green, removed red, changed orange."""
    from .graph import Graph
    g = Graph(title=f"{name}: what changed\n(grey = same, green = new, red = removed, orange = changed)",
              kind="state")
    for nid, label, shape in old.nodes + new.nodes:
        if shape == "state" and nid not in [x[0] for x in g.nodes]:
            g.nodes.append((nid, label, shape))
    notes_old, notes_new = {}, {}
    for m, t in old.notes:
        notes_old.setdefault(m, []).append(t)
    for m, t in new.notes:
        notes_new.setdefault(m, []).append(t)
    for m in sorted(set(notes_old) | set(notes_new)):
        a, b = notes_old.get(m, []), notes_new.get(m, [])
        if a == b:
            g.notes += [(m, t) for t in a]
        else:
            g.notes.append((m, "now: " + ("; ".join(b) or "(nothing)") + "\nwas: " + ("; ".join(a) or "(nothing)")))

    def by_pair(edges):
        out = {}
        for s, t, l in edges:
            out.setdefault((s, t), []).append(l)
        return out
    po, pn = by_pair(old.edges), by_pair(new.edges)
    for pair in list(po) + [p for p in pn if p not in po]:
        a, b = list(po.get(pair, [])), list(pn.get(pair, []))
        for l in [l for l in a if l in b]:
            g.edges.append((pair[0], pair[1], l))
            a.remove(l)
            b.remove(l)
        while a and b:  # same two modes, different words: one changed arrow each
            g.edges.append((pair[0], pair[1], f"now: {b.pop(0)}\nwas: {a.pop(0)}"))
        for l in b:
            g.edges.append((pair[0], pair[1], f"NEW: {l}"))
        for l in a:
            g.edges.append((pair[0], pair[1], f"REMOVED: {l}"))
    return g


def _merged_dot(g) -> str:
    from .graph import _dot
    out = ['digraph "diff" {',
           f'    graph [rankdir=TB, fontname="Helvetica", labelloc=t, fontsize=16, label="{_dot(g.title)}"];',
           '    node [fontname="Helvetica", shape=box, style="rounded,filled", fillcolor="#e8f0fe", penwidth=2];',
           '    edge [fontname="Helvetica", fontsize=10];']
    notes = {}
    for m, t in g.notes:
        notes.setdefault(m, []).append(t)
    for nid, label, _ in g.nodes:
        text = label + ("\n\n" + "\n".join(notes[nid]) if nid in notes else "")
        changed = any(t.startswith("now:") for t in notes.get(nid, []))
        extra = ', color="#e37400"' if changed else ""
        out.append(f'    "{nid}" [label="{_dot(text)}"{extra}];')
    for s, t, l in g.edges:
        if l.startswith("NEW:"):
            style = 'color="#188038", fontcolor="#188038", penwidth=2.2'
        elif l.startswith("REMOVED:"):
            style = 'color="#d93025", fontcolor="#d93025", style=dashed, penwidth=2'
        elif l.startswith("now:"):
            style = 'color="#e37400", fontcolor="#b06000", penwidth=2.2'
        else:
            style = 'color="#9e9e9e", fontcolor="#757575"'
        out.append(f'    "{s}" -> "{t}" [label="{_dot(l)}", {style}];')
    out.append("}")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ diff

@dataclass
class FunctionDiff:
    name: str
    status: str = "same"           # same | changed | not compared | added | removed
    changes: List[str] = field(default_factory=list)
    effects: List[str] = field(default_factory=list)
    note: str = ""
    cases: int = 0
    picture_dot: Optional[str] = None


def diff(old_path, new_path, only=None, extra_args=None) -> List[FunctionDiff]:
    """Run the old and the new version on the same inputs and compare everything."""
    from .graph import state_diagram
    from .lift import parse
    old, new = parse(old_path, extra_args), parse(new_path, extra_args)
    old_fns = {x.name: x for x in old.items if isinstance(x, A.Action)}
    new_fns = {x.name: x for x in new.items if isinstance(x, A.Action)}
    names = [only] if only else list(old_fns) + [n for n in new_fns if n not in old_fns]
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    out = []
    for name in names:
        d = FunctionDiff(name=name)
        out.append(d)
        if name not in old_fns and name not in new_fns:
            d.status, d.note = "not compared", "no function with that name in either version"
            continue
        if name not in old_fns:
            d.status, d.note = "added", "new in this version"
            continue
        if name not in new_fns:
            d.status, d.note = "removed", "no longer in the code"
            continue
        d.effects = _effect_changes(old.facts.get(name, {}), new.facts.get(name, {}))
        lock, _, reason = snapshot_function(old, old_fns[name], old_path, stamp)
        if lock is None:
            d.status, d.note = "not compared", reason
            if d.effects:
                d.status = "changed"
            continue
        d.cases = len(lock["cases"])
        try:
            d.changes = check_lock(new, new_path, lock, extra_args)
        except _NotComparable as e:
            d.status, d.note = "changed", f"can't be run the same way any more: {e}"
            continue
        if d.changes or d.effects:
            d.status = "changed"
        go, gn = state_diagram(old_fns[name], old.facts.get(name)), state_diagram(new_fns[name], new.facts.get(name))
        if go is not None and gn is not None and d.status == "changed":
            d.picture_dot = _merged_dot(_merge(go, gn, name))
    return out


def format_diff(diffs, old_name, new_name) -> str:
    lines = [f"Behaviour of {new_name} compared with {old_name}", ""]
    for d in diffs:
        if d.status == "same":
            lines.append(f"{d.name}: same in all {d.cases} tested cases.")
        elif d.status in ("added", "removed", "not compared"):
            lines.append(f"{d.name}: {d.status}" + (f" -- {d.note}" if d.note else ""))
        else:
            lines.append(f"{d.name}: CHANGED" + (f" -- {d.note}" if d.note else ""))
            for k, c in enumerate(d.changes, 1):
                lines.append(f"  {k}. " + c.replace("\n", "\n     "))
            for e in d.effects:
                lines.append(f"  - {e}")
        lines.append("")
    if not any(d.status in ("changed", "added", "removed") for d in diffs):
        lines.append("NO CHANGES in behaviour (every compared function did exactly the same).")
    return "\n".join(lines) + "\n"
