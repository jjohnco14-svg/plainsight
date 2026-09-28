"""Warnings about a plan itself, found by running it step by step from its start."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plainsight.plan import read_plan, PlanError  # noqa: E402
from plainsight.plancheck import plan_warnings  # noqa: E402

CHECK = ROOT / "tests" / "check"


def warnings(text):
    return plan_warnings(read_plan(text, {}))


def test_impossible_mode_is_found():
    """The first gate draft: the gate always closes at 3 s, so 5 s of blocking never happens."""
    w = warnings((CHECK / "gate_plan_flawed.mmd").read_text())
    assert any(x.startswith("JAMMED can never be reached") for x in w)


@pytest.mark.parametrize("name", ["gate_plan.mmd", "pump_plan.mmd"])
def test_good_plans_have_no_warnings(name):
    assert warnings((CHECK / name).read_text()) == []


def test_logger_plan_flaw_is_found():
    """logger.mmd only raises count once count is already 3 or more: FULL is impossible."""
    assert any(x.startswith("FULL can never be reached") for x in warnings((CHECK / "logger.mmd").read_text()))


def test_arrow_hidden_by_an_earlier_one():
    w = warnings("stateDiagram-v2\n    A --> B : if x is at least 5\n    A --> B : otherwise, if x is at least 7\n"
                 "    B --> A : always\n")
    assert any('The arrow A --> B "otherwise, if x is at least 7" is never taken' in x for x in w)


def test_unreachable_target_is_reported_once():
    w = warnings("stateDiagram-v2\n    A --> B : if x is at least 5\n    A --> C : otherwise, if x is at least 7\n"
                 "    B --> A : always\n    C --> A : always\n")
    assert [x for x in w if "C" in x.split(" can")[0]] and not any("--> C" in x for x in w)


def test_mode_with_no_way_out():
    w = warnings("stateDiagram-v2\n    A --> B : if go\n")
    assert "B has no way out. If that's intended, add: note right of B : never leaves this mode" in w


def test_start_mode():
    text = "%% start: B\nstateDiagram-v2\n    A --> B : if go\n    B --> C : if go\n    C --> B : if go\n"
    assert any(x.startswith("A can never be reached (starting in B") for x in warnings(text))
    with pytest.raises(PlanError):
        read_plan("%% start: Z\nstateDiagram-v2\n    A --> B : if go\n", {})


def test_a_counter_that_must_build_up_over_many_steps_is_reachable():
    """C needs 12 steps of counting: the search must go that deep, not give up early."""
    text = ("stateDiagram-v2\n    A --> B : if go: n becomes 0\n    B --> C : if n is at least 12\n"
            "    C --> A : always\n    note right of B : every time first: n goes up by 1\n")
    assert warnings(text) == []
