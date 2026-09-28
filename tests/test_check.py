"""The chart-first loop: code checked against an approved plan, by running both.

Guards both ways: every planted mistake must be caught (with a concrete
example), and correct code written differently must NOT be flagged."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plainsight.lift import parse  # noqa: E402
from plainsight.check import check, format_report  # noqa: E402
from plainsight.plan import read_plan, PlanError  # noqa: E402
from plainsight.graph import state_diagram, to_mermaid  # noqa: E402
from plainsight.dictum_bridge import A  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("gcc"), reason="gcc is needed to run the code")
GOLDEN = ROOT / "tests" / "golden"
CHECK = ROOT / "tests" / "check"
PUMP = GOLDEN / "49_pump.c"
PLAN = (CHECK / "pump_plan.mmd").read_text()
NAMES = {"LOW_LEVEL": 20, "HIGH_LEVEL": 90, "MAX_FAULTS": 3}


def run(path, plan_text=PLAN):
    return check(parse(str(path)), str(path), read_plan(plan_text, NAMES))


def test_correct_code_matches():
    rep = run(PUMP)
    assert rep.ok and rep.cases > 1000


def test_same_behaviour_written_differently_matches():
    """if/else instead of switch, `>= 3` instead of `> 2`: no false alarm."""
    rep = run(CHECK / "pump_alt.c")
    assert rep.ok, format_report(rep, "plan")


MISTAKES = [
    ("level_off_by_one", "} else if (level >= HIGH_LEVEL) {", "} else if (level > HIGH_LEVEL) {",
     "In PUMPING, with faults = 0, run_minutes = 0, level = 90, flowing = no:\n"
     "  the plan says it goes to IDLE; the code stays in PUMPING."),
    ("timer_not_reset", "                p->state = PUMPING;\n                p->run_minutes = 0;",
     "                p->state = PUMPING;",
     "the plan says run_minutes ends at 0; the code gives 1."),
    ("fault_too_early", "if (!flowing && p->run_minutes > 2) {", "if (!flowing && p->run_minutes >= 2) {",
     "In PUMPING, with faults = 2, run_minutes = 1"),
    ("faults_wrong_way", "p->state = p->faults >= MAX_FAULTS ? FAULT : IDLE;",
     "p->state = p->faults > MAX_FAULTS ? FAULT : IDLE;",
     "the plan says it goes to FAULT; the code goes to IDLE."),
    ("fault_recovers", "        case FAULT:\n            break;",
     "        case FAULT:\n            if (level < 10) {\n                p->state = IDLE;\n            }\n            break;",
     "In FAULT, with faults = 0, run_minutes = 0, level = 0, flowing = no:\n"
     "  the plan says it stays in FAULT; the code goes to IDLE."),
]


@pytest.mark.parametrize("name,old,new,expected", MISTAKES, ids=[m[0] for m in MISTAKES])
def test_planted_mistake_is_caught(tmp_path, name, old, new, expected):
    src = PUMP.read_text()
    assert old in src
    bad = tmp_path / f"{name}.c"
    bad.write_text(src.replace(old, new, 1))
    rep = run(bad)
    assert not rep.ok
    assert expected in "\n".join(rep.problems), format_report(rep, "plan")


def test_timer_counted_after_checks_is_caught(tmp_path):
    """Order mistake, and the code no longer has the plan's shape -- still tested."""
    src = PUMP.read_text()
    code = src.replace("            p->run_minutes++;\n            if (!flowing", "            if (!flowing", 1)
    code = code.replace("            break;\n        case FAULT:", "            p->run_minutes++;\n            break;\n        case FAULT:", 1)
    bad = tmp_path / "late_timer.c"
    bad.write_text(code)
    rep = run(bad)
    assert not rep.ok and "the plan says it goes to FAULT; the code stays in PUMPING." in "\n".join(rep.problems)


def test_difference_only_next_to_a_number_is_caught(tmp_path):
    """Plan: 'over 90'. Code: '>= 92'. They disagree ONLY at 91 -- a value next
    to the numbers, not one of them -- so the neighbours must be tested too."""
    plan = PLAN.replace("otherwise, if level is at least 90", "otherwise, if level is over 90")
    bad = tmp_path / "gap.c"
    bad.write_text(PUMP.read_text().replace("level >= HIGH_LEVEL", "level >= HIGH_LEVEL + 2"))
    rep = run(bad, plan)
    assert not rep.ok
    assert "level = 91" in "\n".join(rep.problems), format_report(rep, "plan")


@pytest.mark.parametrize("stem,fn", [("49_pump", "pump_step"), ("51_crossing", "crossing_tick"),
                                     ("53_calls_everywhere", "machine_step")])
def test_own_diagram_round_trips(stem, fn):
    """A diagram Plainsight drew from code, used as the plan, matches that code."""
    path = GOLDEN / f"{stem}.c"
    r = parse(str(path))
    a = [x for x in r.items if isinstance(x, A.Action) and x.name == fn][0]
    text = f"%% function: {fn}\n" + to_mermaid(state_diagram(a, r.facts[fn]))
    rep = check(r, str(path), read_plan(text, {}))
    assert rep.ok, format_report(rep, "own diagram")


def test_plan_naming_a_call_the_code_never_makes():
    path = GOLDEN / "53_calls_everywhere.c"
    r = parse(str(path))
    a = [x for x in r.items if isinstance(x, A.Action) and x.name == "machine_step"][0]
    text = f"%% function: machine_step\n" + to_mermaid(state_diagram(a, r.facts["machine_step"]))
    rep = check(r, str(path), read_plan(text.replace("calls sound_alarm", "calls ring_bell"), {}))
    assert "The plan says it runs ring_bell, but the code never does." in rep.problems
    # sound_alarm isn't named by the plan, so what it changes IS compared:
    assert any("the plan says alarms ends at 0; the code gives 1." in p for p in rep.problems)


def test_calls_named_by_the_plan_are_exempt_and_said_so():
    path = GOLDEN / "53_calls_everywhere.c"
    r = parse(str(path))
    a = [x for x in r.items if isinstance(x, A.Action) and x.name == "machine_step"][0]
    text = f"%% function: machine_step\n" + to_mermaid(state_diagram(a, r.facts["machine_step"]))
    rep = check(r, str(path), read_plan(text, {}))
    assert rep.ok
    assert rep.not_checked == ["shared variables changed by the functions the plan names (alarms)"]


def test_plan_mode_missing_from_code():
    rep = run(PUMP, PLAN + "    IDLE --> SERVICE : if level is below 5\n")
    assert "The plan has a mode SERVICE that the code doesn't have." in rep.problems


def test_plan_contradicting_itself():
    with pytest.raises(PlanError) as e:
        read_plan(PLAN + "    FAULT --> IDLE : if level is below 5\n", NAMES)
    assert "the plan says FAULT never leaves, but it has arrows out of it" in str(e.value)


@pytest.mark.parametrize("line,message", [
    ("    PUMPING --> IDLE : when level is high", "an arrow's words must start with 'if ...'"),
    ("    IDLE --> PUMPING : if level is below 20: pump starts", "don't understand this change"),
    ("    IDLE ==> PUMPING", "don't understand this line of the plan"),
])
def test_bad_plan_lines_explain_themselves(line, message):
    with pytest.raises(PlanError) as e:
        read_plan(PLAN + line + "\n", NAMES)
    assert message in str(e.value)


def test_plan_using_a_name_the_code_lacks():
    rep = run(PUMP, PLAN.replace("if level is below 20", "if pressure is below 20"))
    assert any("the plan uses 'pressure'" in p for p in rep.problems)


def test_cli_exit_codes(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    ok = subprocess.run([sys.executable, "-m", "plainsight", str(PUMP), "--check", str(CHECK / "pump_plan.mmd")],
                        capture_output=True, text=True, env=env)
    assert ok.returncode == 0 and "MATCHES" in ok.stdout
    bad = tmp_path / "bad.c"
    bad.write_text(PUMP.read_text().replace("level >= HIGH_LEVEL", "level > HIGH_LEVEL"))
    r = subprocess.run([sys.executable, "-m", "plainsight", str(bad), "--check", str(CHECK / "pump_plan.mmd")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 4 and "DIFFERENT" in r.stdout


# ---------------------------------------------------------------- limits loosened

GRADE = CHECK / "grade.c"
GRADE_PLAN = (CHECK / "grade.plan").read_text()


def test_plain_function_plan_matches_and_catches_a_boundary(tmp_path):
    assert run(GRADE, GRADE_PLAN).ok
    bad = tmp_path / "g.c"
    bad.write_text(GRADE.read_text().replace("total >= 80", "total > 80"))
    rep = run(bad, GRADE_PLAN)
    assert "the plan says it gives back 3; the code gives back 2." in "\n".join(rep.problems)
    assert "bonus = 80" in "\n".join(rep.problems) or "score = 80" in "\n".join(rep.problems)


def test_unplanned_change_to_a_shared_variable_is_caught(tmp_path):
    bad = tmp_path / "g.c"
    bad.write_text(GRADE.read_text().replace("    graded++;\n", "    graded += 2;\n"))
    rep = run(bad, GRADE_PLAN)
    assert any("the plan says graded ends at 1; the code gives 2." in p for p in rep.problems)


def test_plan_that_forgets_what_it_gives_back():
    plan = GRADE_PLAN.replace("otherwise: gives back 0\n", "")
    rep = run(GRADE, plan)
    assert any("The plan doesn't say what grade gives back when" in p for p in rep.problems)


def test_record_with_a_list_inside_is_checked_and_the_list_is_named():
    rep = check(parse(str(CHECK / "logger.c")), str(CHECK / "logger.c"),
                read_plan((CHECK / "logger.mmd").read_text(), {}))
    assert "record fields that aren't plain numbers: samples" in rep.not_checked
    # the plan above is deliberately wrong about LOGGING: count stops at SLOTS (4), and FULL comes at 4
    assert not rep.ok


def test_generated_tests_catch_every_planted_mistake(tmp_path):
    from plainsight.maketests import make_tests
    variants = {"correct": PUMP.read_text(), "alt": (CHECK / "pump_alt.c").read_text()}
    for name, old, new, _ in MISTAKES:
        variants[name] = PUMP.read_text().replace(old, new, 1)
    late = PUMP.read_text().replace("            p->run_minutes++;\n            if (!flowing", "            if (!flowing", 1)
    variants["late_timer"] = late.replace("            break;\n        case FAULT:",
                                          "            p->run_minutes++;\n            break;\n        case FAULT:", 1)
    tests_c = tmp_path / "tests.c"
    n = make_tests(parse(str(PUMP)), str(PUMP), read_plan(PLAN, NAMES), str(tests_c))
    assert 5 <= n <= 80
    for name, code in variants.items():
        src = tmp_path / "pump_under_test.c"
        src.write_text(code)
        text = tests_c.read_text().replace(f'#include "{os.path.relpath(PUMP, tmp_path)}"',
                                           f'#include "{src.name}"')
        (tmp_path / "t.c").write_text(text)
        subprocess.run(["gcc", "-std=c11", str(tmp_path / "t.c"), "-o", str(tmp_path / "t"), "-lm"], check=True)
        r = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True)
        if name in ("correct", "alt"):
            assert r.returncode == 0, f"{name}: {r.stdout}"
        else:
            assert r.returncode > 0, f"the generated tests missed: {name}"


def test_conveyor_gate_from_the_loop_demo(tmp_path):
    """Conditional every-time changes; three notes on one mode must ADD UP."""
    gate, plan = CHECK / "gate.c", (CHECK / "gate_plan.mmd").read_text()
    assert run(gate, plan).ok
    bad = tmp_path / "gate.c"
    bad.write_text(gate.read_text().replace("g->blocked_seconds >= JAM_TIME", "g->blocked_seconds > JAM_TIME"))
    rep = run(bad, plan)
    assert "the plan says it goes to JAMMED; the code stays in OPEN." in "\n".join(rep.problems)


def test_several_every_time_notes_add_up_in_order():
    plan = read_plan("stateDiagram-v2\n    A --> B : if x is at least 2\n"
                     "    note right of A : every time first: x goes up by 1\n"
                     "    note right of A : every time first, if y: x goes up by 10\n"
                     "    note right of A : every time first: y becomes 5\n", {})
    mode, v, _ = plan.step("A", {"x": 0, "y": 1})
    assert (mode, v["x"], v["y"]) == ("B", 11, 5)
    mode, v, _ = plan.step("A", {"x": 0, "y": 0})
    assert (mode, v["x"], v["y"]) == ("A", 1, 5)
