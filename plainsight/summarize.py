"""Mechanical summaries: what a function does as a whole, built by fixed rules.

Each rule recognises one common shape of code under strict conditions and
says what that shape does. If no rule matches, the exact steps are shown.
Nothing here guesses, and nothing uses an AI model.

Rules:
  - mode machine: a switch on a variable with named options (an enum) whose
    cases move it between those options -> one bullet list per mode
  - item loop: a counted loop over a list whose parts each match a known
    pattern (remember the first match, stop at the first match, count,
    add up, keep the largest/smallest) -> one sentence per pattern
  - straight line: only simple changes -> "x becomes 0; y goes up by 1"
"""
from typing import List, Optional

from .dictum_bridge import A
from . import nodes as N
from .render import Renderer, _join_and, _join_or

_SHORT_CMP = {"<": "is below", ">": "is over", "<=": "is at most", ">=": "is at least",
              "==": "is", "!=": "is not"}
_NEGATE = {"<": ">=", ">=": "<", ">": "<=", "<=": ">", "==": "!=", "!=": "=="}


class NoMatch(Exception):
    """The code isn't in a shape this rule describes exactly."""


class SummaryWords(Renderer):
    """Shorter wording for summaries. Still exact: only names are shortened
    (a record's fields by their own name), never meaning."""

    def __init__(self, record_param: Optional[str] = None, counter: Optional[str] = None,
                 collection: Optional[str] = None, found_vars=None):
        self.record_param = record_param
        self.counter = counter
        self.collection = collection
        self.found_vars = found_vars if found_vars is not None else {}  # var -> what it remembers

    def expr(self, e) -> str:
        if isinstance(e, A.IndexAccess) and self.collection and e.collection == self.collection \
                and isinstance(e.index, A.Identifier) and e.index.name == self.counter:
            return "the item"
        if isinstance(e, A.Identifier) and self.counter and e.name == self.counter:
            return "its position"
        if isinstance(e, A.Identifier) and getattr(e, "named_constant", False) \
                and e.name in ("true", "false"):
            return e.name
        if isinstance(e, A.Identifier) and getattr(e, "named_constant", False) \
                and getattr(e, "value_text", None):
            return f"{self._plain_value(e.value_text)} ({e.name})"
        return super().expr(e)

    def _binary(self, e):
        if e.op in _SHORT_CMP and isinstance(e.left, A.Identifier) and e.left.name in self.found_vars:
            v, lit = e.left.name, e.right
            if isinstance(lit, A.Literal) and str(lit.value) == "0" and e.op in (">=",):
                return f"once {self.found_vars[v]} has been found"
            if isinstance(lit, A.Literal) and str(lit.value) == "0" and e.op == "<":
                return f"while {self.found_vars[v]} hasn't been found yet"
        if e.op in _SHORT_CMP:
            l = self._group(e.left) if isinstance(e.left, A.BinaryOp) else self.expr(e.left)
            r = self._group(e.right) if isinstance(e.right, A.BinaryOp) else self.expr(e.right)
            return f"{l} {_SHORT_CMP[e.op]} {r}"
        return super()._binary(e)

    def _group(self, e) -> str:
        s = self.expr(e)
        if isinstance(e, A.BinaryOp) and e.op in ("&&", "||"):
            return f"({s})"
        return s

    def negate(self, e) -> str:
        if isinstance(e, A.BinaryOp) and e.op in _NEGATE:
            return self.expr(A.BinaryOp(op=_NEGATE[e.op], left=e.left, right=e.right))
        return f"not ({self.cond(e)})"

    # ---- one change, as a short phrase
    def action(self, s) -> str:
        e = self.expr
        if isinstance(s, A.Assignment):
            return f"{s.target} becomes {self.value(s.value)}"
        if isinstance(s, N.Store):
            return f"{self._store_target(s.target)} becomes {self.value(s.value)}"
        if isinstance(s, N.Step):
            return f"{e(s.target)} goes {'up' if s.delta > 0 else 'down'} by 1"
        if isinstance(s, N.CompoundAssign) and s.op in ("+", "-"):
            return f"{e(s.target)} goes {'up' if s.op == '+' else 'down'} by {e(s.value)}"
        if isinstance(s, A.FuncCall):
            text = self._call_stmt(s)
            return text[0].lower() + text[1:].rstrip(".")
        raise NoMatch


def _simple(s) -> bool:
    return isinstance(s, (A.Assignment, N.Store, N.Step, N.CompoundAssign, A.FuncCall))


# ------------------------------------------------------------------ mode machine

def _mode_machine(sw: N.Switch, words: SummaryWords, var_text: str) -> List[str]:
    if not str(getattr(sw.value, "ctype", "")).endswith("choice"):
        raise NoMatch
    for g in sw.groups:
        if g.falls_through or any(l is not None and not isinstance(l, A.Identifier) for l in g.labels):
            raise NoMatch

    def is_mode_write(s):
        return isinstance(s, (N.Store, A.Assignment)) and _target_text(s, words) == var_text

    def to_phrase(v):
        if isinstance(v, A.Identifier):
            return f"goes to {v.name}"
        if isinstance(v, N.Ternary) and isinstance(v.then, A.Identifier) and isinstance(v.other, A.Identifier):
            return (f"then goes to {v.then.name} if {words.cond(v.cond)}, "
                    f"otherwise to {v.other.name}")
        raise NoMatch

    def mentions_mode(s) -> bool:
        exact = Renderer()
        text = " ".join(exact.expr(x) for x in (getattr(s, "value", None), getattr(s, "target", None))
                        if isinstance(x, N.Node))
        return exact.expr(sw.value) in text

    def leaf(stmts) -> str:
        """Simple changes in source order, at most one of them a mode change.
        A change after the mode change is allowed only if it doesn't involve
        the mode itself (then order can't change the meaning)."""
        parts, moved = [], False
        for s in stmts:
            if isinstance(s, A.Break) and getattr(s, "target", "") == "switch":
                continue
            if not _simple(s):
                raise NoMatch
            if is_mode_write(s):
                if moved:
                    raise NoMatch
                parts.append(to_phrase(s.value))
                moved = True
            elif moved and mentions_mode(s):
                raise NoMatch
            else:
                parts.append(words.action(s))
        return "; ".join(parts) if parts else "nothing changes"

    lines = []
    for g in sw.groups:
        modes = [l.name for l in g.labels if l is not None]
        head = "In " + _join_or(modes) if modes else "In any other mode"
        if any(l is None for l in g.labels) and modes:
            head += ", or any other mode"
        lines.append(head + ":")
        body = list(g.body)
        if not body:
            lines.append(f"  - nothing ever changes; it stays in {_join_or(modes) or 'that mode'}")
            continue
        chain = body[-1] if isinstance(body[-1], A.If) else None
        first = body[:-1] if chain is not None else body
        if chain is None:
            lines.append(f"  - always: {leaf(first)}")
            continue
        if first:
            lines.append(f"  - every time, first: {leaf(first)}")
        prefix, node, conditional = "if", chain, False
        while True:
            if any(isinstance(x, A.If) for x in node.then_body):
                raise NoMatch
            lines.append(f"  - {prefix} {words.cond(node.cond)}: {leaf(node.then_body)}")
            conditional = True
            if len(node.else_body) == 1 and isinstance(node.else_body[0], A.If):
                node, prefix = node.else_body[0], "otherwise, if"
                continue
            if node.else_body:
                if any(isinstance(x, A.If) for x in node.else_body):
                    raise NoMatch
                lines.append(f"  - otherwise: {leaf(node.else_body)}")
            elif conditional and modes:
                lines.append(f"  - otherwise: it stays in {_join_or(modes)}")
            break
    if not sw.has_default:
        lines.append("Any other value: nothing happens.")
    return lines


def _target_text(s, words) -> str:
    if isinstance(s, A.Assignment):
        return s.target
    return words.expr(s.target)


# ------------------------------------------------------------------ item loops

def _item_loop(rep: A.Repeat, before: List[object], words_base: dict) -> List[str]:
    """for (i = 0; i < n; i++) over one list, every part a known pattern."""
    i = rep.counter
    lists = set()

    def find_lists(e):
        if isinstance(e, A.IndexAccess) and isinstance(e.index, A.Identifier) and e.index.name == i:
            lists.add(e.collection)
        for f in ("left", "right", "operand", "cond", "then", "other", "value", "index", "target", "expr"):
            x = getattr(e, f, None)
            if isinstance(x, N.Node if hasattr(N, "Node") else object):
                find_lists(x)
        for x in getattr(e, "args", []) or []:
            find_lists(x)
        for f in ("then_body", "else_body"):
            for x in getattr(e, f, []) or []:
                find_lists(x)
    for s in rep.body:
        find_lists(s)
    if len(lists) != 1:
        raise NoMatch
    coll = lists.pop()
    sentinels = {v.name for v in before if isinstance(v, A.VarDecl) and isinstance(v.value, A.UnaryOp)
                 and v.value.op == "-" and isinstance(v.value.operand, A.Literal)
                 and str(v.value.operand.value) == "1"}
    zeros = {v.name: str(getattr(v.value, "source", v.value.value)) for v in before
             if isinstance(v, A.VarDecl) and _is_zero(v.value)}

    def zero_note(name):
        return f" (starting from {zeros[name]})" if name in zeros else ""
    found = {}
    w = SummaryWords(counter=i, collection=coll, found_vars=found, **words_base)

    def item_cond(c):
        """'the first item that is below 20', not 'where the item is below 20'."""
        if isinstance(c, A.BinaryOp) and c.op in _SHORT_CMP and w.expr(c.left) == "the item":
            return f"that {_SHORT_CMP[c.op]} {w.expr(c.right)}"
        return f"where {w.cond(c)}"

    def once(c):
        """Split off a leading 'start has been found' check."""
        if isinstance(c, A.BinaryOp) and c.op == "&&" and isinstance(c.left, A.BinaryOp) \
                and isinstance(c.left.left, A.Identifier) and c.left.left.name in found:
            return w.cond(c.left) + ", ", c.right
        return "", c

    out = []
    for s in rep.body:
        # remember the first match: if (v < 0 && C) v = i;
        if isinstance(s, A.If) and not s.else_body and len(s.then_body) == 1 \
                and isinstance(s.then_body[0], A.Assignment) \
                and isinstance(s.then_body[0].value, A.Identifier) and s.then_body[0].value.name == i \
                and s.then_body[0].target in sentinels and isinstance(s.cond, A.BinaryOp) and s.cond.op == "&&" \
                and isinstance(s.cond.left, A.BinaryOp) and s.cond.left.op == "<" \
                and isinstance(s.cond.left.left, A.Identifier) and s.cond.left.left.name == s.then_body[0].target:
            v = s.then_body[0].target
            what = item_cond(s.cond.right)
            out.append(f"  - remembers in {v} the position of the first item {what} "
                       f"({v} starts at -1, meaning none found yet)")
            found[v] = v
            continue
        # find, remember, stop: if (C) { v = i; break; }
        if isinstance(s, A.If) and not s.else_body and len(s.then_body) == 2 \
                and isinstance(s.then_body[0], A.Assignment) \
                and isinstance(s.then_body[0].value, A.Identifier) and s.then_body[0].value.name == i \
                and isinstance(s.then_body[1], A.Break) and getattr(s.then_body[1], "target", "loop") == "loop":
            v = s.then_body[0].target
            note = f" ({v} stays -1 if there is none)" if v in sentinels else ""
            out.append(f"  - stops at the first item {item_cond(s.cond)}, and remembers its position in {v}{note}")
            continue
        # stop at the first match: if (C) return E;
        if isinstance(s, A.If) and not s.else_body and len(s.then_body) == 1 \
                and isinstance(s.then_body[0], A.Return):
            pre, rest = once(s.cond)
            out.append(f"  - {pre}stops at the first item {item_cond(rest)}, and gives back "
                       f"{w.value(s.then_body[0].value)}")
            continue
        # count: if (C) k++;
        if isinstance(s, A.If) and not s.else_body and len(s.then_body) == 1 \
                and isinstance(s.then_body[0], N.Step) and s.then_body[0].delta == 1:
            k = w.expr(s.then_body[0].target)
            cond = item_cond(s.cond).replace("that is ", "that are ", 1)
            out.append(f"  - counts the items {cond}, in {k}{zero_note(k)}")
            continue
        # add up: total += item
        if isinstance(s, N.CompoundAssign) and s.op == "+" and isinstance(s.value, A.IndexAccess) \
                and s.value.collection == coll and isinstance(s.value.index, A.Identifier) \
                and s.value.index.name == i:
            t = w.expr(s.target)
            out.append(f"  - adds up all the items into {t}{zero_note(t)}")
            continue
        # keep largest/smallest: if (item > best) best = item;
        if isinstance(s, A.If) and not s.else_body and len(s.then_body) == 1 \
                and isinstance(s.cond, A.BinaryOp) and s.cond.op in (">", "<") \
                and isinstance(s.then_body[0], A.Assignment) \
                and isinstance(s.cond.left, A.IndexAccess) and isinstance(s.cond.right, A.Identifier) \
                and s.cond.right.name == s.then_body[0].target \
                and isinstance(s.then_body[0].value, A.IndexAccess) \
                and w.expr(s.then_body[0].value) == w.expr(s.cond.left):
            kind = "largest" if s.cond.op == ">" else "smallest"
            out.append(f"  - keeps the {kind} item seen so far in {s.then_body[0].target}")
            continue
        raise NoMatch
    stops = any("stops at the first" in l for l in out)
    head = (f"Goes through the items of {coll}, position 0 up to {w.expr(rep.count)} minus 1"
            + (" (it may stop early):" if stops else ":"))
    return [head] + out


# ------------------------------------------------------------------ per function

def _all_nodes(nodes):
    import dataclasses
    stack = list(nodes)
    while stack:
        x = stack.pop()
        yield x
        if dataclasses.is_dataclass(x):
            for f in dataclasses.fields(x):
                v = getattr(x, f.name)
                if isinstance(v, list):
                    stack.extend(i for i in v if dataclasses.is_dataclass(i))
                elif dataclasses.is_dataclass(v):
                    stack.append(v)


def _shorten_fields(a: A.Action, record: Optional[str]) -> A.Action:
    """In a function that works on one record, call its fields by their own
    names ("count" for "the count of what rb points to") -- but only if no
    other name in the function could be confused with them."""
    import copy
    import dataclasses
    if not record:
        return a
    names = {n for n, _ in a.params}
    fields = set()
    for x in _all_nodes(a.body):
        if isinstance(x, A.VarDecl):
            names.add(x.name)
        elif isinstance(x, A.Identifier):
            names.add(x.name)
        elif isinstance(x, N.Member) and x.arrow and isinstance(x.base, A.Identifier) and x.base.name == record:
            fields.add(x.field)
    if fields & names:
        return a

    def fix(x):
        if isinstance(x, N.Member) and x.arrow and isinstance(x.base, A.Identifier) and x.base.name == record:
            ident = A.Identifier(name=x.field, line=x.line)
            for attr in ("ctype", "is_pointer"):
                if hasattr(x, attr):
                    setattr(ident, attr, getattr(x, attr))
            return ident
        if dataclasses.is_dataclass(x):
            for f in dataclasses.fields(x):
                v = getattr(x, f.name)
                if isinstance(v, list):
                    setattr(x, f.name, [fix(i) if dataclasses.is_dataclass(i) else i for i in v])
                elif dataclasses.is_dataclass(v):
                    setattr(x, f.name, fix(v))
        return x
    b = copy.deepcopy(a)
    b.body = [fix(x) for x in b.body]
    return b


def summarize_function(a: A.Action, facts: Optional[dict] = None) -> List[str]:
    record_params = [n for n, t in a.params if t.startswith("pointer to ") and t.endswith(" record")]
    base = {"record_param": record_params[0] if len(record_params) == 1 else None}
    a = _shorten_fields(a, base["record_param"])
    w = SummaryWords(**base)
    takes = ", ".join(f"{n} ({_input_words(a, n, t, facts)})" for n, t in a.params) or "nothing"
    out = [f"Function {a.name}", f"  Takes: {takes}"]
    if a.ret_type != "nothing":
        out.append(f"  Gives back: a {a.ret_type}" if not a.ret_type.startswith(("a ", "an ")) else f"  Gives back: {a.ret_type}")
    out.append("  What it does:")
    body = list(a.body)
    lines = _summarize_block(body, w, base)
    out.extend("    " + l for l in lines)
    if facts is not None:
        out.append("  " + _changes_line(facts, base["record_param"]))
    return out


def _is_zero(e) -> bool:
    return isinstance(e, A.Literal) and str(getattr(e, "source", e.value)) in ("0", "0.0", "0.0f", "0.0F")


def _loop_explains(rest, name, marker_value=None) -> bool:
    """Only hide `v = -1` if the next loop really summarises it (else show it)."""
    for x in rest:
        if isinstance(x, A.Repeat):
            try:
                marker = A.VarDecl(name=name, type="whole number",
                                   value=marker_value or A.UnaryOp(op="-", operand=A.Literal(value=1)))
                text = " ".join(_item_loop(x, [marker], {"record_param": None}))
            except NoMatch:
                return False
            if marker_value is not None:
                return f"{name} (starting from" in text
            return f"in {name} " in text or f"position in {name}" in text
    return False


def _indexed(nodes, name) -> bool:
    import dataclasses
    stack = list(nodes)
    while stack:
        x = stack.pop()
        if isinstance(x, A.IndexAccess) and x.collection == name:
            return True
        if dataclasses.is_dataclass(x):
            for f in dataclasses.fields(x):
                v = getattr(x, f.name)
                if isinstance(v, list):
                    stack.extend(i for i in v if dataclasses.is_dataclass(i))
                elif dataclasses.is_dataclass(v):
                    stack.append(v)
    return False


def _input_words(a, name, words, facts) -> str:
    """Inputs in plain words, from how the code uses them (never guessed):
    a pointer read by position is a list; one the facts show being written
    through is changed in place."""
    changed = any(t["pointer"] == name for t in (facts or {}).get("writes_through_pointers", []))
    if words.startswith("pointer to ") and _indexed(a.body, name):
        elem = words[len("pointer to "):]
        ro = elem.startswith("read-only ")
        from .types_json import plural
        base = "list of " + plural(elem[len("read-only "):] if ro else elem)
        return base + (", changed in place" if changed else ", only read" if ro else "")
    if words.startswith("pointer to ") and words.endswith(" record"):
        rec = words[len("pointer to "):]
        return f"a {rec}, changed in place" if changed else f"a {rec}"
    if words.startswith("the caller's list of "):
        return words[len("the caller's "):] + (", changed in place" if changed else "")
    return words


def _summarize_block(body, w: SummaryWords, base: dict) -> List[str]:
    """Rules first; any statement no rule covers is shown as its exact step."""
    lines, before = [], []
    exact = Renderer()  # the exact steps, from the same (shortened) tree
    for idx, s in enumerate(body):
        try:
            if isinstance(s, N.Switch):
                var_text = w.expr(s.value)
                got = _mode_machine(s, w, var_text)
                lines.append(f"Moves {var_text} between modes, depending on its current mode:")
                lines.extend("  " + l for l in got)
                continue
            if isinstance(s, A.Repeat):
                lines.extend(_item_loop(s, before, base))
                continue
            if isinstance(s, A.VarDecl) and any(isinstance(x, A.Repeat) for x in body[idx + 1:]) \
                    and isinstance(s.value, A.UnaryOp) and s.value.op == "-" \
                    and isinstance(s.value.operand, A.Literal) and str(s.value.operand.value) == "1" \
                    and _loop_explains(body[idx + 1:], s.name):
                before.append(s)  # a "not found yet" marker, explained by the loop rule
                continue
            if isinstance(s, A.VarDecl) and _is_zero(s.value) \
                    and any(isinstance(x, A.Repeat) for x in body[idx + 1:]) \
                    and _loop_explains(body[idx + 1:], s.name, s.value):
                before.append(s)  # a count/total starting at zero, explained by the loop rule
                continue
            if isinstance(s, A.Return):
                # "If it gets through every item..." only when the loop just above
                # stops by GIVING BACK a value; a loop that stops with break
                # falls through to this same return either way.
                if lines and lines[-1].startswith("  - ") and ", and gives back " in lines[-1]:
                    lines.append(f"If it gets through every item without stopping, gives back {w.value(s.value)}.")
                else:
                    lines.append(f"Then gives back {w.value(s.value)}." if lines else f"Gives back {w.value(s.value)}.")
                continue
            if _simple(s):
                lines.append("- " + w.action(s))
                continue
            raise NoMatch
        except NoMatch:
            head, blocks = exact.stmt(s)
            lines.append("(exact step) " + head)
            for label, sub in blocks:
                if label:
                    lines.append("   " + label)
                if sub:
                    lines.extend(exact.steps(sub, "     "))
                elif sub is not None:
                    lines.append("     (does nothing)")
            before.append(s)
    return lines


def _changes_line(facts: dict, record_param: Optional[str]) -> str:
    """'Changes:' lists data it changes; 'Also:' lists everything else it does."""
    if facts["side_effects"] == "none":
        return "Changes: nothing outside itself."
    changes = []
    for t in facts.get("writes_through_pointers", []):
        who = f"the record it's given ({t['pointer']})" if t["pointer"] == record_param \
            else f"what {t['pointer']} points to"
        changes.append(("may change " if t["certainty"] == "may" else "") + who)
    changes += [f"shared variable {g}" for g in facts.get("writes_globals", [])]
    changes += [f"{v} (kept between calls)" for v in facts.get("keeps_state_between_calls", [])]
    also = []
    for e in facts.get("effects", []):
        if e.startswith("does input/output"):
            also.append("reads or writes input/output" + e[len("does input/output"):])
        elif e.startswith(("allocates", "frees", "can end")):
            also.append(e)
    for m in facts.get("possible_effects", []):
        if m.startswith("calls a function through a pointer"):
            also.append("calls a function through a pointer, which could do anything")
        elif m.startswith("calls ") and "not defined in this file" in m:
            also.append(f"calls {m.split()[1].rstrip(',')}, which isn't in this file")
        elif m.startswith("contains inline assembly"):
            also.append("contains inline assembly, which could do anything")
    local = facts.get("calls_local", [])
    if local:
        also.append(f"calls {_join_and(local)} (their effects aren't included here yet)")
    has_maybe = bool(facts.get("possible_effects"))
    lines = []
    if changes:
        lines.append(("Changes: " if has_maybe else "Changes: only ") + _join_and(changes) + ".")
    else:
        lines.append("Changes: no data outside itself.")
    if also:
        lines.append("Also: " + "; ".join(also) + ".")
    return "\n  ".join(lines)


def summarize(result, with_facts: bool = True) -> str:
    out = []
    for item in result.items:
        if isinstance(item, A.Action):
            out.append("\n".join(summarize_function(item, result.facts.get(item.name) if with_facts else None)))
        elif isinstance(item, N.EnumDecl):
            out.append(f"Modes of {item.name}: " + _join_or([n for n, _ in item.options]) + ".")
        elif isinstance(item, A.Shape):
            out.append(f"Record {item.name} holds " + _join_and([n for n, _ in item.fields]) + ".")
        elif isinstance(item, A.VarDecl):
            out.append(f"Shared variable {item.name} ({item.type}).")
    return "\n\n".join(out) + "\n"
