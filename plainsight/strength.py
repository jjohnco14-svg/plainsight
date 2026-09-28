"""How strong is a plan? Plant small bugs in the code and see which it misses.

Planted bugs (in the function the plan is for):
  - a comparison changed:  <  <=  >  >=  ==  !=  to its neighbour
  - a number, or a named constant's value, moved by one
  - && and || swapped, + and - swapped, += and -= swapped
  - a ! removed
  - a line of changes skipped
Each planted version is checked against the plan exactly like real code.
A bug the plan doesn't notice "survived": the report says where, and what
to add to the plan to catch it. (Some survivors make no real difference --
e.g. `x > 2` and `x >= 3` on whole numbers -- so 100% isn't always possible.)
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional

_NEIGHBOUR = {"<": ["<="], "<=": ["<"], ">": [">="], ">=": [">"], "==": ["!="], "!=": ["=="],
              "&&": ["||"], "||": ["&&"], "+": ["-"], "-": ["+"]}
_COMPOUND = {"+=": "-=", "-=": "+="}


@dataclass
class Mutant:
    line: int
    what: str            # plain words
    start: int           # byte offsets in the source
    end: int
    replacement: str
    suggestion: str = ""


@dataclass
class Strength:
    function: str
    total: int = 0
    caught: int = 0
    survivors: List[Mutant] = field(default_factory=list)
    unusable: int = 0    # planted versions that didn't compile (not counted)

    @property
    def score(self) -> float:
        return 100.0 * self.caught / self.total if self.total else 0.0


def _tree(path, extra_args):
    from .lift_json import find_clang, _fill_locations, _parse_macros
    from .lang import read_flags
    clang = find_clang()
    flags = read_flags(path)
    r = subprocess.run([clang, "-fsyntax-only", *flags, *(extra_args or []),
                        "-Xclang", "-ast-dump=json", path], capture_output=True, text=True)
    tree = json.loads(r.stdout)
    _fill_locations(tree)
    macros = _parse_macros(subprocess.run([clang, "-E", "-dM", *flags, *(extra_args or []), path],
                                          capture_output=True, text=True).stdout)
    return tree, macros


def _plain_loc(l, path):
    """A location written directly in the user's file (not produced by a macro)."""
    if not l or "expansionLoc" in l or "spellingLoc" in l or l.get("file") != path:
        return None
    return l


def plant(source_path: str, function: str, extra_args=None) -> List[Mutant]:
    tree, macros = _tree(source_path, extra_args)
    src = open(source_path, "rb").read()
    text = src.decode("latin-1")
    fn = next((d for d in tree.get("inner", []) if d.get("kind") == "FunctionDecl" and d.get("name") == function
               and any(k.get("kind") == "CompoundStmt" for k in d.get("inner", []))), None)
    if fn is None:
        return []
    out: List[Mutant] = []
    used_macros = set()

    def line_of(off):
        return text.count("\n", 0, off) + 1

    def snippet(off):
        a = text.rfind("\n", 0, off) + 1
        b = text.find("\n", off)
        return text[a:b if b >= 0 else None].strip()

    def walk(n, parent_kind=None):
        k = n.get("kind")
        kids = n.get("inner", [])
        r = n.get("range", {})
        b, e = _plain_loc(r.get("begin"), source_path), _plain_loc(r.get("end"), source_path)
        if k == "BinaryOperator" and n.get("opcode") in _NEIGHBOUR and len(kids) == 2:
            le = _plain_loc(kids[0].get("range", {}).get("end"), source_path)
            rb = _plain_loc(kids[1].get("range", {}).get("begin"), source_path)
            if le and rb:
                gap_start = le["offset"] + le.get("tokLen", 0)
                gap = text[gap_start:rb["offset"]]
                op = n["opcode"]
                pos = gap.find(op)
                if pos >= 0 and gap.strip() == op:
                    at = gap_start + pos
                    lb = _plain_loc(kids[0].get("range", {}).get("begin"), source_path)
                    re_ = _plain_loc(kids[1].get("range", {}).get("end"), source_path)
                    left = text[lb["offset"]:le["offset"] + le.get("tokLen", 0)] if lb else _macro_name(kids[0], text)
                    right = text[rb["offset"]:re_["offset"] + re_.get("tokLen", 0)] if re_ else _macro_name(kids[1], text)
                    for rep in _NEIGHBOUR[op]:
                        out.append(Mutant(line_of(at), f"`{left} {op} {right}` changed to `{left} {rep} {right}` "
                                          f"(line: {snippet(at)})", at, at + len(op), rep,
                                          _suggest_op(op, left, right)))
        if k == "CompoundAssignOperator" and n.get("opcode") in _COMPOUND and len(kids) == 2:
            le = _plain_loc(kids[0].get("range", {}).get("end"), source_path)
            rb = _plain_loc(kids[1].get("range", {}).get("begin"), source_path)
            if le and rb:
                gap_start = le["offset"] + le.get("tokLen", 0)
                gap = text[gap_start:rb["offset"]]
                op = n["opcode"]
                if gap.strip() == op:
                    at = gap_start + gap.find(op)
                    out.append(Mutant(line_of(at), f"`{op}` changed to `{_COMPOUND[op]}` in: {snippet(at)}",
                                      at, at + 2, _COMPOUND[op],
                                      "add an example or timeline that checks this value after a change"))
        if k == "UnaryOperator" and n.get("opcode") == "!" and b:
            at = b["offset"]
            if text[at] == "!":
                out.append(Mutant(line_of(at), f"`!` removed in: {snippet(at)}", at, at + 1, "",
                                  "add examples for both the true and the false case of this condition"))
        if k == "IntegerLiteral":
            if b and e and b["offset"] == e["offset"]:
                tok = text[b["offset"]:b["offset"] + b.get("tokLen", 0)]
                if tok.isdigit():
                    v = int(tok)
                    for nv in (v - 1, v + 1):
                        if nv >= 0:
                            out.append(Mutant(line_of(b["offset"]), f"`{v}` changed to `{nv}` in: "
                                              f"{snippet(b['offset'])}", b["offset"], b["offset"] + len(tok),
                                              str(nv), f"add an example right at {v} (and one next to it)"))
            else:
                ex = r.get("begin", {}).get("expansionLoc", {})
                if ex.get("file") == source_path and not ex.get("isMacroArgExpansion"):
                    name = text[ex["offset"]:ex["offset"] + ex.get("tokLen", 0)]
                    if re.fullmatch(r"[A-Za-z_]\w*", name):
                        used_macros.add(name)
        if parent_kind == "CompoundStmt" and k in ("BinaryOperator", "CompoundAssignOperator", "UnaryOperator",
                                                    "CallExpr") and b and e:
            if k != "BinaryOperator" or n.get("opcode") == "=":
                if k != "UnaryOperator" or n.get("opcode") in ("++", "--"):
                    start, end = b["offset"], e["offset"] + e.get("tokLen", 0)
                    out.append(Mutant(line_of(start), f"this line skipped: {snippet(start)}", start, end,
                                      "(void)0", "add an example or timeline that checks what this line changes"))
        for c in kids:
            if isinstance(c, dict):
                walk(c, k)
    walk(fn)
    # named constants the function uses: their #define value moved by one
    for name in sorted(used_macros):
        m = re.search(r"^[ \t]*#[ \t]*define[ \t]+" + re.escape(name) + r"[ \t]+(\d+)\b", text, re.M)
        if m:
            v = int(m.group(1))
            for nv in (v - 1, v + 1):
                if nv >= 0:
                    out.append(Mutant(line_of(m.start(1)), f"{name} changed from {v} to {nv}", m.start(1),
                                      m.end(1), str(nv), f"add an example right at {name} ({v}) and one next to it"))
    return out


def _macro_name(node, text):
    ex = node.get("range", {}).get("begin", {}).get("expansionLoc", {})
    if "offset" in ex:
        return text[ex["offset"]:ex["offset"] + ex.get("tokLen", 0)]
    return "?"


def _suggest_op(op, left, right):
    if op in ("<", "<=", ">", ">="):
        return f"add an example where {left} is exactly {right}"
    if op in ("==", "!="):
        return f"add examples where {left} equals {right}, and where it doesn't"
    if op in ("&&", "||"):
        return f"add examples where only one of `{left}` and `{right}` is true"
    return f"add an example whose result depends on `{left} {op} {right}`"


def measure(result, source_path: str, plan=None, lock=None, extra_args=None, workers: int = 4) -> Strength:
    from .check import check
    from .snapshot import check_lock, _NotComparable
    from .plan import PlanError
    from .harness import HarnessError
    function = lock["function"] if lock else (plan.function or _machine_name(result, plan))
    mutants = plant(source_path, function, extra_args)
    src = open(source_path, "rb").read()
    gcc = shutil.which("gcc") or shutil.which("clang")
    st = Strength(function=function)
    tmp = tempfile.mkdtemp(prefix="plainsight_strength_")

    def run(i_m):
        i, m = i_m
        path = os.path.join(tmp, f"m{i}_{os.path.basename(source_path)}")
        with open(path, "wb") as fh:
            fh.write(src[:m.start] + m.replacement.encode() + src[m.end:])
        from .lang import compiler
        r = subprocess.run([*compiler(path), "-fsyntax-only", "-w", *(extra_args or []), path],
                           capture_output=True, text=True)
        if r.returncode != 0:
            return m, None
        try:
            if lock:
                return m, bool(check_lock(result, path, lock, extra_args))
            return m, not check(result, path, plan, extra_args).ok
        except (PlanError, HarnessError, _NotComparable):
            return m, True  # the planted bug broke the code in a way the check refuses: noticed
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for m, noticed in pool.map(run, list(enumerate(mutants))):
                if noticed is None:
                    st.unusable += 1
                    continue
                st.total += 1
                if noticed:
                    st.caught += 1
                else:
                    st.survivors.append(m)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    st.survivors.sort(key=lambda m: m.line)
    return st


def _machine_name(result, plan):
    from .harness import find_machine
    return find_machine(result, plan.function, plan.modes).function


def format_strength(st: Strength, plan_name: str) -> str:
    lines = [f"Strength of {plan_name} for {st.function}",
             f"SCORE: {st.caught} of {st.total} planted bugs noticed ({st.score:.0f}%)"]
    if st.survivors:
        lines.append("\nNot noticed (some may make no real difference, e.g. `> 2` vs `>= 3` on whole numbers):")
        for k, m in enumerate(st.survivors, 1):
            lines.append(f"{k}. line {m.line}: {m.what}\n   -> {m.suggestion}")
    else:
        lines.append("Every planted bug was noticed.")
    return "\n".join(lines) + "\n"
