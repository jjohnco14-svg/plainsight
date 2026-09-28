"""Extra checks for the REBUILT snapshot.py (2026-09-27): paths the original
tests (test_lock_diff_strength.py) don't cover."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plainsight.lift import parse  # noqa: E402
from plainsight.snapshot import snapshot, check_lock  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("gcc"), reason="gcc needed")
CHECK = ROOT / "tests" / "check"
PUMP = ROOT / "tests" / "golden" / "49_pump.c"


def ps(*args):
    return subprocess.run([sys.executable, "-m", "plainsight", *map(str, args)], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})


def test_a_changed_diff_never_says_no_changes(tmp_path):
    new = tmp_path / "gate.c"
    new.write_text((CHECK / "gate.c").read_text().replace("#define OPEN_TIME 3", "#define OPEN_TIME 4"))
    r = ps("diff", CHECK / "gate.c", new)
    assert r.returncode == 4 and "CHANGED" in r.stdout and "NO CHANGES" not in r.stdout


def test_plain_function_lock_catches_a_boundary_edit(tmp_path):
    grade = CHECK / "grade.c"
    (name, lock, diagram), = snapshot(parse(str(grade)), str(grade), "grade")[0]
    assert diagram is None and len(lock["cases"]) > 10
    bad = tmp_path / "grade.c"
    bad.write_text(grade.read_text().replace("total >= 80", "total > 80"))
    changes = check_lock(parse(str(bad)), str(bad), lock)
    assert any("before, it gave back 3; now it gives back 2." in c for c in changes)
    assert check_lock(parse(str(grade)), str(grade), lock) == []


def test_record_filler_gets_one_zeroed_example_and_catches_edits(tmp_path):
    made, _ = snapshot(parse(str(PUMP)), str(PUMP), "pump_init")
    (name, lock, _), = made
    bad = tmp_path / "pump.c"
    bad.write_text(PUMP.read_text().replace("    p->faults = 0;\n", "    p->faults = 1;\n", 1))
    changes = check_lock(parse(str(bad)), str(bad), lock)
    assert changes and any("faults: before, it ended at 0; now it ends at 1." in c for c in changes)


def test_side_effect_changes_are_reported(tmp_path):
    grade = CHECK / "grade.c"
    new = tmp_path / "grade.c"
    new.write_text(grade.read_text().replace("    graded++;\n", ""))
    r = ps("diff", grade, new)
    assert "no longer changes shared variable graded" in r.stdout
