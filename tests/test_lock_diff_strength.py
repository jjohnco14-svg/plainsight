"""Timelines, locks (snapshot), diff and strength."""
import os
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
PUMP = ROOT / "tests" / "golden" / "49_pump.c"
GATE, GATE_EDITED = CHECK / "gate.c", CHECK / "gate_edited.c"


def ps(*args):
    return subprocess.run([sys.executable, "-m", "plainsight", *map(str, args)], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


# ---------------------------------------------------------------- timelines

TL = ("function: pump_step\n"
      "timeline: p.state = IDLE | level = [15, 15, 50, 95], flowing = true -> p.state = [PUMPING, PUMPING, PUMPING, IDLE]\n"
      "timeline: p.state = PUMPING | level = [50, 50, 50], flowing = false -> p.state = [PUMPING, PUMPING, IDLE], "
      "p.faults = [0, 0, 1]\n")


def test_timeline_matches_and_names_the_failing_step(tmp_path):
    assert check(parse(str(PUMP)), str(PUMP), read_plan(TL, {})).ok
    bad = tmp_path / "p.c"
    bad.write_text(PUMP.read_text().replace("level >= HIGH_LEVEL", "level > 95"))
    rep = check(parse(str(bad)), str(bad), read_plan(TL, {}))
    assert "step 4 (level = 95, flowing = true): p.state: the plan says IDLE, the code gives PUMPING" \
        in "\n".join(rep.problems)


def test_timeline_record_carries_over_between_steps(tmp_path):
    """faults goes 0, 0, 1 only if run_minutes keeps counting from step to step."""
    bad = tmp_path / "p.c"
    bad.write_text(PUMP.read_text().replace("p->run_minutes++;", "p->run_minutes = 1;"))
    rep = check(parse(str(bad)), str(bad), read_plan(TL, {}))
    assert not rep.ok and "step 3" in "\n".join(rep.problems)


def test_timeline_lists_must_line_up():
    with pytest.raises(PlanError) as e:
        read_plan("function: pump_step\ntimeline: p.state = IDLE | level = [1, 2, 3] -> p.state = [IDLE, IDLE]\n", {})
    assert "one value per step" in str(e.value)


# ---------------------------------------------------------------- snapshot / lock

def test_lock_is_same_for_same_code_and_catches_edits(tmp_path):
    r = ps("snapshot", GATE, "-o", tmp_path)
    assert r.returncode == 0 and (tmp_path / "gate_tick.lock").exists()
    same = ps("check", GATE, tmp_path / "gate_tick.lock")
    assert same.returncode == 0 and "SAME: all 20000 recorded cases" in same.stdout
    changed = ps("check", GATE_EDITED, tmp_path / "gate_tick.lock")
    assert changed.returncode == 4
    assert "before, it went to CLOSED; now it stays in JAMMED." in changed.stdout
    assert "before, it went to CLOSED; now it stays in OPEN." in changed.stdout


def test_lock_writes_a_diagram_when_one_can_be_drawn(tmp_path):
    r = ps("snapshot", PUMP, "-o", tmp_path)
    assert (tmp_path / "pump_step.mmd").exists() and (tmp_path / "pump_step.lock").exists()
    assert (tmp_path / "minutes_to_fill.lock").exists() is False and "not locked: minutes_to_fill" in r.stdout


def test_locks_can_be_approved_and_changes_to_them_are_flagged(tmp_path):
    ps("snapshot", GATE, "-o", tmp_path)
    lock = tmp_path / "gate_tick.lock"
    assert ps("approve", lock, "--by", "Jaden").returncode == 0
    assert "WARNING" not in ps("check", GATE, lock).stdout
    data = lock.read_bytes()
    lock.write_bytes(data[:-10] + bytes(10))
    out = ps("check", GATE, lock)
    assert out.returncode != 0 or "changed after it was approved" in out.stdout


# ---------------------------------------------------------------- diff

def test_diff_reports_behaviour_changes_and_draws_them(tmp_path):
    old, new = tmp_path / "old.c", tmp_path / "new.c"
    old.write_text(PUMP.read_text())
    new.write_text(PUMP.read_text().replace("} else if (level >= HIGH_LEVEL) {",
                                            "} else if (level >= HIGH_LEVEL && flowing) {"))
    r = ps("diff", old, new, "--function", "pump_step", "--pictures", tmp_path / "pics")
    assert r.returncode == 4
    assert "In PUMPING, with faults = 0, run_minutes = 0, level = 90, flowing = no:" in r.stdout
    assert "before, it went to IDLE; now it stays in PUMPING." in r.stdout
    if shutil.which("dot"):
        assert (tmp_path / "pics" / "pump_step_diff.png").exists()


def test_diff_picture_keeps_every_arrow_between_the_same_two_modes():
    """The pump has two PUMPING -> IDLE arrows; a changed one must not hide the other."""
    from plainsight.snapshot import _merge
    from plainsight.graph import state_diagram
    from plainsight.dictum_bridge import A
    r = parse(str(PUMP))
    a = [x for x in r.items if isinstance(x, A.Action) and x.name == "pump_step"][0]
    g = state_diagram(a, r.facts["pump_step"])
    g2 = state_diagram(a, r.facts["pump_step"])
    g2.edges = [(x, y, l + " and flowing" if l.startswith("otherwise, if level") else l) for x, y, l in g2.edges]
    merged = _merge(g, g2, "pump_step")
    to_idle = [l for x, y, l in merged.edges if (x, y) == ("PUMPING", "IDLE")]
    assert len(to_idle) == 2 and any(l.startswith("now:") for l in to_idle)


def test_diff_same_code_is_no_change():
    r = ps("diff", GATE, GATE)
    assert r.returncode == 0 and "NO CHANGES" in r.stdout


# ---------------------------------------------------------------- strength

def test_strong_plan_notices_every_planted_bug():
    from plainsight.strength import measure
    st = measure(parse(str(PUMP)), str(PUMP), plan=read_plan((CHECK / "pump_plan.mmd").read_text(),
                                                                 {"LOW_LEVEL": 20, "HIGH_LEVEL": 90, "MAX_FAULTS": 3}))
    assert st.total >= 10 and st.caught == st.total


def test_weak_plan_gets_a_low_score_and_precise_suggestions():
    from plainsight.strength import measure
    weak = ("function: minutes_to_fill\nexample: levels = [50, 18, 40, 70, 95], n = 5 -> gives back 3\n"
            "example: levels = [50, 60, 95], n = 3 -> gives back -1\n")
    lists = CHECK / "lists.c"
    st = measure(parse(str(lists)), str(lists), plan=read_plan(weak, {}))
    assert st.score < 60
    assert any(m.suggestion == "add an example where levels[i] is exactly 20" for m in st.survivors)
    better = weak + ("example: levels = [20, 90], n = 2 -> gives back -1\n"
                     "example: levels = [19, 90], n = 2 -> gives back 1\n"
                     "example: levels = [10, 89], n = 2 -> gives back -1\n")
    st2 = measure(parse(str(lists)), str(lists), plan=read_plan(better, {}))
    assert st2.caught > st.caught      # adding the suggested examples raises the score


def test_lock_strength():
    from plainsight.strength import measure
    from plainsight.snapshot import snapshot
    r = parse(str(GATE))
    (name, lock, _), = snapshot(r, str(GATE), "gate_tick")[0]
    st = measure(r, str(GATE), lock=lock)
    assert st.total >= 10 and st.score >= 90
