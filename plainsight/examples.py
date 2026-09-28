"""Examples: concrete inputs and what should come out, for ANY function.

    function: minutes_to_fill
    example: levels = [50, 18, 40, 70, 95], n = 5 -> gives back 3
    example: levels = [50, 60, 95], n = 3 -> gives back -1

    function: clamp_all
    example: values = [1.5, -3, 20], n = 3, lo = 0, hi = 10 -> values becomes [1.5, 0, 10]

    function: greet
    example: name = "Ana" -> prints "Hello, Ana!\\n"

    function: pump_step
    example: p.state = PUMPING, p.run_minutes = 2, level = 50, flowing = false -> p.state becomes FAULT

Inputs: numbers, true/false, names from the code (modes, constants), "text",
[lists], and record fields as p.field. Results: `gives back V`,
`NAME becomes V` (a list changed in place, a record field, a shared
variable, a value passed by pointer), `prints "TEXT"`.

Each example becomes C code around the REAL function, runs in its own
process (so shared variables can't leak between examples, and a crash is
reported as that example's failure), and its printing is captured. The
same generated file is the standalone test file.
"""
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .plan import PlanError


@dataclass
class Example:
    text: str
    inputs: List[Tuple[str, object]]    # (name or p.field, value)
    results: List[Tuple[str, str, object]]  # (kind, name, value): kind in give/becomes/prints


# ------------------------------------------------------------------ reading

def _split_top(text: str, sep: str) -> List[str]:
    out, depth, cur, quote, i = [], 0, "", None, 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur += ch
            if ch == "\\" and i + 1 < len(text):
                cur += text[i + 1]
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch in "[(":
            depth += 1
            cur += ch
        elif ch in "])":
            depth -= 1
            cur += ch
        elif depth == 0 and text.startswith(sep, i):
            out.append(cur)
            cur = ""
            i += len(sep)
            continue
        else:
            cur += ch
        i += 1
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


_NUM = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")
_NAME = re.compile(r"[A-Za-z_]\w*")


def parse_value(text: str, line: str):
    t = text.strip()
    if t.startswith("[") and t.endswith("]"):
        return [parse_value(x, line) for x in _split_top(t[1:-1], ",")]
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return ("text", t[1:-1])
    if len(t) == 3 and t[0] == "'" and t[-1] == "'":
        return ("char", t)
    if _NUM.fullmatch(t):
        return ("num", t)
    if t in ("true", "false", "yes", "no"):
        return ("bool", "1" if t in ("true", "yes") else "0")
    if _NAME.fullmatch(t):
        return ("name", t)
    raise PlanError(f"don't understand the value '{t}' in: {line}")


def parse_example(line: str) -> Example:
    body = line.split(":", 1)[1] if line.lower().startswith("example") else line
    parts = _split_top(body.replace("→", "->"), "->")
    if len(parts) != 2:
        raise PlanError(f"an example needs inputs, '->', then results: {line}")
    inputs = []
    for item in _split_top(parts[0], ","):
        m = re.fullmatch(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[\d+\])?)\s*=\s*(.+)", item, re.S)
        if not m:
            raise PlanError(f"inputs are written 'name = value': '{item}' in: {line}")
        inputs.append((m.group(1), parse_value(m.group(2), line)))
    results = []
    for item in _split_top(parts[1], ","):
        m = re.fullmatch(r"gives back\s+(.+)", item, re.S)
        if m:
            results.append(("give", None, parse_value(m.group(1), line)))
            continue
        m = re.fullmatch(r"prints\s+(\".*\")", item, re.S)
        if m:
            results.append(("prints", None, parse_value(m.group(1), line)))
            continue
        m = re.fullmatch(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[\d+\])?)\s+becomes\s+(.+)", item, re.S)
        if m:
            results.append(("becomes", m.group(1), parse_value(m.group(2), line)))
            continue
        raise PlanError(f"don't understand the result '{item}' (use 'gives back V', 'NAME becomes V' or "
                        f"'prints \"TEXT\"') in: {line}")
    if not results:
        raise PlanError(f"an example must say what should come out: {line}")
    return Example(text=line.strip(), inputs=inputs, results=results)


@dataclass
class Timeline:
    """Several calls in a row on the same record: behaviour over time.

    timeline: p.state = IDLE | level = [15, 15, 50, 95], flowing = true -> p.state = [PUMPING, PUMPING, PUMPING, IDLE]

    Before '|': where it starts (record fields, shared variables). Between '|' and
    '->': the inputs for each step (a list = one value per step; one value = the
    same every step). After '->': what should be true after each step (lists;
    '_' = don't care). The record and shared variables carry over between steps.
    """
    text: str
    start: List[Tuple[str, object]]
    steps: List[Dict[str, object]]
    expects: List[Tuple[str, str, List[object]]]   # (kind: give|becomes, name, per-step values or None)


def parse_timeline(line: str) -> Timeline:
    body = line.split(":", 1)[1]
    arrow = _split_top(body.replace("→", "->"), "->")
    if len(arrow) != 2:
        raise PlanError(f"a timeline needs '... | inputs -> results': {line}")
    left, right = arrow
    if "|" in left:
        start_text, steps_text = left.split("|", 1)
    else:
        start_text, steps_text = "", left
    start = []
    for item in _split_top(start_text, ","):
        m = re.fullmatch(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[\d+\])?)\s*=\s*(.+)", item, re.S)
        if not m:
            raise PlanError(f"the start is written 'name = value': '{item}' in: {line}")
        start.append((m.group(1), parse_value(m.group(2), line)))
    per_input = []
    for item in _split_top(steps_text, ","):
        m = re.fullmatch(r"([A-Za-z_]\w*(?:\[\d+\])?)\s*=\s*(.+)", item, re.S)
        if not m:
            raise PlanError(f"step inputs are written 'name = [v, v, ...]': '{item}' in: {line}")
        per_input.append((m.group(1), parse_value(m.group(2), line)))
    expects = []
    for item in _split_top(right, ","):
        m = re.fullmatch(r"gives back\s*=?\s*(.+)", item, re.S)
        if m:
            name, val, kind = None, m.group(1), "give"
        else:
            m = re.fullmatch(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[\d+\])?)\s*(?:=|becomes)\s*(.+)", item, re.S)
            if not m:
                raise PlanError(f"results are written 'name = [v, v, ...]' or 'gives back [...]': '{item}' in: {line}")
            name, val, kind = m.group(1), m.group(2), "becomes"
        val = val.strip()
        if not (val.startswith("[") and val.endswith("]")):
            raise PlanError(f"a timeline result needs one value per step, as a list: '{item}' in: {line}")
        values = [None if x.strip() == "_" else parse_value(x, line) for x in _split_top(val[1:-1], ",")]
        expects.append((kind, name, values))
    lengths = {len(v) for _, v in per_input if isinstance(v, list)} | {len(v) for _, _, v in expects}
    if len(lengths) > 1:
        raise PlanError(f"the lists in a timeline must all have one value per step (found lengths "
                        f"{sorted(lengths)}): {line}")
    n = lengths.pop() if lengths else 1
    steps = [{name: (v[k] if isinstance(v, list) else v) for name, v in per_input} for k in range(n)]
    return Timeline(text=line.strip(), start=start, steps=steps, expects=expects)


# ------------------------------------------------------------------ writing the C

def _c(v) -> str:
    kind, raw = v
    if kind == "text":
        return f'"{raw}"'
    return raw


def _strip_ptr(t: str) -> str:
    t = t.strip()
    if t.endswith("*"):
        t = t[:-1].strip()
    return re.sub(r"\bconst\b", "", t).strip()


def _is_char_ptr(t: str) -> bool:
    return bool(re.fullmatch(r"(const\s+)?char\s*\*", t.strip()))


def _describe(ex: Example) -> str:
    return ex.text.split(":", 1)[1].strip() if ex.text.lower().startswith("example") else ex.text


def c_program(result, function: str, examples: List[Example], source_rel: str) -> str:
    from .dictum_bridge import A
    fns = [x for x in result.items if isinstance(x, A.Action) and x.name == function]
    if not fns:
        raise PlanError(f"the code has no function called '{function}'")
    a = fns[0]
    params = [(n, q, d) for n, q, d in a.c_params]
    pnames = [n for n, _, _ in params]
    shapes = {x.name: x for x in result.items if isinstance(x, A.Shape)}
    shared = {v.name: getattr(v, "c_type", "") for v in result.items
              if isinstance(v, A.VarDecl) and getattr(v, "is_global", False)}
    ret = a.c_return
    from . import nodes as N
    enums = [x for x in result.items if isinstance(x, N.EnumDecl)]
    namer, helpers = {}, []
    for k, e in enumerate(enums):
        fn_name = f"enum_word_{k}"
        cases = "".join(f'    if (v == {val}) return "{n}";\n' for n, val in e.options)
        helpers.append(f"static const char *{fn_name}(double v) {{\n{cases}    return \"(not a {e.name} mode)\";\n}}\n")
        for n, _ in e.options:
            namer[n] = fn_name
    blocks = []
    for i, ex in enumerate(examples, 1):
        if isinstance(ex, Timeline):
            blocks.append(_timeline_block(i, ex, a, params, pnames, shapes, shared, ret, function, namer, result))
            continue
        given = dict(ex.inputs)
        decl, setup, args, checks = [], [], [], []
        record_params = {}
        for name, q, d in params:
            words = dict(a.params)[name]
            if words.startswith("pointer to ") and words.endswith(" record"):
                shape = shapes.get(words[len("pointer to "):-len(" record")])
                if shape is None:
                    raise PlanError(f"can't find the record type of '{name}'")
                decl.append(f"{shape.c_name} {name}_rec;")
                decl.append(f"memset(&{name}_rec, 0, sizeof {name}_rec);")
                for k, v in ex.inputs:
                    if k.startswith(name + "."):
                        decl.append(f"{name}_rec.{k.split('.', 1)[1]} = {_c(v)};")
                args.append(f"&{name}_rec")
                record_params[name] = f"{name}_rec"
                continue
            if name not in given:
                raise PlanError(f"example {i} doesn't give a value for the input '{name}': {ex.text}")
            v = given[name]
            ctype = q
            if isinstance(v, list):
                elem = _strip_ptr(ctype) if ctype.strip().endswith("*") else re.sub(r"\[.*\]", "", ctype).strip()
                elem = re.sub(r"\bconst\b", "", elem).strip()
                decl.append(f"{elem} {name}_arr[] = {{{', '.join(_c(x) for x in v)}}};")
                args.append(f"{name}_arr")
            elif v[0] == "text" and (_is_char_ptr(ctype) or "char" in ctype):
                decl.append(f"char {name}_txt[] = {_c(v)};")
                args.append(f"{name}_txt")
            elif ctype.strip().endswith("*"):
                elem = _strip_ptr(ctype)
                decl.append(f"{elem} {name}_one = {_c(v)};")
                args.append(f"&{name}_one")
            else:
                args.append(f"({ctype}) ({_c(v)})")
        for k, v in ex.inputs:
            hw = hardware(k, result)
            if hw:
                setup.append(f"{hw} = {_c(v)};")
                continue
            if "." in k:
                if k.split(".")[0] not in record_params:
                    raise PlanError(f"'{k}' in example {i}: '{k.split('.')[0]}' isn't a record the function takes")
                continue
            if k not in pnames:
                if k not in shared:
                    raise PlanError(f"'{k}' in example {i} is neither an input of {function} nor a shared variable")
                setup.append(f"{k} = {_c(v)};")
        # results
        call = f"{function}({', '.join(args)})"
        for kind, name, v in ex.results:
            if kind == "give":
                if ret is None or ret.strip() == "void":
                    raise PlanError(f"example {i} says {function} gives something back, but it gives back nothing")
                if v[0] == "text":
                    checks.append(("text", "gives back", "got_back", _c(v)))
                else:
                    checks.append(("num", "gives back", "(double) got_back", f"(double) ({_c(v)})"))
            elif kind == "prints":
                checks.append(("text", "prints", "printed", _c(v)))
            else:
                base = name.split(".")[0]
                where = hardware(name, result)
                if where:
                    checks.append(("num", name, where, f"(double) ({_c(v)})"))
                    continue
                if "." in name:
                    if base not in record_params:
                        raise PlanError(f"'{name}' in example {i}: '{base}' isn't a record the function takes")
                    where = f"{record_params[base]}.{name.split('.', 1)[1]}"
                elif name in pnames:
                    gv = given.get(name)
                    if isinstance(gv, list):
                        where = f"{name}_arr"
                    elif gv is not None and gv[0] == "text":
                        where = f"{name}_txt"
                    elif dict((n, q) for n, q, _ in params)[name].strip().endswith("*"):
                        where = f"{name}_one"
                    else:
                        raise PlanError(f"'{name}' in example {i} is passed by value, so the function "
                                        f"can't change it for the caller")
                elif name in shared:
                    where = name
                else:
                    raise PlanError(f"'{name}' in example {i} is not something the function can change")
                if isinstance(v, list):
                    checks.append(("list", name, where, [_c(x) for x in v]))
                elif v[0] == "text":
                    checks.append(("text", name, where, _c(v)))
                else:
                    checks.append(("num", name, where, f"(double) ({_c(v)})"))
        blocks.append(_example_block(i, _describe(ex), decl, setup, call, ret, checks))
    return _wrap(source_rel, function, blocks, "".join(helpers))


def _timeline_block(i, tl: "Timeline", a, params, pnames, shapes, shared, ret, function,
                    enum_namer=None, result=None) -> str:
    """One child process: set the start, then call once per step, checking after each."""
    enum_namer = enum_namer or {}
    decl, start = [], []
    records = {}
    for name, q, d in params:
        words = dict(a.params)[name]
        if words.startswith("pointer to ") and words.endswith(" record"):
            shape = shapes.get(words[len("pointer to "):-len(" record")])
            decl.append(f"{shape.c_name} {name}_rec;")
            decl.append(f"memset(&{name}_rec, 0, sizeof {name}_rec);")
            records[name] = f"{name}_rec"
        elif not q.strip().endswith("*") and name not in tl.steps[0]:
            raise PlanError(f"timeline {i} doesn't give the input '{name}' for its steps: {tl.text}")
    for k, v in tl.start:
        hw = hardware(k, result)
        if hw:
            start.append(f"{hw} = {_c(v)};")
        elif "." in k:
            base = k.split(".")[0]
            if base not in records:
                raise PlanError(f"'{k}' in timeline {i}: '{base}' isn't a record the function takes")
            start.append(f"{records[base]}.{k.split('.', 1)[1]} = {_c(v)};")
        elif k in shared:
            start.append(f"{k} = {_c(v)};")
        else:
            raise PlanError(f"'{k}' in the start of timeline {i} is neither a record field nor a shared variable")
    for name in tl.steps[0]:
        if name not in pnames and not hardware(name, result):
            raise PlanError(f"'{name}' in timeline {i} is not an input of {function}")
    lines = [f"    /* example {i} */", "    fflush(stdout);", "    pid = fork();", "    if (pid == 0) {"]
    lines += ["        " + x for x in decl + start]
    lines += ["        int bad = 0;"]
    for step, values in enumerate(tl.steps, 1):
        args = []
        for name, q, d in params:
            if name in records:
                args.append(f"&{records[name]}")
            else:
                if name not in values:
                    raise PlanError(f"timeline {i} doesn't give the input '{name}' for its steps: {tl.text}")
                args.append(f"({q}) ({_c(values[name])})")
        call = f"{function}({', '.join(args)})"
        shown = ", ".join(f"{n} = {_show(v)}" for n, v in values.items())
        lines.append(f"        if (!bad) {{")
        for name, v in values.items():
            hw = hardware(name, result)
            if hw:
                lines.append(f"            {hw} = {_c(v)};")
        if any(kind == "give" for kind, _, _ in tl.expects):
            lines.append(f"            {ret} got_back = {call};")
        else:
            lines.append(f"            {call};")
        for kind, name, want in tl.expects:
            w = want[step - 1] if step - 1 < len(want) else None
            if w is None:
                continue
            if kind == "give":
                got, label = "(double) got_back", "gives back"
            elif hardware(name, result):
                got, label = f"(double) {hardware(name, result)}", name
            elif "." in name:
                base = name.split(".")[0]
                if base not in records:
                    raise PlanError(f"'{name}' in timeline {i}: '{base}' isn't a record the function takes")
                got, label = f"(double) {records[base]}.{name.split('.', 1)[1]}", name
            elif name in shared:
                got, label = f"(double) {name}", name
            else:
                raise PlanError(f"'{name}' in timeline {i} is not something the function can change")
            lines.append(f"            if (!close_enough({got}, (double) ({_c(w)}))) {{")
            lines.append(f'                if (!bad) printf("FAIL {i}: %s\\n", {_cstr(_describe_tl(tl))});')
            namer = enum_namer.get(w[1]) if w[0] == "name" else None
            if namer:
                lines.append(f'                printf("      step {step} ({_esc(shown)}): %s: the plan says %s, '
                             f'the code gives %s\\n", {_cstr(label)}, {namer}((double) ({_c(w)})), {namer}({got}));')
            else:
                lines.append(f'                printf("      step {step} ({_esc(shown)}): %s: the plan says %.17g, '
                             f'the code gives %.17g\\n", {_cstr(label)}, (double) ({_c(w)}), {got});')
            lines.append("                bad = 1;")
            lines.append("            }")
        lines.append("        }")
    lines.append(f'        if (!bad) printf("ok   {i}: %s\\n", {_cstr(_describe_tl(tl))});')
    lines += ["        fflush(stdout);", "        _exit(bad ? 1 : 0);", "    }",
              "    waitpid(pid, &status, 0);",
              "    if (WIFSIGNALED(status)) {",
              f'        printf("FAIL {i}: %s\\n      the code crashed (signal %d)\\n", {_cstr(_describe_tl(tl))}, WTERMSIG(status));',
              "        failed++;", "    } else if (WEXITSTATUS(status) != 0) {", "        failed++;", "    }",
              "    total++;"]
    return "\n".join(lines)


def _describe_tl(tl) -> str:
    return "timeline: " + tl.text.split(":", 1)[1].strip()


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")


def hardware(name: str, result) -> Optional[str]:
    """pin[N] / millis -> the stand-in variable, for Arduino sketches only."""
    from .lang import language
    if language(result.path) != "arduino":
        return None
    m = re.fullmatch(r"pin\[(\d+)\]", name)
    if m:
        return f"plainsight_pin[{int(m.group(1))}]"
    if name == "millis":
        return "plainsight_millis"
    return None


def _cstr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _example_block(i, label, decl, setup, call, ret, checks) -> str:
    lines = [f"    /* example {i} */", "    fflush(stdout);", "    pid = fork();", "    if (pid == 0) {"]
    lines += ["        " + d for d in decl]
    lines += ["        " + s for s in setup]
    lines += ["        int saved = dup(1);", "        FILE *cap = tmpfile();", "        dup2(fileno(cap), 1);"]
    if ret and ret.strip() != "void":
        lines.append(f"        {ret} got_back = {call};")
    else:
        lines.append(f"        {call};")
    lines += ["        fflush(stdout);", "        dup2(saved, 1);", "        close(saved);",
              "        static char printed[65536];", "        rewind(cap);",
              "        size_t np = fread(printed, 1, sizeof printed - 1, cap);", "        printed[np] = 0;",
              "        (void) printed;", "        int bad = 0;"]
    detail = []
    for k, (kind, name, got, want) in enumerate(checks):
        if kind == "num":
            lines.append(f"        int bad{k} = !close_enough({got}, {want});")
            detail.append(f'        if (bad{k}) printf("      %s: the plan says %.17g, the code gives %.17g\\n", '
                          f'{_cstr(name)}, {want}, (double) {got});')
        elif kind == "text":
            lines.append(f"        int bad{k} = ({got} == NULL) || strcmp({got}, {want}) != 0;")
            detail.append(f'        if (bad{k}) {{ printf("      %s: the plan says ", {_cstr(name)}); show_text({want}); '
                          f'printf(", the code gives "); show_text({got}); printf("\\n"); }}')
        else:  # list
            n = len(want)
            lines.append(f"        double want{k}[] = {{{', '.join(f'(double) ({w})' for w in want)}}};")
            lines.append(f"        int bad{k} = 0;")
            lines.append(f"        for (int j = 0; j < {n}; j++) if (!close_enough((double) {got}[j], want{k}[j])) bad{k} = 1;")
            detail.append(f'        if (bad{k}) {{ printf("      %s: the plan says [", {_cstr(name)}); '
                          f'for (int j = 0; j < {n}; j++) printf(j ? ", %.17g" : "%.17g", want{k}[j]); '
                          f'printf("], the code gives ["); '
                          f'for (int j = 0; j < {n}; j++) printf(j ? ", %.17g" : "%.17g", (double) {got}[j]); '
                          f'printf("]\\n"); }}')
        lines.append(f"        bad += bad{k};")
    lines.append(f'        if (bad) printf("FAIL {i}: %s\\n", {_cstr(label)}); else printf("ok   {i}: %s\\n", {_cstr(label)});')
    lines += detail
    lines += ["        fflush(stdout);", "        _exit(bad ? 1 : 0);", "    }",
              "    waitpid(pid, &status, 0);",
              "    if (WIFSIGNALED(status)) {",
              f'        printf("FAIL {i}: %s\\n      the code crashed (signal %d)\\n", {_cstr(label)}, WTERMSIG(status));',
              "        failed++;",
              "    } else if (WEXITSTATUS(status) != 0) {",
              "        failed++;",
              "    }", f"    total++;"]
    return "\n".join(lines)


def _wrap(source_rel, function, blocks, helpers="") -> str:
    return f"""/* Example tests written by Plainsight for {function}. Expected results come from the PLAN.
 * Run:  gcc -std=c11 THIS_FILE.c -o examples && ./examples   (Linux/macOS; exit code = failures)
 */
#define _POSIX_C_SOURCE 200809L
#define main plainsight_original_main
#include "{source_rel}"
#undef main
#include <stdio.h>
#include <string.h>
#include <math.h>
#include <unistd.h>
#include <sys/wait.h>

/* text shown on one line, with line breaks and tabs spelled out */
static void show_text(const char *t) {{
    if (!t) {{ printf("(nothing)"); return; }}
    putchar('"');
    for (; *t; t++) {{
        if (*t == '\\n') printf("\\\\n");
        else if (*t == '\\t') printf("\\\\t");
        else if (*t == '"') printf("\\\\\\"");
        else putchar(*t);
    }}
    putchar('"');
}}

static int close_enough(double a, double b) {{
    double scale = fabs(a) > fabs(b) ? fabs(a) : fabs(b);
    return fabs(a - b) <= 1e-9 * (scale > 1 ? scale : 1);
}}

{helpers}
int main(void) {{
    int failed = 0, total = 0, status = 0;
    pid_t pid;
    setvbuf(stdout, NULL, _IONBF, 0);
{chr(10).join(blocks)}
    printf("\\n%d of %d examples passed.\\n", total - failed, total);
    return failed;
}}
"""


def explain_compile_error(program: str, stderr: str, examples: List[Example]) -> str:
    """Turn the compiler's first error into: which example, which input."""
    lines = program.splitlines()
    for err in stderr.splitlines():
        m = re.match(r".*?:(\d+):\d+: error: (.*)", err)
        if not m:
            continue
        line_no, msg = int(m.group(1)), m.group(2)
        if line_no > len(lines):
            continue
        idx = None
        for k in range(line_no - 1, -1, -1):
            mm = re.match(r"\s*/\* example (\d+) \*/", lines[k])
            if mm:
                idx = int(mm.group(1))
                break
        if idx is None:
            continue
        code_line = lines[line_no - 1]
        name = re.search(r"\b([A-Za-z_]\w*)_(arr|txt|one|rec)\b", code_line)
        ex = examples[idx - 1]
        if name:
            given = dict(ex.inputs).get(name.group(1))
            shown = _show(given) if given is not None else "?"
            return (f"example {idx}: the value {name.group(1)} = {shown} doesn't fit that input "
                    f"(the compiler says: {msg}) -- in: {ex.text}")
        return f"example {idx}: a value doesn't fit (the compiler says: {msg}) -- in: {ex.text}"
    first = next((l for l in stderr.splitlines() if "error" in l), stderr.strip()[-300:])
    return "the examples didn't compile: " + first.split("error:", 1)[-1].strip()


def _show(v) -> str:
    if isinstance(v, list):
        return "[" + ", ".join(_show(x) for x in v) + "]"
    if v[0] == "bool":
        return "true" if v[1] == "1" else "false"
    return f'"{v[1]}"' if v[0] == "text" else v[1]


# ------------------------------------------------------------------ running

@dataclass
class ExampleResult:
    passed: int = 0
    total: int = 0
    failures: List[str] = field(default_factory=list)


def run_examples(result, source_path: str, function: str, examples: List[Example],
                 extra_args: Optional[List[str]] = None) -> ExampleResult:
    gcc = shutil.which("gcc") or shutil.which("clang")
    if not gcc:
        raise PlanError("gcc (or clang) is needed to run examples")
    with tempfile.TemporaryDirectory() as tmp:
        src, exe = os.path.join(tmp, "examples.c"), os.path.join(tmp, "examples")
        program = c_program(result, function, examples, os.path.abspath(source_path))
        with open(src, "w") as fh:
            fh.write(program)
        from .lang import compiler
        r = subprocess.run([*compiler(source_path), "-w", *(extra_args or []), src, "-o", exe, "-lm"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise PlanError(explain_compile_error(program, r.stderr, examples))
        run = subprocess.run([exe], capture_output=True, text=True, timeout=120)
    out = ExampleResult()
    block = None
    for line in run.stdout.splitlines():
        if line.startswith(("ok   ", "FAIL ")):
            out.total += 1
            if line.startswith("ok"):
                out.passed += 1
                block = None
            else:
                block = [line[5:].split(": ", 1)[1] if ": " in line else line]
                out.failures.append(block)
        elif block is not None and line.startswith("      "):
            block.append(line.strip())
    out.failures = [f"Example: {b[0]}\n  " + "\n  ".join(b[1:]) for b in out.failures]
    return out
