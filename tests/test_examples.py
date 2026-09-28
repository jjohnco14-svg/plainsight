"""Examples: any input types (lists, text, records), lists changed in place,
printing, shared variables -- run on the real code, each in its own process."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plainsight.lift import parse  # noqa: E402
from plainsight.check import check  # noqa: E402
from plainsight.plan import read_plan, PlanError  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("gcc"), reason="gcc needed")
CHECK = ROOT / "tests" / "check"
LISTS = CHECK / "lists.c"

FILL = """function: minutes_to_fill
example: levels = [50, 18, 40, 70, 95], n = 5 -> gives back 3
example: levels = [50, 60, 95], n = 3 -> gives back -1
example: levels = [10, 90], n = 2 -> gives back 1
"""
CLAMP = "function: clamp_all\nexample: values = [1.5, -3, 20], n = 3, lo = 0, hi = 10 -> values becomes [1.5, 0, 10]\n"
GREET = ('function: greet\nexample: name = "Ana" -> prints "Hello, Ana!\\n", greetings becomes 1\n'
         'example: name = "Bo" -> prints "Hello, Bo!\\n", greetings becomes 1\n')


def run(path, text):
    return check(parse(str(path)), str(path), read_plan(text, {}))


@pytest.mark.parametrize("plan", [FILL, CLAMP, GREET], ids=["list-in", "list-changed", "prints"])
def test_examples_match_correct_code(plan):
    rep = run(LISTS, plan)
    assert rep.ok, rep.problems


def test_examples_do_not_leak_shared_variables():
    """Both greet examples expect greetings 0 -> 1: only true if each runs on its own."""
    assert run(LISTS, GREET).ok


@pytest.mark.parametrize("old,new,plan,expected", [
    ("levels[i] >= 90", "levels[i] > 90", FILL, "gives back: the plan says 1, the code gives -1"),
    ("values[i] = lo;", "values[i] = hi;", CLAMP, "values: the plan says [1.5, 0, 10], the code gives [1.5, 10, 10]"),
    ("Hello, %s!", "Hi, %s!", GREET, 'prints: the plan says "Hello, Ana!\\n", the code gives "Hi, Ana!\\n"'),
    ("    greetings++;\n", "", GREET, "greetings: the plan says 1, the code gives 0"),
])
def test_examples_catch_mistakes(tmp_path, old, new, plan, expected):
    src = LISTS.read_text()
    assert old in src
    bad = tmp_path / "bad.c"
    bad.write_text(src.replace(old, new, 1))
    rep = run(bad, plan)
    assert not rep.ok and expected in "\n".join(rep.problems), rep.problems


def test_a_crash_fails_only_its_example():
    plan = ("function: first_item\nexample: items = [7, 8], n = 2 -> gives back 7\n"
            "example: items = [7, 8, 9], n = 3 -> gives back 7\n")
    rep = run(CHECK / "crash.c", plan)
    assert rep.examples.passed == 1 and rep.examples.total == 2
    assert "the code crashed" in rep.problems[0]


def test_record_example_on_the_pump():
    plan = ("function: pump_step\n"
            "example: p.state = PUMPING, p.run_minutes = 2, p.faults = 2, level = 50, flowing = false "
            "-> p.state becomes FAULT, p.faults becomes 3\n")
    assert run(ROOT / "tests" / "golden" / "49_pump.c", plan).ok


@pytest.mark.parametrize("plan,message", [
    ("function: first_item\nexample: items = [7], count = 1 -> gives back 7\n",
     "doesn't give a value for the input 'n'"),
    ('function: first_item\nexample: items = ["a", "b"], n = 2 -> gives back 7\n',
     'example 1: the value items = ["a", "b"] doesn\'t fit'),
    ("function: first_item\nexample: items = [7], n = 1 -> n becomes 2\n", "passed by value"),
])
def test_bad_examples_explain_themselves(plan, message):
    with pytest.raises(PlanError) as e:
        run(CHECK / "crash.c", plan)
    assert message in str(e.value), str(e.value)


def test_examples_become_a_standalone_test_file(tmp_path):
    from plainsight.maketests import make_tests
    out = tmp_path / "t.c"
    n = make_tests(parse(str(LISTS)), str(LISTS), read_plan(FILL, {}), str(out))
    assert n == 3
    subprocess.run(["gcc", "-std=c11", str(out), "-o", str(tmp_path / "t"), "-lm"], check=True)
    r = subprocess.run([str(tmp_path / "t")], capture_output=True, text=True)
    assert r.returncode == 0 and "3 of 3 examples passed." in r.stdout


def test_plan_check_reports_examples_that_do_not_fit():
    from plainsight.planfile import validate
    code = parse(str(CHECK / "crash.c"))
    plan, problems, _ = validate("function: first_item\nexample: items = [7], count = 1 -> gives back 7\n", code)
    assert plan is None and "doesn't give a value for the input 'n'" in problems[0]
    plan, problems, _ = validate("function: first_item\nexample: items = [7], n = 1 -> gives back 7\n", code)
    assert plan is not None and problems == []
