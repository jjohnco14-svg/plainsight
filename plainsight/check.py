"""Check real code against an approved plan, by behaviour.

Both are run on the same inputs -- the plan as a simulator, the code
compiled and run for real -- and every disagreement is reported as a
concrete example in plain words. Inputs are chosen around every number
that appears in the plan or the code (e.g. 89, 90, 91 for "at least 90"),
because that's where mistakes hide.
"""
import itertools
import random
from dataclasses import dataclass, field
from typing import Dict, List

from .dictum_bridge import A
from .harness import Machine, find_machine, find_function, run_cases
from .plan import Plan, PlanError, constants, calls as plan_calls

LIMIT = 20000


@dataclass
class Report:
    function: str
    cases: int = 0
    problems: List[str] = field(default_factory=list)   # plain-words findings
    groups: List[dict] = field(default_factory=list)
    not_checked: List[str] = field(default_factory=list)  # stated, never silently passed
    plan_warnings: List[str] = field(default_factory=list)  # about the plan itself

    @property
    def ok(self) -> bool:
        return not self.problems


def _is_bool(ctype: str) -> bool:
    return "bool" in ctype.lower()


def _code_numbers(result, function) -> List[float]:
    from .summarize import _all_nodes
    out = set()
    for a in result.items:
        if isinstance(a, A.Action) and a.name == function:
            for x in _all_nodes(a.body):
                if isinstance(x, A.Literal) and isinstance(x.value, (int, float)):
                    out.add(x.value)
                if isinstance(x, A.Identifier) and getattr(x, "value_text", None):
                    try:
                        out.add(int(x.value_text.rstrip("uUlL"), 0))
                    except ValueError:
                        pass
    return sorted(out)


def _choices(m: Machine, plan: Plan, numbers) -> Dict[str, List[float]]:
    around = {0, 1, -1}
    for c in numbers:
        around |= {c - 1, c, c + 1}
    ints = sorted(x for x in around)
    out = {}
    for name, ctype in m.fields + (m.shared or []) + m.inputs:
        if name == m.mode_field:
            continue
        out[name] = [0, 1] if _is_bool(ctype) else ints
    return out


def _cases(m: Machine, plan: Plan, choices) -> List[Dict[str, float]]:
    names = list(choices)
    if not m.mode_field:  # a plain function: one "mode" that isn't a variable
        combos = itertools.product(*[choices[n] for n in names])
        total = 1
        for n in names:
            total *= len(choices[n])
        if total <= LIMIT:
            return [dict(zip(names, c)) for c in combos]
        rng = random.Random(1234)
        out = [{**{k: 0 for k in names}, n: v} for n in names for v in choices[n]]
        while len(out) < LIMIT:
            out.append({n: rng.choice(choices[n]) for n in names})
        return out
    modes = [x for x in plan.modes]
    total = len(modes)
    for n in names:
        total *= len(choices[n])
    if total <= LIMIT:
        combos = itertools.product(modes, *[choices[n] for n in names])
        return [dict(zip([m.mode_field] + names, c)) for c in combos]
    rng = random.Random(1234)  # fixed: the same inputs every run
    out = []
    seen = set()
    # every value of every input at least once, with the others at 0
    for mode in modes:
        for n in names:
            for v in choices[n]:
                case = {m.mode_field: mode, **{k: 0 for k in names}, n: v}
                out.append(case)
    while len(out) < LIMIT:
        c = (rng.choice(modes),) + tuple(rng.choice(choices[n]) for n in names)
        if c not in seen:
            seen.add(c)
            out.append(dict(zip([m.mode_field] + names, c)))
    return out


def _fmt(name, value, m: Machine) -> str:
    ctype = dict(m.fields + (m.shared or []) + m.inputs).get(name, "")
    if _is_bool(ctype):
        return f"{name} = {'yes' if value else 'no'}"
    if float(value).is_integer():
        return f"{name} = {int(value)}"
    return f"{name} = {value}"


def _changed_by(result, fn, seen=None) -> set:
    """Shared variables a function changes, itself or through functions it runs."""
    seen = seen if seen is not None else set()
    if fn in seen:
        return set()
    seen.add(fn)
    f = result.facts.get(fn, {})
    out = set(f.get("writes_globals", []))
    for c in f.get("calls_local", []):
        out |= _changed_by(result, c, seen)
    return out


def check(result, source_path: str, plan: Plan, extra_args=None) -> Report:
    if plan.examples:
        from .examples import run_examples
        ex = run_examples(result, source_path, plan.function or _machine_name(result, plan),
                          plan.examples, extra_args)
    if plan.kind == "examples":
        rep = Report(function=plan.function, cases=ex.total)
        rep.problems = ex.failures
        rep.examples = ex
        return rep
    rep = _check_rules_or_machine(result, source_path, plan, extra_args)
    if plan.examples:
        rep.problems += ex.failures
        rep.examples = ex
    return rep


def _machine_name(result, plan):
    return find_machine(result, plan.function, plan.modes).function


def _check_rules_or_machine(result, source_path: str, plan: Plan, extra_args=None) -> Report:
    if plan.kind == "rules":
        m = find_function(result, plan.function)
    else:
        m = find_machine(result, plan.function, plan.modes)
    rep = Report(function=m.function)
    rep.machine = m
    if m.mode_field:
        code_modes = set(m.modes)
        for mode in plan.modes:
            if mode not in code_modes:
                rep.problems.append(f"The plan has a mode {mode} that the code doesn't have.")
        for mode in sorted(code_modes - set(plan.modes)):
            rep.problems.append(f"The code has a mode {mode} that the plan doesn't mention.")
        if rep.problems:
            return rep
    facts = result.facts.get(m.function, {})
    code_calls = set(facts.get("calls_local", [])) | set(facts.get("calls_external", []))
    named = plan_calls(plan)
    for c in named:
        if c not in code_calls:
            rep.problems.append(f"The plan says it runs {c}, but the code never does.")
    # "calls f" in a plan means "runs f, whatever f does": what f changes isn't compared
    exempt = set()
    for c in named:
        exempt |= _changed_by(result, c)
    shared = [g for g, _ in (m.shared or [])]
    compared = [f for f, _ in m.fields if f != m.mode_field] + [g for g in shared if g not in exempt]
    if exempt & set(shared):
        rep.not_checked.append("shared variables changed by the functions the plan names ("
                               + ", ".join(sorted(exempt & set(shared))) + ")")
    other = [t["pointer"] for t in facts.get("writes_through_pointers", []) if t["pointer"] != m.record_param]
    if other:
        rep.not_checked.append("changes through other pointers: " + ", ".join(other))
    if m.skipped:
        rep.not_checked.append("record fields that aren't plain numbers: " + ", ".join(m.skipped))
    io = [e for e in facts.get("effects", []) if e.startswith("does input/output")]
    if io:
        rep.not_checked.append("what it prints or reads (" + io[0].split("(", 1)[1])
    if any("through a pointer" in p for p in facts.get("possible_effects", [])):
        rep.not_checked.append("functions called through pointers")

    numbers = constants(plan) + _code_numbers(result, m.function)
    choices = _choices(m, plan, numbers)
    cases = _cases(m, plan, choices)
    code_in = [{**c, m.mode_field: m.modes[c[m.mode_field]]} if m.mode_field else c for c in cases]
    code_out = run_cases(source_path, m, code_in, extra_args)
    rep.cases = len(cases)
    number_to_mode = {v: k for k, v in m.modes.items()}
    groups: Dict[tuple, dict] = {}
    for case, got in zip(cases, code_out):
        mode = case.get(m.mode_field) if m.mode_field else None
        values = {k: v for k, v in case.items() if k != m.mode_field}
        try:
            plan_mode, plan_vals, rule = plan.step(mode, values)
        except PlanError as e:
            rep.problems.append(str(e))
            return rep
        code_mode = number_to_mode.get(int(got[m.mode_field]), f"(value {got[m.mode_field]})") \
            if m.mode_field else None
        diffs = [f for f in compared if abs(plan_vals.get(f, values.get(f, 0)) - got[f]) > 1e-9]
        if m.returns and plan.kind == "rules":
            want = plan_vals.get("(gives back)")
            if want is None:
                rep.problems.append(f"The plan doesn't say what {m.function} gives back when "
                                    + ", ".join(_fmt(k, v, m) for k, v in values.items()) + ".")
                return rep
            if abs(want - got["(gives back)"]) > 1e-9:
                diffs.append("(gives back)")
        if plan_mode == code_mode and not diffs:
            continue
        key = (mode, plan_mode, code_mode, tuple(diffs))
        g = groups.setdefault(key, {"count": 0, "examples": [], "rule": rule})
        g["count"] += 1
        g["examples"].append((case, plan_vals, got))
    for (mode, plan_mode, code_mode, diffs), g in sorted(groups.items(), key=lambda kv: -kv[1]["count"]):
        # the plainest example first: no negative values, then the smallest ones
        g["examples"].sort(key=lambda e: (sum(1 for k, v in e[0].items() if k != m.mode_field and v < 0),
                                          sum(abs(v) for k, v in e[0].items() if k != m.mode_field)))
        case, plan_vals, got = g["examples"][0]
        inputs = ", ".join(_fmt(k, v, m) for k, v in case.items() if k != m.mode_field)
        lines = [f"In {mode}, with {inputs}:" if mode else f"With {inputs}:"]
        if plan_mode != code_mode:
            says = f"goes to {plan_mode}" if plan_mode != mode else f"stays in {mode}"
            does = f"goes to {code_mode}" if code_mode != mode else f"stays in {mode}"
            lines.append(f"  the plan says it {says}; the code {does}.")
        for f in diffs:
            if f == "(gives back)":
                lines.append(f"  the plan says it gives back {_num(plan_vals.get(f))}; "
                             f"the code gives back {_num(got[f])}.")
            else:
                lines.append(f"  the plan says {f} ends at {_fmt(f, plan_vals.get(f, 0), m).split(' = ')[1]}; "
                             f"the code gives {_fmt(f, got[f], m).split(' = ')[1]}.")
        if g["rule"]:
            lines.append(f"  (plan line used: \"{g['rule']}\")")
        lines.append(f"  The same difference shows up in {g['count']} of the {rep.cases} tested cases.")
        rep.problems.append("\n".join(lines))
        rep.groups.append({"mode": mode, "plan": plan_mode, "code": code_mode, "fields": list(diffs),
                           "count": g["count"]})
    return rep


def _num(v) -> str:
    return str(int(v)) if v is not None and float(v).is_integer() else str(v)


def add_plan_warnings(rep: Report, plan: Plan):
    """The plan search is slow-ish; run it when a person will read the report."""
    from .plancheck import plan_warnings
    m = getattr(rep, "machine", None)
    if m is None:
        rep.plan_warnings = plan_warnings(plan)
        return
    rep.plan_warnings = plan_warnings(plan, persistent={f for f, _ in m.fields if f != m.mode_field}
                                      | {g for g, _ in (m.shared or [])}, inputs={i for i, _ in m.inputs})


def format_report(rep: Report, plan_name: str) -> str:
    head = f"Checking {rep.function} against the plan {plan_name}"
    if rep.plan_warnings:
        head += "\nPLAN WARNINGS (about the plan itself -- check it says what you mean):\n" + \
            "\n".join(f"  - {w}" for w in rep.plan_warnings)
    extra = "".join(f"Not checked: {x}.\n" for x in rep.not_checked)
    ex = getattr(rep, "examples", None)
    examples_only = ex is not None and getattr(rep, "machine", None) is None
    if ex is not None and ex.total and not examples_only:
        extra = f"Examples: {ex.passed} of {ex.total} passed.\n" + extra
    if rep.ok and examples_only:
        return f"{head}\nMATCHES: all {ex.total} examples passed.\n{extra}"
    if not rep.ok and examples_only:
        body = "\n\n".join(f"{i}. {p}" for i, p in enumerate(rep.problems, 1))
        return f"{head}\nDIFFERENT: {ex.total - ex.passed} of {ex.total} examples failed.\n\n{body}\n{extra}"
    if rep.ok:
        return (f"{head}\nMATCHES: the code did exactly what the plan says in all {rep.cases} tested cases "
                f"(inputs around every number in the plan and the code).\n{extra}")
    body = "\n\n".join(f"{i}. {p}" for i, p in enumerate(rep.problems, 1))
    tested = f" ({rep.cases} cases tested)" if rep.cases else ""
    return f"{head}\nDIFFERENT{tested}: {len(rep.problems)} difference(s) found.\n\n{body}\n{extra}"
