"""M1 checks. Run: python3 -m pytest tests/ -q  (from the plainsight folder)"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from plainsight.lift import parse  # noqa: E402
from plainsight.render import render  # noqa: E402
from expected_facts import EXPECTED  # noqa: E402

C_FILES = sorted(GOLDEN.glob("*.c"))


def run_cli(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "plainsight", *args], capture_output=True,
                          text=True, cwd=cwd or ROOT,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


def test_at_least_30_examples():
    assert len(C_FILES) >= 30


@pytest.mark.skipif(not shutil.which("gcc"), reason="gcc not installed")
@pytest.mark.parametrize("path", C_FILES, ids=lambda p: p.stem)
def test_golden_compiles(path):
    r = subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", str(path)],
                       capture_output=True, text=True)
    assert r.returncode == 0 and r.stderr == "", r.stderr


@pytest.mark.parametrize("path", C_FILES, ids=lambda p: p.stem)
def test_golden_english(path):
    expected_file = path.with_suffix(".txt")
    assert expected_file.exists(), f"no reviewed English for {path.name}"
    got = render(parse(str(path)))
    assert got == expected_file.read_text()


GRAPH_FILES = sorted(GOLDEN.glob("*.graph.mmd"))


@pytest.mark.parametrize("expected_file", GRAPH_FILES, ids=lambda p: p.name.split(".")[0])
def test_golden_graph(expected_file):
    from plainsight.graph import graphs_for, to_mermaid
    source = GOLDEN / (expected_file.name.split(".")[0] + ".c")
    got = "\n".join(to_mermaid(g) for g in graphs_for(parse(str(source))))
    assert got == expected_file.read_text()


@pytest.mark.parametrize("path", C_FILES, ids=lambda p: p.stem)
def test_graph_structure(path):
    """Every flowchart: one start, every box reachable from it, and only end
    boxes have no way out. Every diagram must be drawable by Graphviz."""
    from plainsight.graph import graphs_for, to_dot
    for g in graphs_for(parse(str(path))):
        if g.kind == "flow":
            starts = [n for n, _, shape in g.nodes if shape == "start"]
            assert len(starts) == 1, g.title
            out = {}
            for a, b, _ in g.edges:
                out.setdefault(a, []).append(b)
            seen, stack = set(), [starts[0]]
            while stack:
                x = stack.pop()
                if x not in seen:
                    seen.add(x)
                    stack.extend(out.get(x, []))
            for nid, label, shape in g.nodes:
                assert nid in seen, f"{g.title}: unreachable box {label!r}"
                if shape == "end":
                    assert nid not in out, f"{g.title}: an end box leads somewhere"
                else:
                    assert nid in out, f"{g.title}: dead end at {label!r}"
        if shutil.which("dot"):
            r = subprocess.run(["dot", "-Tsvg", "-o", os.devnull], input=to_dot(g),
                               capture_output=True, text=True)
            assert r.returncode == 0, r.stderr


def test_golden_master():
    from plainsight.master import build, to_mermaid
    got = to_mermaid(build(parse(str(GOLDEN / "52_tank_sim.c")), "52_tank_sim.c"))
    assert got == (GOLDEN / "52_tank_sim.master.mmd").read_text()


@pytest.mark.parametrize("path", C_FILES, ids=lambda p: p.stem)
def test_master_arrows_match_facts(path):
    """Every 'runs' arrow is a call the facts know about, and every call the
    facts know about has its arrow. The chart must be drawable."""
    from plainsight.master import build, to_dot
    result = parse(str(path))
    m = build(result, path.name)
    owner = {nid: nid.split("__")[0] for nid, *_ in m.nodes}
    drawn = {(owner.get(src, src), dst.split("__")[0]) for src, dst, _, style in m.edges if style == "dashed"}
    known = {(f, c) for f, x in result.facts.items() for c in x["calls_local"]}
    assert drawn == known
    if shutil.which("dot"):
        r = subprocess.run(["dot", "-Tsvg", "-o", os.devnull], input=to_dot(m), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_master_overview_for_big_programs(tmp_path):
    from plainsight.master import build, MAX_FUNCTIONS
    n = MAX_FUNCTIONS + 8
    src = "int shared = 0;\n" + f"int f{n - 1}(int x) {{ shared = x; return x; }}\n"
    src += "".join(f"int f{i}(int x) {{ return f{i + 1}(x) + 1; }}\n" for i in reversed(range(n - 1)))
    big = tmp_path / "big.c"
    big.write_text(src)
    m = build(parse(str(big)), "big.c")
    assert m.overview and len([x for x in m.nodes if x[2] != "note"]) == n
    legend = [x for x in m.nodes if x[0] == "shared_legend"][0]
    assert f"shared: changed by f{n - 1}" in legend[1]


SUMMARY_FILES = sorted(GOLDEN.glob("*.summary.txt"))


@pytest.mark.parametrize("expected_file", SUMMARY_FILES, ids=lambda p: p.name.split(".")[0])
def test_golden_summary(expected_file):
    from plainsight.summarize import summarize
    source = GOLDEN / (expected_file.name.split(".")[0] + ".c")
    assert summarize(parse(str(source))) == expected_file.read_text()


def test_summary_mode_table_and_fallback():
    from plainsight.summarize import summarize
    pump = summarize(parse(str(GOLDEN / "49_pump.c")))
    assert "  In FAULT:\n        - nothing ever changes; it stays in FAULT" in pump
    assert "then goes to FAULT if faults is at least 3 (MAX_FAULTS), otherwise to IDLE" in pump
    assert "(exact step)" not in pump                      # every part covered by a rule
    rules = summarize(parse(str(GOLDEN / "47_switch_rules.c")))
    assert "(exact step) Checks the value of code:" in rules  # fall-through: no rule, exact steps


@pytest.mark.parametrize("stem", sorted(EXPECTED), ids=str)
def test_expected_facts(stem):
    facts = parse(str(GOLDEN / f"{stem}.c")).facts
    for fn, want in EXPECTED[stem].items():
        assert fn in facts, f"{fn} missing"
        got = facts[fn]
        for key, value in want.items():
            if key == "through":
                actual = [(t["pointer"], t["origin"], t["certainty"])
                          for t in got["writes_through_pointers"]]
            elif key == "state":
                actual = got["keeps_state_between_calls"]
            elif key == "local":
                actual = got["calls_local"]
            elif key == "external":
                actual = got["calls_external"]
            elif key == "returns":
                actual = got["return_statements"]
            elif key == "unsupported":
                actual = len(got["not_yet_supported"])
            else:
                actual = got[key]
            assert actual == value, f"{fn}.{key}: expected {value!r}, got {actual!r}"


# ---- exactness rules, checked directly (independent of the golden files)

def english(stem):
    return render(parse(str(GOLDEN / f"{stem}.c")))


def test_grouping_is_explicit():
    out = english("02_precedence")
    assert "a plus (b times c)" in out
    assert "(a plus b) times c" in out


def test_integer_division_and_remainder_named():
    out = english("04_int_division")
    assert "the whole-number part of a divided by b" in out
    assert "the remainder of a divided by b" in out
    assert "starting at x divided by y." in out  # fractional division stays plain


def test_repeat_only_when_proven():
    assert "Repeats n times" in english("11_repeat_simple")
    assert "Repeats up to n times" in english("12_repeat_with_break")
    for stem in ("13_for_step2", "14_for_modifies_bound", "15_for_global_bound_with_call"):
        out = english(stem)
        assert "Repeats" not in out and "Loop:" in out, stem


def test_nonzero_conditions_spelled_out():
    assert "If use_q is not zero:" in english("20_pointer_param_reassigned")


# ---- command line

def test_cli_missing_file():
    r = run_cli("no_such_file.c")
    assert r.returncode == 1
    assert "no_such_file.c does not exist" in r.stderr
    assert r.stdout == ""


def test_cli_broken_c(tmp_path):
    bad = tmp_path / "bad.c"
    bad.write_text("int f( { return 1 }\n")
    r = run_cli(str(bad))
    assert r.returncode == 2
    assert "does not compile" in r.stderr
    assert r.stdout == ""


def test_cli_unsupported_shown_and_strict():
    path = str(GOLDEN / "46_macro_facts.c")
    r = run_cli(path)
    assert r.returncode == 0
    assert "[Not yet supported: call through a function pointer (line 25)]" in r.stdout
    assert run_cli(path, "--strict").returncode == 3
    assert run_cli(str(GOLDEN / "01_add_two.c"), "--strict").returncode == 0


def test_cli_json():
    r = run_cli(str(GOLDEN / "17_global_write.c"), "--json")
    assert r.returncode == 0
    data = json.loads(r.stdout)
    assert data["functions"]["bump"]["writes_globals"] == ["counter"]
    assert "Sets counter to counter plus by." in data["english"]


def test_cli_help_has_example():
    r = run_cli("--help")
    assert r.returncode == 0 and "example:" in r.stdout


def test_runs_from_another_directory(tmp_path):
    r = run_cli(str(GOLDEN / "01_add_two.c"), cwd=tmp_path)
    assert r.returncode == 0 and "Function add" in r.stdout
