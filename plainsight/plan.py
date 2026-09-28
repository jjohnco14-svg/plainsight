"""Read an approved PLAN (a mode diagram) and run it as a small simulator.

The plan language is the wording Plainsight itself uses in its diagrams,
so a diagram Plainsight drew -- or one an AI drafted in the same words --
is a valid plan. Mermaid state-diagram lines:

    %% function: pump_step
    IDLE --> PUMPING : if level is below 20: run_minutes becomes 0
    PUMPING --> FAULT : if not flowing and run_minutes is over 2: faults goes up by 1, then if faults is at least 3
    PUMPING --> IDLE : if not flowing and run_minutes is over 2: faults goes up by 1, then if faults is below 3
    PUMPING --> IDLE : otherwise, if level is at least 90
    note right of PUMPING : every time first: run_minutes goes up by 1
    note right of FAULT : never leaves this mode

Meaning (stated so it can't be read two ways):
- In a mode, the "every time first" changes happen first.
- Then the arrows out of that mode are checked top to bottom; the FIRST one
  whose "if" holds is taken: its changes happen in order, then the move.
- Arrows with the same "if ...: changes" and different "then if" are one
  choice made AFTER those changes (e.g. after faults goes up by 1).
- If no arrow is taken, the mode stays the same.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


class PlanError(Exception):
    pass


# ------------------------------------------------------------------ words -> structure

_OPS = [("is not equal to", "!="), ("is equal to", "=="), ("is greater than", ">"),
        ("is less than", "<"), ("is at least", ">="), ("is at most", "<="),
        ("is below", "<"), ("is over", ">"), ("is not", "!="), ("is", "==")]


def _tokens(text: str) -> List[str]:
    text = text.replace("(", " ( ").replace(")", " ) ").replace(",", " , ")
    return text.split()


class _Parser:
    """cond  := conj ('or' conj)*
       conj  := unary ('and' unary)*
       unary := 'not' unary | '(' cond ')' | value [OP value]
       value := term (('plus'|'minus'|'times') term)*
       term  := NUMBER ['(' NAME ')'] | NAME ['(' NUMBER ')'] | 'negative' term | '(' value ')'"""

    def __init__(self, text: str, names: Dict[str, float]):
        self.t = _tokens(text)
        self.i = 0
        self.text = text
        self.names = names

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def take(self, word=None):
        tok = self.peek()
        if word is not None and tok != word:
            raise PlanError(f"expected '{word}' in: {self.text}")
        self.i += 1
        return tok

    def done(self):
        return self.i >= len(self.t)

    def cond(self):
        node = self.conj()
        while self.peek() == "or":
            self.take()
            node = ("or", node, self.conj())
        return node

    def conj(self):
        node = self.unary()
        while self.peek() == "and":
            self.take()
            node = ("and", node, self.unary())
        return node

    def unary(self):
        if self.peek() == "not":
            self.take()
            return ("not", self.unary())
        if self.peek() == "(" and self._paren_is_condition():
            self.take("(")
            node = self.cond()
            self.take(")")
            return node
        left = self.value()
        op = self._op()
        if op is None:
            return ("truth", left)
        return ("cmp", op, left, self.value())

    def _paren_is_condition(self) -> bool:
        depth, j = 0, self.i
        while j < len(self.t):
            tok = self.t[j]
            if tok == "(":
                depth += 1
            elif tok == ")":
                depth -= 1
                if depth == 0:
                    return False
            elif depth == 1 and tok in ("and", "or", "not", "is"):
                return True
            j += 1
        return False

    def _op(self):
        rest = " ".join(self.t[self.i:self.i + 5])
        for words, op in _OPS:
            if rest == words or rest.startswith(words + " "):
                self.i += len(words.split())
                return op
        return None

    def value(self):
        node = self.term()
        while self.peek() in ("plus", "minus", "times"):
            op = {"plus": "+", "minus": "-", "times": "*"}[self.take()]
            node = ("math", op, node, self.term())
        return node

    def term(self):
        tok = self.take()
        if tok is None:
            raise PlanError(f"sentence ends too early: {self.text}")
        if tok == "negative":
            return ("math", "-", ("num", 0), self.term())
        if tok == "(":
            node = self.value()
            self.take(")")
            return node
        if re.fullmatch(r"-?\d+(\.\d+)?", tok):
            if self.peek() == "(" and self.peek(2) == ")" and re.fullmatch(r"[A-Za-z_]\w*", self.peek(1) or ""):
                self.i += 3  # "20 (LOW_LEVEL)": the name is only a reminder
            return ("num", float(tok) if "." in tok else int(tok))
        if tok in ("true", "yes"):
            return ("num", 1)
        if tok in ("false", "no"):
            return ("num", 0)
        if re.fullmatch(r"[A-Za-z_]\w*", tok):
            if self.peek() == "(" and re.fullmatch(r"-?\d+(\.\d+)?", self.peek(1) or "") and self.peek(2) == ")":
                self.i += 3  # "LOW_LEVEL (20)"
            if tok in self.names:
                return ("num", self.names[tok])
            return ("var", tok)
        raise PlanError(f"don't understand '{tok}' in: {self.text}")


def parse_cond(text, names):
    p = _Parser(text, names)
    node = p.cond()
    if not p.done():
        raise PlanError(f"extra words after the condition: '{' '.join(p.t[p.i:])}' in: {text}")
    return node


def parse_change(text, names):
    """'x becomes V' | 'x goes up by N' | 'x goes down by N' | 'calls f ...'"""
    m = re.fullmatch(r"calls (\w+)\b.*", text.strip())
    if m:
        return ("call", m.group(1), None)
    m = re.fullmatch(r"gives back (.+)", text.strip())
    if m:
        p = _Parser(m.group(1), names)
        v = p.value()
        if not p.done():
            raise PlanError(f"don't understand what it gives back in: {text}")
        return ("give", None, v)
    m = re.fullmatch(r"(\w+) becomes (.+)", text.strip())
    if m:
        p = _Parser(m.group(2), names)
        v = p.value()
        if not p.done():
            raise PlanError(f"don't understand the value in: {text}")
        return (m.group(1), "=", v)
    m = re.fullmatch(r"(\w+) goes (up|down) by (.+)", text.strip())
    if m:
        p = _Parser(m.group(3), names)
        v = p.value()
        return (m.group(1), "+=" if m.group(2) == "up" else "-=", v)
    raise PlanError(f"don't understand this change: '{text}' (use 'x becomes 5', "
                    f"'x goes up by 1' or 'x goes down by 1')")


def _split_changes(text) -> List[str]:
    return [c.strip() for c in text.split(",") if c.strip()]


# ------------------------------------------------------------------ the model

@dataclass
class Rule:
    cond: object                      # when this arrow is considered (None = always)
    changes: List[tuple]              # in order
    branches: List[Tuple[object, str]] = field(default_factory=list)  # (then-if cond or None, target)
    text: str = ""


@dataclass
class Plan:
    function: Optional[str]
    modes: List[str]
    kind: str = "machine"  # "machine" (mode diagram) or "rules" (a plain function)
    first: Dict[str, List[tuple]] = field(default_factory=dict)  # mode -> "every time first" changes
    rules: Dict[str, List[Rule]] = field(default_factory=dict)   # mode -> ordered rules
    stuck: set = field(default_factory=set)                      # modes that never leave
    first_text: Dict[Optional[str], str] = field(default_factory=dict)  # the plan's own words
    start: Optional[str] = None  # "%% start: MODE"; otherwise the first mode written
    examples: List = field(default_factory=list)  # concrete input -> result checks (examples.py)

    def step(self, mode: Optional[str], values: Dict[str, float]):
        """One call, as the plan describes it.
        Returns (new mode, values after the changes, the plan line used or None).
        A rules plan has no modes: its rules are checked top to bottom."""
        v = dict(values)
        if self.kind == "rules":
            for ch in self.first.get(None, []):
                _apply(ch, v)
            for rule in self.rules.get(None, []):
                if rule.cond is None or _eval(rule.cond, v):
                    for ch in rule.changes:
                        _apply(ch, v)
                    return None, v, rule.text
            return None, v, None
        for ch in self.first.get(mode, []):
            _apply(ch, v)
        for rule in self.rules.get(mode, []):
            if rule.cond is None or _eval(rule.cond, v):
                for ch in rule.changes:
                    _apply(ch, v)
                for then_cond, target in rule.branches:
                    if then_cond is None or _eval(then_cond, v):
                        return target, v, rule.text
                return mode, v, rule.text
        return mode, v, None


def _value(node, v):
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "var":
        if node[1] not in v:
            raise PlanError(f"the plan uses '{node[1]}', which the code's machine doesn't have")
        return v[node[1]]
    if kind == "math":
        a, b = _value(node[2], v), _value(node[3], v)
        return a + b if node[1] == "+" else a - b if node[1] == "-" else a * b
    raise PlanError(f"bad value {node}")


def _eval(node, v) -> bool:
    kind = node[0]
    if kind == "and":
        return _eval(node[1], v) and _eval(node[2], v)
    if kind == "or":
        return _eval(node[1], v) or _eval(node[2], v)
    if kind == "not":
        return not _eval(node[1], v)
    if kind == "truth":
        return _value(node[1], v) != 0
    if kind == "cmp":
        a, b = _value(node[2], v), _value(node[3], v)
        return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b, "==": a == b, "!=": a != b}[node[1]]
    raise PlanError(f"bad condition {node}")


def _join_text(old, new):
    return new if not old else old + "; " + new


def _apply(change, v):
    if change[0] == "if":
        if _eval(change[1], v):
            for c in change[2]:
                _apply(c, v)
        return
    if change[0] == "call":
        return  # "runs f, whatever f does": checked separately (see check.py)
    if change[0] == "give":
        v["(gives back)"] = _value(change[2], v)
        return
    name, op, val = change
    if name not in v:
        raise PlanError(f"the plan changes '{name}', which the code's machine doesn't have")
    x = _value(val, v)
    v[name] = x if op == "=" else v[name] + x if op == "+=" else v[name] - x


def calls(plan: Plan) -> List[str]:
    """Functions the plan says get run."""
    out = []
    for chs in list(plan.first.values()) + [r.changes for rs in plan.rules.values() for r in rs]:
        for c in chs:
            if c[0] == "call" and c[1] not in out:
                out.append(c[1])
    return out


def constants(plan: Plan) -> List[float]:
    """Every number the plan mentions: the inputs worth testing sit around these."""
    out = set()

    def walk(n):
        if isinstance(n, tuple):
            if n and n[0] == "num":
                out.add(n[1])
            for x in n[1:]:
                walk(x)
        elif isinstance(n, list):
            for x in n:
                walk(x)
    for mode, rules in plan.rules.items():
        for r in rules:
            walk(r.cond)
            walk(r.changes)
            for c, _ in r.branches:
                walk(c)
    for chs in plan.first.values():
        walk(chs)
    return sorted(out)


# ------------------------------------------------------------------ reading the file

_ARROW = re.compile(r"^\s*(\w+)\s*-->\s*(\w+)\s*:\s*(.+?)\s*$")
_NOTE = re.compile(r"^\s*note\s+(?:right|left)\s+of\s+(\w+)\s*:\s*(.+?)\s*$")
_LONE = re.compile(r"^\s*(\w+)\s*$")


def _clean(label: str) -> str:
    label = re.sub(r"<br\s*/?>", " ", label)
    for a, b in (("#quot;", '"'), ("#lt;", "<"), ("#gt;", ">"), ("&amp;", "&")):
        label = label.replace(a, b)
    return re.sub(r"\s+", " ", label).strip()


def read_plan(text: str, names: Optional[Dict[str, float]] = None) -> Plan:
    """A mode diagram (Mermaid stateDiagram) or a rules list (a plain function)."""
    if "stateDiagram" not in text and "-->" not in text:
        return read_rules(text, names)
    names = dict(names or {})
    function = None
    start = None
    machine_examples = []
    modes: List[str] = []
    arrows: List[Tuple[str, str, str]] = []
    notes: List[Tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"%%\s*function:\s*(\w+)", line)
        if m:
            function = m.group(1)
            continue
        m = re.match(r"%%\s*start:\s*(\w+)", line)
        if m:
            start = m.group(1)
            continue
        m = re.match(r"%%\s*(example:.*)$", line, re.I)
        if m:
            from .examples import parse_example
            machine_examples.append(parse_example(m.group(1)))
            continue
        m = re.match(r"%%\s*(timeline:.*)$", line, re.I)
        if m:
            from .examples import parse_timeline
            machine_examples.append(parse_timeline(m.group(1)))
            continue
        if line.startswith("%%") or line.startswith("stateDiagram") or line.startswith("direction"):
            continue
        m = _ARROW.match(line)
        if m:
            arrows.append((m.group(1), m.group(2), _clean(m.group(3))))
            for x in (m.group(1), m.group(2)):
                if x not in modes and x != "[*]":
                    modes.append(x)
            continue
        m = _NOTE.match(line)
        if m:
            notes.append((m.group(1), _clean(m.group(2))))
            if m.group(1) not in modes:
                modes.append(m.group(1))
            continue
        m = _LONE.match(line)
        if m:
            if m.group(1) not in modes:
                modes.append(m.group(1))
            continue
        raise PlanError(f"don't understand this line of the plan: {line}")

    plan = Plan(function=function, modes=modes, start=start, examples=machine_examples)
    if start is not None and start not in modes:
        raise PlanError(f"the plan starts in {start}, but has no such mode")
    names_for_values = {k: v for k, v in names.items() if v is not None}
    for mode, text in notes:
        t = text.strip()
        m2 = re.fullmatch(r"every time first,\s*if (.+?):\s*(.+)", t, re.I)
        if m2:
            # a change made every time, but only when something is true
            changes = [parse_change(c, names_for_values) for c in _split_changes(m2.group(2))]
            plan.first.setdefault(mode, []).append(("if", parse_cond(m2.group(1), names_for_values), changes))
            plan.first_text[mode] = _join_text(plan.first_text.get(mode), f"if {m2.group(1)}: {m2.group(2)}")
        elif t.lower().startswith("every time first:"):
            # several notes add up, in order (they never replace each other)
            plan.first.setdefault(mode, []).extend(parse_change(c, names_for_values)
                                                   for c in _split_changes(t.split(":", 1)[1]))
            plan.first_text[mode] = _join_text(plan.first_text.get(mode), t.split(":", 1)[1].strip())
        elif t.lower().startswith("never leaves"):
            plan.stuck.add(mode)
        elif t.lower().startswith("every time:"):
            plan.first.setdefault(mode, []).extend(parse_change(c, names_for_values)
                                                   for c in _split_changes(t.split(":", 1)[1]))
        else:
            raise PlanError(f"don't understand the note on {mode}: '{t}' "
                            f"(use 'every time first: ...' or 'never leaves this mode')")

    for src, dst, label in arrows:
        t = label
        if t.lower() in ("always",):
            cond_text, rest = None, ""
        else:
            t = re.sub(r"^otherwise,\s*", "", t)
            if not t.lower().startswith("if "):
                raise PlanError(f"an arrow's words must start with 'if ...': {src} --> {dst} : {label}")
            t = t[3:]
            cond_text, _, rest = t.partition(":")
        then_cond = None
        changes_text = rest
        m = re.search(r"(?:,\s*)?\bthen if\b(.+)$", rest)
        if m:
            changes_text = rest[:m.start()]
            then_cond = parse_cond(m.group(1).strip(), names_for_values)
        elif cond_text is not None:
            m = re.search(r"^(.*?),\s*then if (.+)$", cond_text)
            if m:
                cond_text, then_cond = m.group(1), parse_cond(m.group(2), names_for_values)
        cond = parse_cond(cond_text.strip(), names_for_values) if cond_text is not None else None
        changes = [parse_change(c, names_for_values) for c in _split_changes(changes_text)]
        rules = plan.rules.setdefault(src, [])
        same = [r for r in rules if r.cond == cond and r.changes == changes and r.branches
                and r.branches[-1][0] is not None]
        if then_cond is not None and same:
            same[-1].branches.append((then_cond, dst))
        else:
            rules.append(Rule(cond=cond, changes=changes, branches=[(then_cond, dst)], text=label))
    for mode in plan.stuck:
        if plan.rules.get(mode):
            raise PlanError(f"the plan says {mode} never leaves, but it has arrows out of it")
    return plan


def read_rules(text: str, names: Optional[Dict[str, float]] = None) -> Plan:
    """A plan for a plain function, one rule per line, checked top to bottom:

        function: grade
        if score is at least 90: gives back 4
        otherwise, if score is at least 80: gives back 3
        otherwise: gives back 0
    """
    names = {k: v for k, v in (names or {}).items() if v is not None}
    plan = Plan(function=None, modes=[], kind="rules")
    rules = plan.rules.setdefault(None, [])
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "%%")):
            continue
        m = re.fullmatch(r"function:\s*(\w+)", line)
        if m:
            plan.function = m.group(1)
            continue
        if line.lower().startswith("example"):
            from .examples import parse_example
            plan.examples.append(parse_example(line))
            continue
        if line.lower().startswith("timeline"):
            from .examples import parse_timeline
            plan.examples.append(parse_timeline(line))
            continue
        m = re.fullmatch(r"every time first:\s*(.+)", line, re.I)
        if m:
            plan.first[None] = [parse_change(c, names) for c in _split_changes(m.group(1))]
            plan.first_text[None] = m.group(1).strip()
            continue
        body = re.sub(r"^otherwise,\s*", "", line)
        m = re.fullmatch(r"otherwise:\s*(.+)", body)
        if m:
            rules.append(Rule(cond=None, changes=[parse_change(c, names) for c in _split_changes(m.group(1))],
                              text=line))
            continue
        m = re.fullmatch(r"if (.+?):\s*(.+)", body)
        if not m:
            raise PlanError(f"don't understand this rule: '{line}' (use 'if CONDITION: CHANGES' "
                            f"or 'otherwise: CHANGES')")
        rules.append(Rule(cond=parse_cond(m.group(1), names),
                          changes=[parse_change(c, names) for c in _split_changes(m.group(2))], text=line))
    if plan.function is None:
        raise PlanError("a plan must say which function it is for: 'function: NAME'")
    if not rules and not plan.examples:
        raise PlanError("the plan has no rules and no examples")
    if not rules:
        plan.kind = "examples"
    return plan
