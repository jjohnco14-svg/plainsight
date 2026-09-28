"""Run the REAL C function on many inputs, in one compiled test program.

For a machine function like  void pump_step(Pump *p, int level, bool flowing)
this writes a small C program that includes the user's file, then for each
input line sets every field of a fresh record plus the other inputs, calls
the function once, and prints every field afterwards. Nothing is simulated:
this is the user's own code, compiled by gcc with sanitizers on.
"""
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_SCALAR = re.compile(r"^(?:const\s+)?(?:unsigned\s+|signed\s+)?"
                     r"(?:_Bool|bool|char|short|int|long|long long|long int|short int|"
                     r"u?int(?:8|16|32|64)_t|size_t|float|double|enum \w+)$")


class HarnessError(Exception):
    pass


@dataclass
class Machine:
    """What to call, what to set before each call, and what to read after."""
    function: str
    record_param: Optional[str]          # None for a plain function
    record_c: Optional[str]              # "Pump" or "struct Pump"
    fields: List[Tuple[str, str]]        # record fields the test sets and reads (scalar)
    inputs: List[Tuple[str, str]]        # other parameters: (name, C type), in order
    param_order: List[str]               # every parameter name, in order
    mode_field: Optional[str]            # None for a plain function
    modes: Dict[str, int]                # mode name -> its number
    shared: List[Tuple[str, str]] = None # shared (file-level) variables the test sets and reads
    returns: Optional[str] = None        # C return type, if it gives something back
    skipped: List[str] = None            # fields left at zero and not compared (lists etc.)


_NUMBER_WORDS = ("whole number", "fractional number", "truth value", "choice", "byte", "character",
                 "count")


def _scalar(ctype: str, desugared: str, words: str = "") -> bool:
    """A plain number the test can set: by its C spelling, or by the type words
    Plainsight worked out (a typedef'd enum reads 'PumpState choice')."""
    for t in (desugared, ctype):
        if t and _SCALAR.match(t.strip()):
            return True
    w = words.replace("read-only ", "")
    return any(w.endswith(x) or w.startswith(x) for x in _NUMBER_WORDS) and "pointer" not in w \
        and "list" not in w


def find_machine(result, function: Optional[str], plan_modes: Optional[List[str]]) -> Machine:
    """The function to test, the record it changes, and the record field that
    holds the mode -- found from TYPES, not from the code's shape, so the code
    may be written any way at all."""
    from .dictum_bridge import A
    from . import nodes as N
    actions = [x for x in result.items if isinstance(x, A.Action)]
    shapes = {x.name: x for x in result.items if isinstance(x, A.Shape)}
    enums = {x.name: x for x in result.items if isinstance(x, N.EnumDecl)}

    def mode_field_of(shape):
        """The field whose named options share the most modes with the plan
        (or, with no plan, the first field holding named options).
        Modes on only one side are reported by the comparison, not here."""
        best, best_overlap = None, 0
        for fname, words in shape.fields:
            w = words.replace("read-only ", "")
            if w.endswith(" choice") and w[:-len(" choice")] in enums:
                options = dict(enums[w[:-len(" choice")]].options)
                if plan_modes is None:
                    return fname, options
                overlap = len(set(plan_modes) & set(options))
                if overlap > best_overlap:
                    best, best_overlap = (fname, options), overlap
        return best

    candidates = []
    for a in actions:
        if function is not None and a.name != function:
            continue
        recs = [(n, t) for n, t in a.params if t.startswith("pointer to ") and t.endswith(" record")]
        if len(recs) != 1:
            continue
        shape = shapes.get(recs[0][1][len("pointer to "):-len(" record")])
        if shape is not None and mode_field_of(shape):
            candidates.append((a, recs[0][0], shape))
    if not candidates:
        what = f"'{function}'" if function else "any function"
        raise HarnessError(f"{what} in the code doesn't take a record with a field holding any of the "
                           f"plan's modes ({', '.join(plan_modes or [])})")
    if len(candidates) > 1:
        raise HarnessError("more than one function could be the machine (" +
                           ", ".join(c[0].name for c in candidates) +
                           "); name it with '%% function: NAME' at the top of the plan")
    a, rec_param, shape = candidates[0]
    mode_field, modes = mode_field_of(shape)
    if not getattr(shape, "c_name", None):
        raise HarnessError(f"can't find how C spells the record type {shape.name}")
    fields, skipped = [], []
    field_words = dict(shape.fields)
    for name, ctype, desugared in shape.c_fields:
        if _scalar(ctype, desugared, field_words.get(name, "")):
            fields.append((name, ctype))
        else:
            skipped.append(name)  # a list or pointer: left at zero, not compared (stated in the report)
    return Machine(function=a.name, record_param=rec_param, record_c=shape.c_name, fields=fields,
                   inputs=_inputs(a, rec_param), param_order=[n for n, _, _ in a.c_params],
                   mode_field=mode_field, modes=modes, shared=_shared(result),
                   returns=_returns(a), skipped=skipped)


def find_function(result, function: str) -> Machine:
    """A plain function: numbers in, maybe a number out, maybe shared variables changed."""
    from .dictum_bridge import A
    fns = [x for x in result.items if isinstance(x, A.Action) and x.name == function]
    if not fns:
        raise HarnessError(f"the code has no function called '{function}'")
    a = fns[0]
    return Machine(function=a.name, record_param=None, record_c=None, fields=[],
                   inputs=_inputs(a, None), param_order=[n for n, _, _ in a.c_params],
                   mode_field=None, modes={}, shared=_shared(result), returns=_returns(a), skipped=[])


def _inputs(a, rec_param):
    inputs = []
    param_words = dict(a.params)
    for name, ctype, desugared in a.c_params:
        if name == rec_param:
            continue
        if not _scalar(ctype, desugared, param_words.get(name, "")):
            raise HarnessError(f"input '{name}' ({ctype}) isn't a plain number or truth value, so a "
                               f"test can't set it yet")
        inputs.append((name, ctype))
    return inputs


def _shared(result):
    """File-level variables a test can set and read: plain numbers, not read-only."""
    from .dictum_bridge import A
    out = []
    for v in result.items:
        if isinstance(v, A.VarDecl) and getattr(v, "is_global", False) and not getattr(v, "is_extern", False):
            w = v.type
            if w.startswith("read-only") or not _scalar("", "", w):
                continue
            ctype = getattr(v, "c_type", "") or "double"
            out.append((v.name, ctype))
    return out


def _returns(a):
    return None if a.ret_type == "nothing" else (getattr(a, "c_return", None) or "double")


def g_modes(g):
    return [nid for nid, _, shape in g.nodes if shape == "state"]


def run_cases(source_path: str, m: Machine, cases: List[Dict[str, float]],
              extra_args: Optional[List[str]] = None) -> List[Dict[str, float]]:
    """cases: a value for every record field, shared variable and input.
    Returns the record fields and shared variables after one call, plus
    '(gives back)' if the function returns something."""
    gcc = shutil.which("gcc") or shutil.which("clang")
    if not gcc:
        raise HarnessError("gcc (or clang) is needed to compile the tests")
    shared = m.shared or []
    names = [f for f, _ in m.fields] + [g for g, _ in shared] + [i for i, _ in m.inputs]
    program = test_program(source_path, m, names, [
        f"        if (scanf(\"{' '.join(['%lf'] * len(names))}\", "
        + ", ".join(f"&v{k}" for k in range(len(names))) + f") != {len(names)}) break;"
    ], loop=True)
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = os.path.join(tmp, "check.c"), os.path.join(tmp, "check")
        with open(src, "w") as fh:
            fh.write(program)
        from .lang import compiler
        cmd = [*compiler(source_path), "-w", "-fsanitize=undefined", "-fno-sanitize-recover=all",
               *(extra_args or []), src, "-o", exe, "-lm"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise HarnessError("the test program didn't compile: " + r.stderr.strip().splitlines()[-1])
        data = "\n".join(" ".join(repr(float(c[n])) for n in names) for c in cases) + "\n"
        r = subprocess.run([exe], input=data, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            raise HarnessError("the code crashed or misbehaved while testing: " + r.stderr.strip()[:500])
    outs = [f for f, _ in m.fields] + [g for g, _ in shared] + (["(gives back)"] if m.returns else [])
    out = []
    for line in r.stdout.splitlines():
        if not line.startswith("@@"):
            continue  # anything the user's code printed itself
        vals = [float(x) for x in line[2:].split()]
        out.append(dict(zip(outs, vals)))
    if len(out) != len(cases):
        raise HarnessError("the test program stopped early")
    return out


def test_program(source_path: str, m: Machine, names: List[str], read_lines: List[str],
                 loop: bool) -> str:
    """The C around one call: set everything, call, print everything."""
    shared = m.shared or []
    decls = "".join(f"        double v{k};\n" for k in range(len(names)))
    sets = ""
    if m.record_param:
        sets += f"        {m.record_c} r;\n        memset(&r, 0, sizeof r);\n"
        sets += "".join(f"        r.{f} = ({t}) v{names.index(f)};\n" for f, t in m.fields)
    sets += "".join(f"        {g} = ({t}) v{names.index(g)};\n" for g, t in shared)
    args = []
    for p in m.param_order:
        if p == m.record_param:
            args.append("&r")
        else:
            args.append(f"({dict(m.inputs)[p]}) v{names.index(p)}")
    call = f"{m.function}({', '.join(args)})"
    outs = [f"(double) r.{f}" for f, _ in m.fields] + [f"(double) {g}" for g, _ in shared]
    if m.returns:
        call = f"double plainsight_result = (double) {call}"
        outs.append("plainsight_result")
    fmt = " ".join(["%.17g"] * len(outs))
    return f"""/* written by Plainsight: runs {m.function} once per input line */
#define main plainsight_original_main
#include "{os.path.abspath(source_path)}"
#undef main
#include <stdio.h>
#include <string.h>
int main(void) {{
    setvbuf(stdout, NULL, _IOFBF, 1 << 16);
    for (;;) {{
{decls}""" + "\n".join(read_lines) + f"""
{sets}        {call};
        printf("@@{fmt}\\n"{", " if outs else ""}{", ".join(outs)});
    }}
    return 0;
}}
"""
