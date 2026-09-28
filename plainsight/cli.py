"""plainsight FILE.c [--facts] [--json] [--strict]"""
import argparse
import json
import os
import sys

EXAMPLE = """example:
  python3 -m plainsight blink.c            plain-English steps for every function
  python3 -m plainsight blink.c --facts    ...plus what each function reads, changes and calls
  python3 -m plainsight blink.c --summary  what each function does as a whole, in plain words
  python3 -m plainsight blink.c --graph-out diagrams   a picture of each function (state diagram or flowchart)
  python3 -m plainsight blink.c --master chart         ONE picture of the whole program

for AI agents (no API keys; the agent writes the plan and the code itself):
  plainsight agent-guide                        the workflow, step by step
  plainsight guide                              the plan language
  plainsight names blink.c                      names a plan may use
  plainsight plan-check plan.mmd --code blink.c is the plan usable? + picture + warnings
  plainsight approve plan.mmd --by "Jaden"      record a person's yes
  plainsight check blink.c plan.mmd --make-tests tests.c   does the code do what the plan says?
  plainsight snapshot blink.c -o plans          lock what the code does now, as plans to approve
  plainsight diff old.c new.c --pictures pics   what an edit changed in behaviour (red/green pictures)
  plainsight strength blink.c plan.mmd          which planted bugs the plan would miss
  plainsight doctor                             what this sandbox needs installed
  python3 -m plainsight blink.c --json     everything as one JSON object (for other programs)

exit codes: 0 ok, 1 file missing, 2 C does not compile, 3 --strict and something is not yet supported"""


DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")


def _parse_code(path):
    from .lift import parse, CompileError
    if not os.path.isfile(path):
        print(f"error: {path} does not exist", file=sys.stderr)
        return None, 1
    try:
        return parse(os.path.abspath(path)), 0
    except CompileError as e:
        print(f"error: {path} does not compile: {e}", file=sys.stderr)
        return None, 2


def guide_main(argv) -> int:
    """plainsight guide / plainsight agent-guide: print a bundled document."""
    name = "AGENTS.md" if argv and argv[0] == "agent" else "PLAN_GUIDE.md"
    sys.stdout.write(open(os.path.join(DOCS, name)).read())
    return 0


def names_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="plainsight names",
                                 description="The exact names a plan for this code may use.")
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result, code = _parse_code(args.file)
    if result is None:
        return code
    from .planfile import names_from_code
    text, consts = names_from_code(result)
    if args.json:
        print(json.dumps({"names": text.splitlines(), "constants": consts}, indent=2))
    else:
        print(text)
    return 0


def plan_check_main(argv) -> int:
    ap = argparse.ArgumentParser(
        prog="plainsight plan-check",
        description="Is this plan usable? Parses it, checks it fits the code, warns about impossible "
                    "modes, and draws it as a picture for a person to approve.")
    ap.add_argument("plan")
    ap.add_argument("--code", metavar="FILE.c", help="the code the plan is for (its names must match)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.plan):
        print(f"error: {args.plan} does not exist", file=sys.stderr)
        return 1
    result = None
    if args.code:
        result, code = _parse_code(args.code)
        if result is None:
            return code
    from .planfile import validate, plan_to_dot, approval
    plan, problems, warnings = validate(open(args.plan).read(), result)
    picture = None
    if plan is not None and plan.kind != "examples":  # examples are read as text, not as a picture
        import shutil
        import subprocess
        dot = shutil.which("dot")
        if dot:
            picture = os.path.splitext(args.plan)[0] + ".png"
            r = subprocess.run([dot, "-Tpng", "-Gdpi=110", "-o", picture], input=plan_to_dot(plan),
                               capture_output=True, text=True)
            picture = picture if r.returncode == 0 else None
    status = approval(args.plan)["state"]
    if args.json:
        print(json.dumps({"usable": not problems, "problems": problems, "warnings": warnings,
                          "picture": picture, "approval": status}, indent=2))
    else:
        if problems:
            print("NOT USABLE -- fix the plan:")
            for p_ in problems:
                print(f"  - {p_}")
        else:
            print(f"USABLE ({plan.kind} plan" + (f" for {plan.function}" if plan.function else "") + ")")
        for w in warnings:
            print(f"PLAN WARNING: {w}")
        if picture:
            print(f"picture: {picture} -- show it to a person; after they say yes: "
                  f"plainsight approve {args.plan} --by \"their name\"")
        print(f"approval: {status}")
    return 2 if problems else 0


def approve_main(argv) -> int:
    ap = argparse.ArgumentParser(
        prog="plainsight approve",
        description="Record that a person looked at this exact plan (its picture) and said yes. "
                    "Checks against the plan warn if it changes afterwards.")
    ap.add_argument("plan")
    ap.add_argument("--by", required=True, metavar="NAME", help="the person who approved it")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.plan):
        print(f"error: {args.plan} does not exist", file=sys.stderr)
        return 1
    from .plan import read_plan, PlanError
    try:
        if args.plan.endswith(".lock"):
            from .snapshot import load_lock
            load_lock(args.plan)
        else:
            read_plan(open(args.plan).read(), {})
    except (PlanError, OSError, ValueError) as e:
        print(f"error: the plan isn't usable, so it can't be approved: {e}", file=sys.stderr)
        return 2
    from .planfile import approve
    stamp = approve(args.plan, args.by)
    print(f"approved: {args.plan} by {stamp['approved_by']} at {stamp['approved_at']} "
          f"(stamp in {args.plan}.approved; any later change to the plan will be flagged)")
    return 0


def _check_lock(args, result) -> int:
    from .snapshot import load_lock, check_lock, _NotComparable
    from .planfile import approval
    if not os.path.isfile(args.check):
        print(f"error: {args.check} does not exist", file=sys.stderr)
        return 1
    try:
        lock = load_lock(args.check)
        changes = check_lock(result, result.path, lock,
                             [f"-I{d}" for d in args.includes] + [f"-D{d}" for d in args.defines])
    except (OSError, ValueError) as e:
        print(f"error: {args.check} isn't a Plainsight lock file ({e})", file=sys.stderr)
        return 2
    except _NotComparable as e:
        print(f"error: {lock['function']} can't be checked against the lock: {e}", file=sys.stderr)
        return 2
    status = approval(args.check)
    if args.json:
        print(json.dumps({"function": lock["function"], "result": "same" if not changes else "changed",
                          "cases_tested": len(lock["cases"]), "changes": changes, "approval": status}, indent=2))
    else:
        print(f"Checking {lock['function']} against its lock ({lock['source']}, {lock['created']})")
        if not changes:
            print(f"SAME: all {len(lock['cases'])} recorded cases behave exactly as locked.")
        else:
            print(f"CHANGED: {len(changes)} kind(s) of behaviour change.\n")
            for k, c in enumerate(changes, 1):
                print(f"{k}. {c}\n")
        if status["state"] != "approved":
            print("WARNING: no person has approved this lock yet." if status["state"] == "not approved"
                  else "WARNING: the lock file changed after it was approved.")
    return 0 if not changes else 4


def check_main(argv) -> int:
    """plainsight check FILE.c PLAN [options]  ==  plainsight FILE.c --check PLAN [options]"""
    if len(argv) < 2 or argv[0].startswith("-") or argv[1].startswith("-"):
        print("usage: plainsight check FILE.c PLAN [--make-tests OUT.c] [--json] [-I DIR] [-D NAME]",
              file=sys.stderr)
        return 1
    return _main([argv[0], "--check", argv[1]] + argv[2:])


def snapshot_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="plainsight snapshot",
                                 description="Write what the code does NOW as plans a person can approve "
                                             "(then later edits are checked against them).")
    ap.add_argument("file")
    ap.add_argument("-o", "--out", required=True, metavar="FOLDER",
                    help="folder for the locks (one per function) and any mode diagrams")
    ap.add_argument("--function", metavar="NAME", help="only this function")
    args = ap.parse_args(argv)
    result, code = _parse_code(args.file)
    if result is None:
        return code
    from .snapshot import snapshot, save_lock
    made, skipped = snapshot(result, result.path, args.function)
    os.makedirs(args.out, exist_ok=True)
    for name, lock, diagram in made:
        path = os.path.join(args.out, name + ".lock")
        save_lock(lock, path)
        print(f"wrote {path} (every tested case and its result: {len(lock['cases'])} cases)")
        if diagram:
            dpath = os.path.join(args.out, name + ".mmd")
            with open(dpath, "w") as fh:
                fh.write(diagram)
            print(f"wrote {dpath} (the mode diagram a person can look at and approve)")
    for name, why in skipped:
        print(f"not locked: {name} -- {why}")
    if made:
        print("Next: show the pictures (plainsight FILE.c --master chart) to a person, approve the locks "
              "(plainsight approve NAME.lock --by NAME), then after every edit: plainsight check FILE.c NAME.lock")
    return 0 if made else 2


def diff_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="plainsight diff",
                                 description="What an edit changed in behaviour: old version vs new version.")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--function", metavar="NAME", help="only this function")
    ap.add_argument("--pictures", metavar="FOLDER", help="write a picture of each changed mode machine")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    for f in (args.old, args.new):
        r, code = _parse_code(f)
        if r is None:
            return code
    from .snapshot import diff, format_diff
    diffs = diff(os.path.abspath(args.old), os.path.abspath(args.new), args.function)
    pictures = {}
    if args.pictures:
        import shutil
        import subprocess
        dot = shutil.which("dot")
        os.makedirs(args.pictures, exist_ok=True)
        for d in diffs:
            if d.picture_dot and d.status == "changed" and dot:
                path = os.path.join(args.pictures, f"{d.name}_diff.png")
                subprocess.run([dot, "-Tpng", "-Gdpi=110", "-o", path], input=d.picture_dot, text=True, check=True)
                pictures[d.name] = path
    if args.json:
        print(json.dumps([{"function": d.name, "status": d.status, "changes": d.changes, "effects": d.effects,
                           "note": d.note, "picture": pictures.get(d.name)} for d in diffs], indent=2))
    else:
        sys.stdout.write(format_diff(diffs, os.path.basename(args.old), os.path.basename(args.new)))
        for n, pth in pictures.items():
            print(f"picture of the changes in {n}: {pth}")
    return 4 if any(d.status == "changed" for d in diffs) else 0


def strength_main(argv) -> int:
    ap = argparse.ArgumentParser(prog="plainsight strength",
                                 description="Plant small bugs in the code and report which ones the plan "
                                             "(or lock) would NOT notice, with what to add.")
    ap.add_argument("file")
    ap.add_argument("plan", help="a plan (.mmd / .plan) or a lock (.lock)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    result, code = _parse_code(args.file)
    if result is None:
        return code
    from .strength import measure, format_strength
    lock = plan = None
    try:
        if args.plan.endswith(".lock"):
            from .snapshot import load_lock
            lock = load_lock(args.plan)
        else:
            from .plan import read_plan
            from .planfile import names_from_code
            plan = read_plan(open(args.plan).read(), names_from_code(result)[1])
    except Exception as e:
        print(f"error: can't read {args.plan}: {e}", file=sys.stderr)
        return 2
    st = measure(result, result.path, plan, lock)
    if args.json:
        print(json.dumps({"function": st.function, "noticed": st.caught, "planted": st.total,
                          "score_percent": round(st.score, 1),
                          "not_noticed": [{"line": m.line, "what": m.what, "suggestion": m.suggestion}
                                          for m in st.survivors]}, indent=2))
    else:
        sys.stdout.write(format_strength(st, os.path.basename(args.plan)))
    return 0


def doctor_main(argv) -> int:
    """What this sandbox has, and what to install."""
    import shutil
    import subprocess
    import tempfile
    ok = True
    rows = []
    clang = os.environ.get("PLAINSIGHT_CLANG") or shutil.which("clang")
    if clang:
        with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as fh:
            fh.write("int f(void) { return 1; }\n")
        r = subprocess.run([clang, "-fsyntax-only", "-Xclang", "-ast-dump=json", fh.name],
                           capture_output=True, text=True)
        os.unlink(fh.name)
        good = r.returncode == 0 and r.stdout.lstrip().startswith("{")
        rows.append(("clang (reads C)", "ok" if good else "found, but its syntax-tree output failed"))
        ok &= good
    else:
        rows.append(("clang (reads C)", "MISSING -- needed for everything"))
        ok = False
    gcc = shutil.which("gcc") or shutil.which("cc")
    rows.append(("gcc (runs checks, examples, tests)", "ok" if gcc else "MISSING -- needed for --check"))
    ok &= bool(gcc)
    rows.append(("graphviz dot (pictures)", "ok" if shutil.which("dot") else "missing -- pictures skipped"))
    rows.append(("python", sys.version.split()[0]))
    for name, state in rows:
        print(f"{name:38s} {state}")
    if not ok or not shutil.which("dot"):
        print("\ninstall (Debian/Ubuntu/most sandboxes):  apt-get install -y clang gcc graphviz")
        print("install (macOS):                         brew install llvm graphviz  (gcc: xcode-select --install)")
    return 0 if ok else 1


def main(argv=None) -> int:
    try:
        return _main(argv)
    except BrokenPipeError:
        # the reader stopped early (e.g. `| head`): not an error worth a traceback
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0


def _main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    commands = {"guide": lambda a: guide_main([]), "agent-guide": lambda a: guide_main(["agent"]),
                "names": names_main, "plan-check": plan_check_main, "approve": approve_main,
                "doctor": doctor_main, "check": check_main, "snapshot": snapshot_main, "diff": diff_main,
                "strength": strength_main}
    if argv and argv[0] in commands:
        return commands[argv[0]](argv[1:])
    ap = argparse.ArgumentParser(
        prog="plainsight",
        description="Describe what a C file does in exact plain English, with facts about each function.",
        epilog=EXAMPLE, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="the C source file to describe")
    ap.add_argument("--facts", action="store_true", help="add facts under each function")
    ap.add_argument("--json", action="store_true", help="print one JSON object instead of text")
    ap.add_argument("--summary", action="store_true",
                    help="what each function does as a whole (rule-based; exact steps where no rule fits)")
    ap.add_argument("--graph", action="store_true",
                    help="print a diagram of each function as Mermaid text (readable by people, AI and GitHub)")
    ap.add_argument("--graph-out", metavar="FOLDER",
                    help="write each function's diagram to FOLDER as .mmd, .dot, .svg and .png "
                         "(images need Graphviz: sudo apt install graphviz)")
    ap.add_argument("--master", metavar="FOLDER",
                    help="write ONE chart of the whole program to FOLDER (master.mmd/.dot/.svg/.png)")
    ap.add_argument("--check", metavar="PLAN",
                    help="check the code against an approved plan (a mode diagram, .mmd) by running both "
                         "on the same inputs; exit code 4 if they differ")
    ap.add_argument("--make-tests", metavar="OUT.c",
                    help="with --check: also write a standalone C test file from the plan "
                         "(one test per plan line, plus the boundaries); needs only gcc to run")
    ap.add_argument("--strict", action="store_true",
                    help="exit with code 3 if any part is not yet supported")
    ap.add_argument("-I", dest="includes", action="append", default=[],
                    help="extra include folder for the C code (repeatable)")
    ap.add_argument("-D", dest="defines", action="append", default=[],
                    help="define a macro for the C code, e.g. -D _DEFAULT_SOURCE (repeatable)")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.file):
        print(f"error: {args.file} does not exist", file=sys.stderr)
        return 1

    try:
        from .lift import parse, CompileError
        from .render import render
    except ImportError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        result = parse(os.path.abspath(args.file),
                       [f"-I{d}" for d in args.includes] + [f"-D{d}" for d in args.defines])
    except CompileError as e:
        print(f"error: {args.file} does not compile: {e}", file=sys.stderr)
        return 2

    if args.check and args.check.endswith(".lock"):
        return _check_lock(args, result)

    if args.check:
        from .plan import read_plan, PlanError
        from .check import check, format_report
        from .harness import HarnessError
        if not os.path.isfile(args.check):
            print(f"error: {args.check} does not exist", file=sys.stderr)
            return 1
        from .dictum_bridge import A as _A
        macros = {}
        for item in result.items:
            if isinstance(item, _A.Action):
                for k, v in getattr(item, "macros", {}).items():
                    try:
                        macros[k] = int(v.rstrip("uUlL"), 0) if v else None
                    except (ValueError, AttributeError):
                        try:
                            macros[k] = float(v.rstrip("fF"))
                        except (ValueError, AttributeError):
                            pass
                break
        try:
            plan = read_plan(open(args.check).read(), {k: v for k, v in macros.items() if v is not None})
            rep = check(result, result.path, plan,
                        [f"-I{d}" for d in args.includes] + [f"-D{d}" for d in args.defines])
        except (PlanError, HarnessError) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        from .check import add_plan_warnings
        from .planfile import approval
        add_plan_warnings(rep, plan)
        status = approval(args.check)
        tests_written = None
        if args.make_tests:
            from .maketests import make_tests
            n = make_tests(result, result.path, plan, args.make_tests)
            tests_written = {"file": args.make_tests, "count": n}
        if args.json:
            ex = getattr(rep, "examples", None)
            print(json.dumps({
                "function": rep.function, "result": "matches" if rep.ok else "different",
                "cases_tested": rep.cases, "problems": rep.problems, "not_checked": rep.not_checked,
                "plan_warnings": rep.plan_warnings, "approval": status,
                "examples": {"passed": ex.passed, "total": ex.total} if ex else None,
                "tests_written": tests_written}, indent=2))
        else:
            sys.stdout.write(format_report(rep, os.path.basename(args.check)))
            if status["state"] == "approved":
                print(f"Plan approved by {status['approved_by']} at {status['approved_at']}.")
            elif status["state"] == "changed since approval":
                print(f"WARNING: the plan CHANGED after {status['approved_by']} approved it -- "
                      f"show the new picture and approve again (plainsight approve {args.check} --by NAME).")
            else:
                print(f"WARNING: no person has approved this plan yet "
                      f"(plainsight plan-check {args.check}, show the picture, then plainsight approve).")
            if tests_written:
                print(f"wrote {tests_written['count']} plan tests to {args.make_tests} "
                      f"(run: gcc -std=c11 {args.make_tests} -o plan_tests -lm && ./plan_tests)", file=sys.stderr)
        return 0 if rep.ok else 4

    if args.master:
        import shutil
        import subprocess
        from .master import build, to_dot as master_dot, to_mermaid as master_mermaid
        m = build(result, os.path.basename(args.file))
        os.makedirs(args.master, exist_ok=True)
        stem = os.path.join(args.master, "master")
        with open(stem + ".mmd", "w") as fh:
            fh.write(master_mermaid(m))
        with open(stem + ".dot", "w") as fh:
            fh.write(master_dot(m))
        dot = shutil.which("dot")
        if dot:
            for fmt in ("svg", "png"):
                r = subprocess.run([dot, f"-T{fmt}", "-Gdpi=110", stem + ".dot", "-o", f"{stem}.{fmt}"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    print(f"error: could not draw the master chart: {r.stderr.strip()}", file=sys.stderr)
                    return 2
        kind = "overview (too big for one detailed chart)" if m.overview else "detailed chart"
        print(f"wrote master {kind} to {args.master}" + ("" if dot else " (text only: install Graphviz for images)"),
              file=sys.stderr)
        if not (args.graph or args.graph_out):
            return 3 if args.strict and result.coverage["not_yet_supported"] else 0

    if args.graph or args.graph_out:
        from .graph import graphs_for, to_mermaid, to_dot
        graphs = graphs_for(result)
        if args.graph:
            sys.stdout.write("\n".join(to_mermaid(g) for g in graphs))
        if args.graph_out:
            import shutil
            import subprocess
            os.makedirs(args.graph_out, exist_ok=True)
            dot = shutil.which("dot")
            for g in graphs:
                stem = os.path.join(args.graph_out, g.title.split(":")[0].split()[0])
                with open(stem + ".mmd", "w") as fh:
                    fh.write(to_mermaid(g))
                with open(stem + ".dot", "w") as fh:
                    fh.write(to_dot(g))
                if dot:
                    for fmt in ("svg", "png"):
                        r = subprocess.run([dot, f"-T{fmt}", "-Gdpi=110", stem + ".dot", "-o", f"{stem}.{fmt}"],
                                           capture_output=True, text=True)
                        if r.returncode != 0:
                            print(f"error: could not draw {stem}: {r.stderr.strip()}", file=sys.stderr)
                            return 2
            print(f"wrote {len(graphs)} diagram(s) to {args.graph_out}"
                  + ("" if dot else " (text only: install Graphviz for images)"), file=sys.stderr)
        return 3 if args.strict and result.coverage["not_yet_supported"] else 0

    if args.summary and not args.json:
        from .summarize import summarize
        sys.stdout.write(summarize(result))
        return 3 if args.strict and result.coverage["not_yet_supported"] else 0

    if args.json:
        from .summarize import summarize
        payload = {"file": args.file, "english": render(result, with_facts=False),
                   "summary": summarize(result),
                   "functions": result.facts, "coverage": result.coverage}
        print(json.dumps(payload, indent=2))
    else:
        sys.stdout.write(render(result, with_facts=args.facts))

    if args.strict and result.coverage["not_yet_supported"]:
        return 3
    return 0
