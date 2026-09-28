"""The commands an AI agent uses in a sandbox: no API keys, a person approves."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "tests" / "check"
GOLDEN = ROOT / "tests" / "golden"
pytestmark = pytest.mark.skipif(not shutil.which("gcc"), reason="gcc needed")


def ps(*args, cwd=None, root=ROOT):
    return subprocess.run([sys.executable, "-m", "plainsight", *map(str, args)], capture_output=True,
                          text=True, cwd=cwd, env={**os.environ, "PYTHONPATH": str(root)})


def test_guides_print():
    assert "three kinds of plan" in ps("guide").stdout
    out = ps("agent-guide").stdout
    assert "Never run approve without their explicit yes" in out


def test_names_lists_what_a_plan_may_use():
    out = ps("names", GOLDEN / "49_pump.c").stdout
    assert "modes (PumpState): IDLE, PUMPING, FAULT" in out and "run_minutes" in out
    assert "LOW_LEVEL = 20" in out


def test_plan_check_usable_unusable_and_warnings(tmp_path):
    good = tmp_path / "plan.mmd"
    good.write_text((CHECK / "pump_plan.mmd").read_text())
    r = ps("plan-check", good, "--code", GOLDEN / "49_pump.c")
    assert r.returncode == 0 and "USABLE (machine plan for pump_step)" in r.stdout
    assert "approval: not approved" in r.stdout
    if shutil.which("dot"):
        assert (tmp_path / "plan.png").exists()
    bad = tmp_path / "bad.mmd"
    bad.write_text(good.read_text().replace("if level is below 20", "if pressure is below 20"))
    r = ps("plan-check", bad, "--code", GOLDEN / "49_pump.c")
    assert r.returncode == 2 and "names the code doesn't have: pressure" in r.stdout
    r = ps("plan-check", CHECK / "gate_plan_flawed.mmd", "--json")
    data = json.loads(r.stdout)
    assert data["usable"] and any("JAMMED can never be reached" in w for w in data["warnings"])


def test_approval_is_recorded_and_changes_are_flagged(tmp_path):
    plan = tmp_path / "plan.mmd"
    plan.write_text((CHECK / "pump_plan.mmd").read_text())
    r = ps("check", GOLDEN / "49_pump.c", plan)
    assert r.returncode == 0 and "WARNING: no person has approved this plan yet" in r.stdout
    assert ps("approve", plan, "--by", "Jaden").returncode == 0
    r = ps("check", GOLDEN / "49_pump.c", plan)
    assert "Plan approved by Jaden" in r.stdout
    plan.write_text(plan.read_text() + "    note right of IDLE : every time first: faults becomes 0\n")
    r = ps("check", GOLDEN / "49_pump.c", plan)
    assert "the plan CHANGED after Jaden approved it" in r.stdout


def test_approve_needs_a_name_and_a_usable_plan(tmp_path):
    plan = tmp_path / "plan.mmd"
    plan.write_text("stateDiagram-v2\n    A ==> B\n")
    assert ps("approve", plan).returncode != 0                 # no --by
    r = ps("approve", plan, "--by", "Jaden")
    assert r.returncode == 2 and "isn't usable" in r.stderr
    assert not (tmp_path / "plan.mmd.approved").exists()


def test_check_json_for_agents(tmp_path):
    bad = tmp_path / "pump.c"
    bad.write_text((GOLDEN / "49_pump.c").read_text().replace("level >= HIGH_LEVEL", "level > HIGH_LEVEL"))
    r = ps("check", bad, CHECK / "pump_plan.mmd", "--json")
    data = json.loads(r.stdout)
    assert r.returncode == 4 and data["result"] == "different" and data["approval"]["state"] == "not approved"
    assert any("the code stays in PUMPING" in p for p in data["problems"])


def test_doctor():
    r = ps("doctor")
    assert "clang (reads C)" in r.stdout and r.returncode in (0, 1)


def test_works_without_the_dictum_repo(tmp_path):
    """Copied away from the Dictum repo, Plainsight uses its bundled copies."""
    alone = tmp_path / "alone"
    shutil.copytree(ROOT / "plainsight", alone / "plainsight")
    r = subprocess.run([sys.executable, "-c", "import plainsight.dictum_bridge as b; print(b.COMPILER_DIR)"],
                       capture_output=True, text=True, cwd=alone, env={**os.environ, "PYTHONPATH": str(alone)})
    assert r.stdout.strip() == "None", r.stderr
    r = ps("check", GOLDEN / "49_pump.c", CHECK / "pump_plan.mmd", root=alone, cwd=alone)
    assert r.returncode == 0 and "MATCHES" in r.stdout, r.stderr
    r = ps(GOLDEN / "49_pump.c", "--summary", root=alone, cwd=alone)
    assert "Moves state between modes" in r.stdout
