"""Plan files, for an AI agent working in a sandbox (no API calls, no keys).

The agent writes the plan itself; Plainsight tells it which names it may
use, checks the plan fits the code, warns about impossible modes, draws the
plan as a picture for a person, and records the person's approval.
"""
import datetime
import hashlib
import json
import os
import re
from typing import Dict, List, Optional, Tuple

from .plan import PlanError, read_plan


def names_from_code(result) -> Tuple[str, Dict[str, float]]:
    """The exact names a plan for this code may use, as text for the model,
    plus the named constants' values (for reading the plan)."""
    from .dictum_bridge import A
    from . import nodes as N
    lines, consts = [], {}
    shapes = {x.name: x for x in result.items if isinstance(x, A.Shape)}
    for x in result.items:
        if isinstance(x, N.EnumDecl):
            lines.append(f"- modes ({x.name}): " + ", ".join(n for n, _ in x.options))
        elif isinstance(x, A.Shape):
            lines.append(f"- record {x.name} fields: " + ", ".join(f"{n} ({t})" for n, t in x.fields))
        elif isinstance(x, A.VarDecl) and getattr(x, "is_global", False):
            lines.append(f"- shared variable: {x.name} ({x.type})")
        elif isinstance(x, A.Action):
            params = ", ".join(f"{n} ({t})" for n, t in x.params)
            gives = "" if x.ret_type == "nothing" else f", gives back a {x.ret_type}"
            lines.append(f"- function {x.name}({params}){gives}")
            for k, v in getattr(x, "macros", {}).items():
                if v and re.fullmatch(r"-?\d+(\.\d+)?", v.rstrip("uUlLfF")) and not k.startswith("_"):
                    consts[k] = float(v.rstrip("uUlLfF")) if "." in v else int(v.rstrip("uUlL"))
    user_consts = {k: v for k, v in consts.items() if not k.isupper() or len(k) > 1}
    shown = [k for k in user_consts if k in _constants_used(result)]
    if shown:
        lines.append("- named constants: " + ", ".join(f"{k} = {user_consts[k]}" for k in shown))
    return "\n".join(lines), consts


def _constants_used(result) -> set:
    from .dictum_bridge import A
    from .summarize import _all_nodes
    out = set()
    for a in result.items:
        if isinstance(a, A.Action):
            for x in _all_nodes(a.body):
                if isinstance(x, A.Identifier) and getattr(x, "named_constant", False):
                    out.add(x.name)
    return out


def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:\w+)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def _problem(text: str, result, consts) -> Optional[str]:
    try:
        plan = read_plan(text, consts)
    except PlanError as e:
        return str(e)
    if result is None:
        return None
    if plan.examples:
        problem = _examples_problem(plan, result)
        if problem:
            return problem
    if plan.kind == "examples":
        return None
    from .harness import find_machine, find_function, HarnessError
    try:
        m = find_function(result, plan.function) if plan.kind == "rules" \
            else find_machine(result, plan.function, plan.modes)
    except HarnessError as e:
        return str(e)
    known = {f for f, _ in m.fields} | {g for g, _ in (m.shared or [])} | {i for i, _ in m.inputs}
    used = _names_used(plan)
    unknown = sorted(n for n in used if n not in known)
    if unknown:
        return ("it uses names the code doesn't have: " + ", ".join(unknown)
                + ". Allowed: " + ", ".join(sorted(known)))
    if m.mode_field:
        extra = [x for x in plan.modes if x not in m.modes]
        if extra:
            return "it uses modes the code doesn't have: " + ", ".join(extra)
    return None


def _examples_problem(plan, result) -> Optional[str]:
    """Examples must fit the code: known inputs, and values the compiler accepts."""
    import shutil
    import subprocess
    import tempfile
    from .examples import c_program, explain_compile_error
    from .harness import find_machine, HarnessError
    try:
        fn = plan.function or find_machine(result, plan.function, plan.modes).function
        program = c_program(result, fn, plan.examples, os.path.abspath(result.path))
        with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as fh:
            fh.write(program)
            name = fh.name
    except (PlanError, HarnessError) as e:
        return str(e)
    gcc = shutil.which("gcc") or shutil.which("clang")
    if gcc:
        from .lang import compiler
        r = subprocess.run([*compiler(result.path), "-fsyntax-only", "-w", name], capture_output=True, text=True)
        os.unlink(name)
        if r.returncode != 0:
            return explain_compile_error(program, r.stderr, plan.examples)
    return None


def _names_used(plan) -> set:
    out = set()

    def walk(n):
        if isinstance(n, tuple):
            if n and n[0] == "var":
                out.add(n[1])
            elif n and len(n) == 3 and isinstance(n[0], str) and n[1] in ("=", "+=", "-="):
                out.add(n[0])
            for x in n[1:]:
                walk(x)
        elif isinstance(n, list):
            for x in n:
                walk(x)
    for rules in plan.rules.values():
        for r in rules:
            walk(r.cond)
            walk(r.changes)
            for c, _ in r.branches:
                walk(c)
    for chs in plan.first.values():
        walk(chs)
    return out




def validate(text: str, result=None) -> Tuple[Optional[object], List[str], List[str]]:
    """(plan or None, problems that must be fixed, warnings to show a person)."""
    from .plancheck import plan_warnings
    consts = names_from_code(result)[1] if result is not None else {}
    problem = _problem(text, result, consts)
    if problem:
        return None, [problem], []
    plan = read_plan(text, consts)
    return plan, [], plan_warnings(plan)


# ------------------------------------------------------------------ approval

def _digest(data) -> str:
    """Fingerprint of a plan (text: line endings and outer blank space don't count) or a lock (bytes)."""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return hashlib.sha256(data).hexdigest()
    return hashlib.sha256(data.replace("\r\n", "\n").strip().encode()).hexdigest()


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def stamp_path(plan_path: str) -> str:
    return plan_path + ".approved"


def approve(plan_path: str, by: str) -> dict:
    """Record that a person looked at this exact plan and said yes."""
    stamp = {"plan": os.path.basename(plan_path), "sha256": _digest(_read(plan_path)), "approved_by": by,
             "approved_at": datetime.datetime.now().isoformat(timespec="seconds")}
    with open(stamp_path(plan_path), "w") as fh:
        json.dump(stamp, fh, indent=2)
    return stamp


def approval(plan_path: str) -> dict:
    """{'state': 'approved' | 'not approved' | 'changed since approval', ...}"""
    sp = stamp_path(plan_path)
    if not os.path.isfile(sp):
        return {"state": "not approved"}
    try:
        stamp = json.load(open(sp))
    except (ValueError, OSError):
        return {"state": "not approved"}
    if stamp.get("sha256") != _digest(_read(plan_path)):
        return {"state": "changed since approval", **stamp}
    return {"state": "approved", **stamp}


# ------------------------------------------------------------------ the picture to approve

def plan_to_dot(plan) -> str:
    """The plan drawn for approval: a mode diagram, or a chain of decisions."""
    from .graph import Graph, to_dot
    if plan.kind == "machine":
        g = Graph(title=f"PLAN for {plan.function or 'the machine'}\n(no arrow applies = stays the same)",
                  kind="state")
        for mode in plan.modes:
            g.nodes.append((mode, mode, "state"))
            if mode in plan.first:
                g.notes.append((mode, "every time first: " + plan.first_text.get(mode, "")))
            if mode in plan.stuck:
                g.notes.append((mode, "never leaves this mode"))
        for mode, rules in plan.rules.items():
            for r in rules:
                for then_cond, target in r.branches:
                    label = re.sub(r"^otherwise,\s*", "", r.text) if len(r.branches) == 1 else r.text
                    g.edges.append((mode, target, label))
        return to_dot(g)
    g = Graph(title=f"PLAN for {plan.function}", kind="flow")
    prev = g.add(f"start {plan.function}", "start")
    if None in plan.first:
        box = g.add(plan.first_text.get(None, ""), "step")
        g.link(prev, box)
        prev = box
    label = ""
    for r in plan.rules.get(None, []):
        text = r.text.split(":", 1)
        if r.cond is None:
            end = g.add(text[1].strip(), "end")
            g.link(prev, end, label)
            prev = None
            break
        cond = re.sub(r"^(otherwise,\s*)?if\s+", "", text[0]).strip()
        d = g.add(cond + "?", "decision")
        g.link(prev, d, label)
        end = g.add(text[1].strip(), "end")
        g.link(d, end, "yes")
        prev, label = d, "no"
    if prev is not None:
        end = g.add("(the plan says nothing here)", "end")
        g.link(prev, end, label)
    return to_dot(g)

