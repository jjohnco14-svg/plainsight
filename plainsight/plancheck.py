"""Warnings about a PLAN itself, before any code exists.

Runs the plan step by step from its start mode, trying inputs around every
number in the plan at each step, and reports modes that can never be
reached and arrows that are never taken -- the kind of mistake that makes a
mode like JAMMED impossible even though it's drawn.

What persists between steps: everything the plan changes (and, when code is
given, the record's fields and shared variables). Everything else is an
input, chosen freshly at each step. Numbers start at 0 unless the plan says
otherwise with  %% start: MODE
"""
import itertools
import random
import re
from collections import deque
from typing import Dict, List, Optional, Set

from .plan import Plan, PlanError, constants

MAX_STATES = 60000
MAX_INPUT_COMBOS = 256


def _vars(plan: Plan):
    """(changed by the plan, only read by the plan)"""
    changed, read = set(), set()

    def walk(n, reading=True):
        if isinstance(n, tuple):
            if n and n[0] == "var":
                read.add(n[1])
                return
            if n and n[0] == "if":
                walk(n[1])
                for c in n[2]:
                    walk(c)
                return
            if n and len(n) == 3 and isinstance(n[0], str) and n[1] in ("=", "+=", "-="):
                changed.add(n[0])
                walk(n[2])
                return
            for x in n[1:]:
                walk(x)
        elif isinstance(n, list):
            for x in n:
                walk(x)
    for rules in plan.rules.values():
        for r in rules:
            walk(r.cond)
            walk(r.changes)
            for c, _ in r.branches:
                walk(c)
    for chs in plan.first.values():
        walk(chs)
    return changed, read - changed


def plan_warnings(plan: Plan, persistent: Optional[Set[str]] = None, inputs: Optional[Set[str]] = None,
                  start: Optional[str] = None) -> List[str]:
    if plan.kind != "machine" or not plan.modes:
        return []
    changed, read_only = _vars(plan)
    keep = sorted(changed | (persistent or set()))
    free = sorted((read_only | (inputs or set())) - set(keep))
    start = start or plan.start or plan.modes[0]
    nums = constants(plan)
    around = sorted({0, 1, -1} | {c + d for c in nums for d in (-1, 0, 1)})
    bound = max([abs(x) for x in around] + [1]) * 3 + 10
    combos = list(itertools.product(*[around for _ in free]))
    if len(combos) > MAX_INPUT_COMBOS:
        rng = random.Random(7)
        combos = [tuple(0 for _ in free)] + rng.sample(combos, MAX_INPUT_COMBOS - 1)

    seen_modes = {start}
    taken = set()  # (mode, rule text, target)
    first_state = (start, tuple(0 for _ in keep))
    queue, seen = deque([first_state]), {first_state}
    capped = False
    while queue:
        mode, vals = queue.popleft()
        for combo in combos:
            values = dict(zip(keep, vals))
            values.update(zip(free, combo))
            try:
                new_mode, after, rule = plan.step(mode, values)
            except PlanError:
                continue
            if rule is not None:
                taken.add((mode, rule, new_mode))
            seen_modes.add(new_mode)
            state = (new_mode, tuple(after.get(k, 0) for k in keep))
            if any(abs(v) > bound for v in state[1]) or state in seen:
                continue
            if len(seen) >= MAX_STATES:
                capped = True
                continue
            seen.add(state)
            queue.append(state)

    how = (f"starting in {start} with every number at 0, and trying inputs around every number in the plan"
           + (" (search stopped at its size limit, so this may be a false alarm)" if capped else ""))
    out = []
    for mode in plan.modes:
        if mode not in seen_modes:
            out.append(f"{mode} can never be reached ({how}).")
    for mode, rules in plan.rules.items():
        if mode not in seen_modes:
            continue
        for r in rules:
            for _, target in r.branches:
                if target not in seen_modes:
                    continue  # already said: that mode can never be reached
                if (mode, r.text, target) not in taken:
                    out.append(f"The arrow {mode} --> {target} \"{r.text}\" is never taken ({how}).")
    for mode in plan.modes:
        if mode in seen_modes and not plan.rules.get(mode) and mode not in plan.stuck:
            out.append(f"{mode} has no way out. If that's intended, add: note right of {mode} : "
                       f"never leaves this mode")
    return out
