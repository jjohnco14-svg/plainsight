"""Run Plainsight over real open-source C and count what isn't supported yet.

    python3 tools/corpus_census.py            (downloads to ./corpus_cache/ once)

Checks: every file parses, nothing crashes. Output: statements covered and a
ranked list of not-yet-supported constructs -- the priority list for new rules.
"""
import collections
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plainsight.lift import parse  # noqa: E402

BASE = "https://raw.githubusercontent.com/"
FILES = {  # file -> (url, extra compiler args); all MIT/BSD/public-domain projects
    "cJSON.c": ("DaveGamble/cJSON/master/cJSON.c", []),
    "cJSON.h": ("DaveGamble/cJSON/master/cJSON.h", None),
    "linenoise.c": ("antirez/linenoise/master/linenoise.c", ["-D_DEFAULT_SOURCE"]),
    "linenoise.h": ("antirez/linenoise/master/linenoise.h", None),
    "sds.c": ("antirez/sds/master/sds.c", []),
    "sds.h": ("antirez/sds/master/sds.h", None),
    "sdsalloc.h": ("antirez/sds/master/sdsalloc.h", None),
    "testhelp.h": ("antirez/sds/master/testhelp.h", None),
    "jsmn.h": ("zserge/jsmn/master/jsmn.h", None),
    "aes.c": ("kokke/tiny-AES-c/master/aes.c", []),
    "aes.h": ("kokke/tiny-AES-c/master/aes.h", None),
    "sha256.c": ("B-Con/crypto-algorithms/master/sha256.c", []),
    "sha256.h": ("B-Con/crypto-algorithms/master/sha256.h", None),
}


def main() -> int:
    cache = ROOT / "corpus_cache"
    cache.mkdir(exist_ok=True)
    for name, (url, _) in FILES.items():
        if not (cache / name).exists():
            urllib.request.urlretrieve(BASE + url, cache / name)
    jsmn = cache / "jsmn_impl.c"
    if not jsmn.exists():
        jsmn.write_text("#define JSMN_STATIC\n" + (cache / "jsmn.h").read_text())
    targets = [(n, a) for n, (_, a) in FILES.items() if a is not None] + [("jsmn_impl.c", [])]

    gaps = collections.Counter()
    total = covered = functions = crashes = 0
    print(f"{'file':14s} {'functions':>9s} {'statements':>10s} {'covered':>8s} {'time':>6s}")
    for name, extra in targets:
        t = time.time()
        try:
            r = parse(str(cache / name), extra)
        except Exception as e:  # a crash is a bug to fix, never skipped silently
            crashes += 1
            print(f"{name:14s} CRASH: {e!r}")
            continue
        cov = r.coverage
        ok = cov["statements"] - cov["not_yet_supported"]
        total, covered, functions = total + cov["statements"], covered + ok, functions + len(r.facts)
        for f in r.facts.values():
            for g in f["not_yet_supported"]:
                gaps[g["what"]] += 1
        pct = 100 * ok / cov["statements"] if cov["statements"] else 100
        print(f"{name:14s} {len(r.facts):9d} {cov['statements']:10d} {pct:7.1f}% {time.time() - t:5.1f}s")
    print(f"\nTOTAL: {functions} functions, {covered} of {total} statements described "
          f"({100 * covered / total:.1f}%), {crashes} crashes\n")
    print("Not yet supported, most common first:")
    for what, n in gaps.most_common():
        print(f"  {n:4d}  {what}")
    return 1 if crashes else 0


if __name__ == "__main__":
    raise SystemExit(main())
