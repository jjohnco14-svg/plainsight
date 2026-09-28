"""Everything the installed tool needs must be listed in pyproject's package-data.

A bug found while writing the README: `pip install` left out the Arduino stand-in
header (docs/plainsight_arduino.h), so .ino files worked from the repo but failed
after installing."""
import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _patterns():
    text = (ROOT / "pyproject.toml").read_text()
    m = re.search(r"\[tool\.setuptools\.package-data\]\s*plainsight\s*=\s*\[(.*?)\]", text, re.S)
    assert m, "pyproject.toml has no package-data for plainsight"
    return re.findall(r'"([^"]+)"', m.group(1))


def test_every_file_in_docs_is_packaged():
    patterns = _patterns()
    docs = ROOT / "plainsight" / "docs"
    missing = [f.name for f in docs.iterdir()
               if f.is_file() and not any(fnmatch.fnmatch(f"docs/{f.name}", p) for p in patterns)]
    assert not missing, f"not in package-data, so missing after pip install: {missing}"
