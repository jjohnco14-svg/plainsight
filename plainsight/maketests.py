"""Turn an approved plan into a standalone C test file.

The expected results come from the PLAN, so the file encodes what was
approved, not what the code happens to do. It needs nothing but a C
compiler to run:

    gcc -std=c11 plan_tests.c -o plan_tests && ./plan_tests

Chosen cases: for every plan line (and every outcome of a "then if"), the
plainest input that uses it; plus, around every number, the pairs of
inputs one step apart where the plan's answer changes -- the boundaries.
"""
import os
from typing import Dict, List, Tuple

from .check import _choices, _cases, _code_numbers, _changed_by, _fmt
from .harness import find_machine, find_function
from .plan import Plan, constants, calls as plan_calls

MAX_TESTS = 80


def _plainness(case, m):
    return (sum(1 for k, v in case.items() if k != m.mode_field and v < 0),
            sum(abs(v) for k, v in case.items() if k != m.mode_field))


def pick_cases(m, plan: Plan, cases: List[Dict]) -> List[Tuple[Dict, str]]:
    outcomes = {}
    for c in cases:
        mode = c.get(m.mode_field) if m.mode_field else None
        values = {k: v for k, v in c.items() if k != m.mode_field}
        new_mode, vals, rule = plan.step(mode, values)
        outcomes[tuple(sorted(c.items(), key=lambda kv: kv[0]))] = (new_mode, rule, vals)
    chosen, why = [], {}

    def key(c):
        return tuple(sorted(c.items(), key=lambda kv: kv[0]))

    # 1. for every (mode, plan line, result): the case where the most planned
    #    changes are VISIBLE (a value that starts where the plan leaves it
    #    can't show a missing change), then the plainest
    def visible(c, vals):
        return sum(1 for f, v in vals.items() if f in c and f != m.mode_field and v != c[f])

    best = {}
    for c in cases:
        mode = c.get(m.mode_field) if m.mode_field else None
        new_mode, rule, vals = outcomes[key(c)]
        k = (mode, rule, new_mode, vals.get("(gives back)"))
        score = (-visible(c, vals), _plainness(c, m))
        if k not in best or score < best[k][0]:
            best[k] = (score, c)
    best = {k: c for k, (_, c) in best.items()}
    for k, c in best.items():
        chosen.append(c)
        why[key(c)] = "plan line"
    # 2. boundaries: one input one step apart, and the plan's answer changes
    index = {key(c): c for c in cases}
    boundary = []
    for c in cases:
        for name, v in c.items():
            if name == m.mode_field:
                continue
            nxt = dict(c)
            nxt[name] = v + 1
            other = index.get(key(nxt))
            if other is None:
                continue
            a, b = outcomes[key(c)], outcomes[key(other)]
            if (a[0], a[1], a[2].get("(gives back)")) != (b[0], b[1], b[2].get("(gives back)")):
                boundary.append((_plainness(c, m), name, c, other))
    boundary.sort(key=lambda x: x[0])
    seen_edges = set()
    for _, name, c, other in boundary:
        mode = c.get(m.mode_field) if m.mode_field else None
        edge = (mode, name, c[name], outcomes[key(c)][1], outcomes[key(other)][1])
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        for x in (c, other):
            if key(x) not in why:
                chosen.append(x)
                why[key(x)] = f"boundary on {name}"
        if len(chosen) >= MAX_TESTS:
            break
    return [(c, why[key(c)]) for c in chosen[:MAX_TESTS]]


def make_tests(result, source_path: str, plan: Plan, out_path: str) -> int:
    rel = os.path.relpath(os.path.abspath(source_path), os.path.dirname(os.path.abspath(out_path)))
    n_examples = 0
    if plan.examples:
        from .examples import c_program
        fn = plan.function or find_machine(result, plan.function, plan.modes).function
        ex_path = out_path if plan.kind == "examples" else os.path.splitext(out_path)[0] + "_examples.c"
        with open(ex_path, "w") as fh:
            fh.write(c_program(result, fn, plan.examples, rel))
        n_examples = len(plan.examples)
    if plan.kind == "examples":
        return n_examples
    m = find_function(result, plan.function) if plan.kind == "rules" \
        else find_machine(result, plan.function, plan.modes)
    exempt = set()
    for c in plan_calls(plan):
        exempt |= _changed_by(result, c)
    shared = [(g, t) for g, t in (m.shared or [])]
    checked = [f for f, _ in m.fields if f != m.mode_field] + [g for g, _ in shared if g not in exempt]
    cases = _cases(m, plan, _choices(m, plan, constants(plan) + _code_numbers(result, m.function)))
    picked = pick_cases(m, plan, cases)

    names = [f for f, _ in m.fields] + [g for g, _ in shared] + [i for i, _ in m.inputs]
    rows, labels = [], []
    for case, why in picked:
        mode = case.get(m.mode_field) if m.mode_field else None
        values = {k: v for k, v in case.items() if k != m.mode_field}
        new_mode, vals, rule = plan.step(mode, values)
        ins = {**case}
        if m.mode_field:
            ins[m.mode_field] = m.modes[mode]
        expect = {}
        if m.mode_field:
            expect[m.mode_field] = m.modes[new_mode]
        for f in checked:
            expect[f] = vals.get(f, values.get(f, 0))
        if m.returns and plan.kind == "rules":
            expect["(gives back)"] = vals.get("(gives back)")
        inputs = ", ".join(_fmt(k, v, m) for k, v in case.items() if k != m.mode_field)
        where = f"in {mode}, with {inputs}" if mode else f"with {inputs}"
        result_words = []
        if m.mode_field:
            result_words.append(f"goes to {new_mode}" if new_mode != mode else f"stays in {mode}")
        if "(gives back)" in expect and expect["(gives back)"] is not None:
            result_words.append(f"gives back {_num(expect['(gives back)'])}")
        label = f"{where}: {' and '.join(result_words) or 'changes as planned'} ({why})"
        rows.append(([float(ins[n]) for n in names], expect))
        labels.append(label)

    outs = ([m.mode_field] if m.mode_field else []) + checked + \
        (["(gives back)"] if m.returns and plan.kind == "rules" else [])
    text = _c_file(m, names, outs, rows, labels, rel, plan)
    with open(out_path, "w") as fh:
        fh.write(text)
    return len(rows) + n_examples


def _num(v):
    return str(int(v)) if float(v).is_integer() else str(v)


def _cstr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _c_file(m, names, outs, rows, labels, rel, plan) -> str:
    shared = m.shared or []
    mode_names = {v: k for k, v in m.modes.items()}
    n_in, n_out = len(names), len(outs)
    table = []
    for (ins, expect), label in zip(rows, labels):
        exp = [expect[o] if expect.get(o) is not None else 0 for o in outs]
        table.append("    {" + _cstr(label) + ", {" + ", ".join(repr(x) for x in ins) + "}, {"
                     + ", ".join(repr(float(x)) for x in exp) + "}},")
    sets = ""
    if m.record_param:
        sets += f"        {m.record_c} r;\n        memset(&r, 0, sizeof r);\n"
        sets += "".join(f"        r.{f} = ({t}) t->in[{names.index(f)}];\n" for f, t in m.fields)
    sets += "".join(f"        {g} = ({t}) t->in[{names.index(g)}];\n" for g, t in shared)
    args = ["&r" if p == m.record_param else f"({dict(m.inputs)[p]}) t->in[{names.index(p)}]"
            for p in m.param_order]
    call = f"{m.function}({', '.join(args)})"
    got = []
    for o in outs:
        if o == "(gives back)":
            got.append("got_back")
        elif o in dict(m.fields):
            got.append(f"(double) r.{o}")
        else:
            got.append(f"(double) {o}")
    if "(gives back)" in outs:
        call = f"double got_back = (double) {call}"
    mode_table = ""
    if m.mode_field:
        mode_table = ("static const char *mode_name(double v) {\n" + "".join(
            f"    if (v == {num}) return {_cstr(name)};\n" for num, name in sorted(mode_names.items()))
            + '    return "(unknown mode)";\n}\n')
    out_names = ", ".join(_cstr("gives back" if o == "(gives back)" else o) for o in outs)
    return f"""/* Tests written by Plainsight from the approved plan for {m.function}.
 * Expected results come from the PLAN, not from the code.
 * Run:  gcc -std=c11 {os.path.basename(rel).replace('.c', '')}_plan_tests.c -o plan_tests && ./plan_tests
 * (or whatever this file is called). Exit code = number of failed tests.
 */
#define main plainsight_original_main
#include "{rel}"
#undef main
#include <stdio.h>
#include <string.h>
#include <math.h>

struct plan_test {{ const char *what; double in[{max(n_in, 1)}]; double want[{max(n_out, 1)}]; }};

static const struct plan_test tests[] = {{
{chr(10).join(table)}
}};

static const char *out_names[] = {{ {out_names} }};
{mode_table}
int main(void) {{
    int failed = 0, n = (int) (sizeof tests / sizeof tests[0]);
    for (int i = 0; i < n; i++) {{
        const struct plan_test *t = &tests[i];
{sets}        {call};
        double got[{max(n_out, 1)}] = {{ {", ".join(got) or "0"} }};
        int ok = 1;
        for (int k = 0; k < {n_out}; k++) {{
            if (fabs(got[k] - t->want[k]) > 1e-9) {{
                if (ok) printf("FAIL %d: %s\\n", i + 1, t->what);
                ok = 0;
                {"if (k == 0) printf(\"      mode: plan says %s, code gives %s\\n\", mode_name(t->want[k]), mode_name(got[k])); else" if m.mode_field else ""}
                printf("      %s: plan says %g, code gives %g\\n", out_names[k], t->want[k], got[k]);
            }}
        }}
        if (ok) printf("ok   %d: %s\\n", i + 1, t->what);
        failed += !ok;
    }}
    printf("\\n%d of %d plan tests passed.\\n", n - failed, n);
    return failed;
}}
"""
