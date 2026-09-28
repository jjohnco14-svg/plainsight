"""Nodes -> plain-English steps.

Rules that keep the English exact:
- Grouping is always explicit: a + b * c is "a plus (b times c)".
- Whole-number division and remainder are named as such.
- A loop is called "N times" only when the lifter proved nothing changes N or
  the counter; otherwise every part of the loop is spelled out.
- Anything not handled is shown in brackets, never paraphrased.
"""
import re
from typing import List, Optional

from .dictum_bridge import A
from . import nodes as N

_WORDS = {
    "+": "plus", "-": "minus", "*": "times", "/": "divided by",
    "==": "is equal to", "!=": "is not equal to", "<": "is less than",
    ">": "is greater than", "<=": "is at most", ">=": "is at least",
    "&&": "and", "||": "or", "&": "bitwise-and", "|": "bitwise-or",
    "^": "bitwise-xor", "<<": "shifted left by", ">>": "shifted right by",
}
_CAT = {"||": "or", "&&": "and", "==": "cmp", "!=": "cmp", "<": "cmp", ">": "cmp",
        "<=": "cmp", ">=": "cmp", "+": "add", "-": "add", "*": "mul", "/": "mul",
        "%": "mul", "<<": "shift", ">>": "shift", "&": "bitand", "|": "bitor", "^": "bitxor"}


def _special(node) -> bool:
    """Ops rendered as a phrase ('the remainder of ...') always get grouped."""
    return isinstance(node, A.BinaryOp) and (node.op == "%" or getattr(node, "int_div", False))


_LOGIC_OPS = ("&&", "||")
_CMP_OPS = ("==", "!=", "<", ">", "<=", ">=")


def _is_phrase(child) -> bool:
    """Children that read as a multi-word phrase and would blur into a
    neighbouring operator without grouping ("calling f with a plus b")."""
    if isinstance(child, (N.Ternary, N.Cast, N.SizeOf, A.IndexAccess, N.Index, N.ItemCount,
                          N.AssignExpr)):
        return True
    if isinstance(child, N.MacroCall) and child.args:
        return True
    if isinstance(child, A.FuncCall) and child.args:
        return True
    if isinstance(child, N.Member) and child.arrow:
        return True
    if isinstance(child, A.UnaryOp) and child.op in ("&", "*", "~", "!"):
        return True
    if isinstance(child, A.UnaryOp) and isinstance(child.operand, A.BinaryOp):
        return True
    return False


def _needs_paren(parent: A.BinaryOp, child, side: str) -> bool:
    if _is_phrase(child):
        return True
    if not isinstance(child, A.BinaryOp):
        return False
    if _special(parent) or _special(child):
        return True
    pc, cc = _CAT.get(parent.op), _CAT.get(child.op)
    if pc in ("or", "and") and cc in ("cmp", "add", "mul", "shift"):
        return False
    if pc == "cmp" and cc in ("add", "mul", "shift"):
        return False
    if pc == cc and side == "left" and pc in ("or", "and", "add", "mul"):
        return False
    return True


def _article(words: str) -> str:
    if words.startswith(("text", "changeable text", "nothing", "read-only text")):
        return words
    return ("an " if words[:1] in "aeiou" else "a ") + words


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _join_or(items: List[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return ", ".join(items[:-1]) + f", or {items[-1]}"


def _join_and(items: List[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


_PRINTF_CONV = re.compile(r"%([-+ #0]*)(\d+|\*)?(?:\.(\d+|\*))?(hh|h|ll|l|j|z|t|L)?([diouxXeEfFgGaAcspn%])")


class Renderer:
    # ------------------------------------------------------------ expressions

    def expr(self, e) -> str:
        if e is None:
            return "nothing"
        if isinstance(e, A.Literal):
            src = str(getattr(e, "source", e.value))
            return src if src == "zero" else self._number(src)
        if isinstance(e, A.Identifier):
            if getattr(e, "named_constant", False):
                if e.name == "NULL":
                    return "NULL (points nowhere)"
                vt = getattr(e, "value_text", None)
                return f"{e.name} ({self._plain_value(vt)})" if vt else e.name
            return e.name
        if isinstance(e, N.CharLit):
            return self._char(e.source)
        if isinstance(e, N.StrLit):
            return e.source
        if isinstance(e, A.BinaryOp):
            return self._binary(e)
        if isinstance(e, A.UnaryOp) and e.op == "!":
            o = e.operand
            if self._is_boolean(o):
                if isinstance(o, A.BinaryOp) or _is_phrase(o) or isinstance(o, A.FuncCall):
                    return f"not ({self.expr(o)})"
                return f"not {self.expr(o)}"
            if getattr(o, "is_pointer", False):
                return f"{self._group(o)} is NULL"
            return f"{self._group(o)} is zero"
        if isinstance(e, A.UnaryOp):
            inner = self._group(e.operand)
            return {"-": f"negative {inner}", "+": inner, "!": f"not {inner}",
                    "~": f"the bitwise-not of {inner}", "&": f"the location of {inner}",
                    "*": f"the value {inner} points to"}.get(e.op, f"{e.op}{inner}")
        if isinstance(e, A.IndexAccess):
            return f"item {self._index(e.index)} of {e.collection}"
        if isinstance(e, N.Index):
            return f"item {self._index(e.index)} of {self._group(e.base)}"
        if isinstance(e, N.ItemCount):
            return f"the number of items in {e.name} ({e.count})"
        if isinstance(e, A.FieldAccess):
            return f"{e.obj}'s {e.field}"
        if isinstance(e, N.Member):
            base = self._group(e.base)
            if e.arrow:
                return f"the {e.field} of what {base} points to"
            if isinstance(e.base, (A.Identifier, A.FieldAccess)):
                return f"{base}'s {e.field}"
            return f"the {e.field} of {base}"
        if isinstance(e, A.FuncCall):
            args = _join_and([self.expr(a) for a in e.args])
            return f"the result of calling {e.name}" + (f" with {args}" if args else "")
        if isinstance(e, N.Cast):
            return f"{self._group(e.expr)} converted to {e.type_words}"
        if isinstance(e, N.Ternary):
            return f"{self._group(e.then)} if {self.cond(e.cond)}, otherwise {self._group(e.other)}"
        if isinstance(e, N.SizeOf):
            if e.expr is not None:
                return f"the size in bytes of {self._group(e.expr)}"
            return f"the size in bytes of {e.what}"
        if isinstance(e, N.ListLit) and getattr(e, "fields", None):
            return _join_and([f"{f} set to {self.expr(x)}" for f, x in zip(e.fields, e.items)])
        if isinstance(e, N.ListLit):
            items = [self.expr(x) for x in e.items]
            text = "the values " + _join_and(items) if items else "no values"
            if getattr(e, "rest_zero", False):
                text += ", with every other item set to zero"
            return text
        if isinstance(e, N.AssignExpr):
            return f"{self.expr(e.target)} (newly set to {self.expr(e.value)})"
        if isinstance(e, N.MacroCall):
            args = _join_and([self.expr(a) for a in e.args])
            if e.library:
                return f"the result of calling {e.name}" + (f" with {args}" if args else "")
            return f"the result of the macro {e.name}" + (f" with {args}" if args else "")
        if isinstance(e, N.Unsupported):
            return f"[Not yet supported: {e.what} (line {e.line})]"
        return f"[Not yet supported: {type(e).__name__}]"

    def _index(self, i) -> str:
        """An index that is itself a phrase must be grouped, or
        'item the head of what rb points to of items' is unreadable."""
        if isinstance(i, (A.Identifier, A.Literal, N.CharLit)):
            return self.expr(i)
        return f"({self.expr(i)})"

    def _group(self, e) -> str:
        s = self.expr(e)
        if isinstance(e, A.BinaryOp) or _is_phrase(e):
            return f"({s})"
        return s

    # A condition: C treats any nonzero value as true, so say so explicitly.
    @staticmethod
    def _is_boolean(e) -> bool:
        if isinstance(e, A.BinaryOp) and e.op in _LOGIC_OPS + _CMP_OPS:
            return True
        if isinstance(e, A.UnaryOp) and e.op == "!":
            return True
        if isinstance(e, A.Identifier) and getattr(e, "named_constant", False) \
                and e.name in ("true", "false"):
            return True
        return str(getattr(e, "ctype", "")).startswith(("truth value", "read-only truth value"))

    def cond(self, e) -> str:
        if self._is_boolean(e):
            return self.expr(e)
        if getattr(e, "is_pointer", False):
            return f"{self._group(e)} is not NULL"
        return f"{self._group(e)} is not zero"

    # A value: a comparison stored or returned in C is the number 1 or 0.
    def value(self, e) -> str:
        if isinstance(e, A.BinaryOp) and e.op in _LOGIC_OPS + _CMP_OPS or \
                isinstance(e, A.UnaryOp) and e.op == "!":
            return f"1 if {self.expr(e)}, otherwise 0"
        return self.expr(e)

    def _binary(self, e: A.BinaryOp) -> str:
        if e.op in _LOGIC_OPS:
            l, r = self.cond(e.left), self.cond(e.right)
        else:
            l, r = self.expr(e.left), self.expr(e.right)
        if isinstance(e.left, N.AssignExpr) and e.op in _CMP_OPS:
            l = f"{self.expr(e.left.target)}, newly set to {self.expr(e.left.value)},"
            return f"{l} {_WORDS.get(e.op, e.op)} {r}"
        if _needs_paren(e, e.left, "left"):
            l = f"({l})"
        if _needs_paren(e, e.right, "right"):
            r = f"({r})"
        if e.op == "%":
            return f"the remainder of {l} divided by {r}"
        if e.op == "/" and getattr(e, "int_div", False):
            return f"the whole-number part of {l} divided by {r}"
        return f"{l} {_WORDS.get(e.op, e.op)} {r}"

    @staticmethod
    def _plain_value(text: str) -> str:
        """A named constant's value in plain decimal: BRKINT (2), not BRKINT (0000002)."""
        t = text.rstrip("uUlL")
        try:
            if t.lower().startswith("0x"):
                return str(int(t, 16))
            if re.fullmatch(r"0[0-7]+", t):
                return str(int(t, 8))
        except ValueError:
            pass
        return Renderer._number(text)

    @staticmethod
    def _number(text: str) -> str:
        t = text.rstrip("uUlL")
        if t.lower().startswith("0x"):
            return f"{t} ({int(t, 16)})"
        if re.fullmatch(r"0[0-7]+", t):
            return f"{t} (octal, which is {int(t, 8)})"
        if re.fullmatch(r"[0-9.eE+-]+[fFlL]", t):
            t = t[:-1]
        return t

    @staticmethod
    def _char(src: str) -> str:
        names = {r"'\n'": "the new-line character", r"'\0'": "the end-of-text character",
                 r"'\t'": "the tab character", "' '": "the space character"}
        return names.get(src, f"the character {src}")

    # ------------------------------------------------------------ statements

    def steps(self, stmts, pad: str) -> List[str]:
        """Numbered steps. Labels ("Otherwise:") sit under the step's text;
        nested steps sit two spaces further in."""
        lines = []
        for i, s in enumerate(stmts, 1):
            head, blocks = self.stmt(s)
            lines.append(f"{pad}{i}. {head}")
            for label, body in blocks:
                if label is not None:
                    lines.append(f"{pad}   {label}")
                if body is not None:
                    lines.extend(self.steps(body, pad + "     ") if body else
                                 [f"{pad}     (does nothing)"])
        return lines

    def stmt(self, s):
        """Returns (sentence, [(label_or_None, sub_steps_or_None), ...])."""
        e = self.expr
        if isinstance(s, A.VarDecl):
            if getattr(s, "is_static", False):
                start = f", starting at {e(s.value)} before the program runs" if s.value else ""
                return f"Keeps {s.name} ({s.type}) between calls{start}.", []
            if s.value is None:
                return f"Creates {s.name} ({s.type}), not yet given a value.", []
            if isinstance(s.value, N.ListLit):
                word = "with" if getattr(s.value, "fields", None) else "holding"
                return f"Creates {s.name} ({s.type}), {word} {e(s.value)}.", []
            return f"Creates {s.name} ({s.type}), starting at {self.value(s.value)}.", []
        if isinstance(s, A.Assignment) and isinstance(s.value, N.Ternary):
            t = s.value
            return (f"Sets {s.target} to {self._group(t.then)} if {self.cond(t.cond)}; "
                    f"otherwise sets it to {self._group(t.other)}."), []
        if isinstance(s, N.Store) and isinstance(s.value, N.Ternary):
            t = s.value
            return (f"Puts {self._group(t.then)} into {self._store_target(s.target)} "
                    f"if {self.cond(t.cond)}; otherwise puts {self._group(t.other)} there."), []
        if isinstance(s, A.Assignment):
            return f"Sets {s.target} to {self.value(s.value)}.", []
        if isinstance(s, N.Store):
            return f"Puts {self.value(s.value)} into {self._store_target(s.target)}.", []
        if isinstance(s, N.CompoundAssign):
            t, v = e(s.target), self._group(s.value)
            if s.op == "+":
                return f"Adds {v} to {t}.", []
            if s.op == "-":
                return f"Subtracts {v} from {t}.", []
            if s.op == "*":
                return f"Multiplies {t} by {v}.", []
            if s.op == "/":
                keep = ", keeping only the whole-number part" if s.int_div else ""
                return f"Divides {t} by {v}{keep}.", []
            if s.op == "%":
                return f"Replaces {t} with the remainder of {t} divided by {v}.", []
            return f"Sets {t} to {t} {_WORDS.get(s.op, s.op)} {v}.", []
        if isinstance(s, N.Step):
            t = e(s.target)
            return (f"Adds 1 to {t}." if s.delta > 0 else f"Subtracts 1 from {t}."), []
        if isinstance(s, A.Return):
            if s.value is None:
                return "Stops here.", []
            return f"Stops and gives back {self.value(s.value)}.", []
        if isinstance(s, A.FuncCall):
            return self._call_stmt(s), []
        if isinstance(s, A.If):
            blocks = [(None, s.then_body)]
            if s.else_body:
                if len(s.else_body) == 1 and isinstance(s.else_body[0], A.If):
                    inner = s.else_body[0]
                    head2, blocks2 = self.stmt(inner)
                    blocks.append((f"Otherwise, i{head2[1:]}", None))
                    blocks.extend(blocks2)
                else:
                    blocks.append(("Otherwise:", s.else_body))
            return f"If {self.cond(s.cond)}:", blocks
        if isinstance(s, A.While):
            return f"While {self.cond(s.cond)}, repeats:", [(None, s.body)]
        if isinstance(s, A.Repeat):
            up_to = "up to " if self._breaks(s.body) else ""
            return (f"Repeats {up_to}{self._group(s.count)} times, with {s.counter} counting from 0:",
                    [(None, s.body)])
        if isinstance(s, N.ForLoop):
            blocks = []
            if s.init:
                blocks.append(("Before starting:", s.init))
            cond = f"Keeps going while {self.cond(s.cond)}." if s.cond is not None \
                else "Keeps going forever, until something inside stops it."
            blocks.append((cond, None))
            if s.step:
                blocks.append(("After each round:", s.step))
            blocks.append(("Each round:", s.body))
            return "Loop:", blocks
        if isinstance(s, N.DoWhile):
            return (f"Repeats the following at least once, then again while {self.cond(s.cond)}:",
                    [(None, s.body)])
        if isinstance(s, A.Break):
            if getattr(s, "target", "loop") == "switch":
                return "Leaves the case check (goes on to the step after it).", []
            return "Stops the loop.", []
        if isinstance(s, N.Switch):
            return self._switch(s)
        if isinstance(s, N.Continue):
            return "Skips the rest of this round and moves to the next round.", []
        if isinstance(s, N.ExprStmt) and isinstance(s.expr, N.StrLit) and s.expr.source == "__FALLTHROUGH__":
            return "Then continues straight into the next case's steps (there is no break here).", []
        if isinstance(s, N.ExprStmt) and isinstance(s.expr, N.MacroCall):
            m = s.expr
            args = _join_and([e(a) for a in m.args])
            what = f"Calls {m.name}" if m.library else f"Uses the macro {m.name}"
            return what + (f" with {args}." if args else "."), []
        if isinstance(s, N.ExprStmt):
            return f"Works out {e(s.expr)} (the result is not used).", []
        if isinstance(s, N.Unsupported):
            return f"[Not yet supported: {s.what} (line {s.line})] -- this step is not described.", []
        return f"[Not yet supported: {type(s).__name__}] -- this step is not described.", []

    def _switch(self, s):
        blocks = []
        for g in s.groups:
            cases = [x for x in g.labels if x is not None]
            has_default = any(x is None for x in g.labels)
            if cases:
                label = "If it is " + _join_or([self.expr(x) for x in cases])
                label += ", or in any other case:" if has_default else ":"
            else:
                label = "In any other case:"
            body = list(g.body)
            if g.falls_through:
                body.append(N.ExprStmt(expr=N.StrLit(source="__FALLTHROUGH__")))
            blocks.append((label, body))
        if not s.has_default:
            blocks.append((f"If it is none of these, nothing here happens.", None))
        return f"Checks the value of {self.expr(s.value)}:", blocks

    def _store_target(self, t) -> str:
        if isinstance(t, A.UnaryOp) and t.op == "*":
            return f"the location {self._group(t.operand)} points to"
        return self.expr(t)

    @staticmethod
    def _breaks(body, own: bool = True) -> bool:
        """Can this loop stop before its count runs out? True if its body
        (at any depth) returns, ends the program, or has a break that
        belongs to THIS loop (a break inside an inner loop or a switch
        doesn't)."""
        for s in body:
            if isinstance(s, A.Break) and own and getattr(s, "target", "loop") == "loop":
                return True
            if isinstance(s, A.Return):
                return True
            if isinstance(s, A.FuncCall) and s.name in ("exit", "abort", "_Exit"):
                return True
            if isinstance(s, N.Unsupported):
                return True  # can't rule it out (e.g. goto)
            if isinstance(s, A.If):
                if Renderer._breaks(s.then_body, own) or Renderer._breaks(s.else_body, own):
                    return True
            if isinstance(s, N.Switch):
                if any(Renderer._breaks(g.body, False) for g in s.groups):
                    return True
            if isinstance(s, (A.While, A.Repeat, N.ForLoop, N.DoWhile)):
                if Renderer._breaks(s.body, False):
                    return True
        return False

    # ------------------------------------------------------------ calls / printing

    def _call_stmt(self, c: A.FuncCall) -> str:
        if c.name == "printf" and c.args:
            t = self._printf(c.args[0], c.args[1:])
            if t == '"" and starts a new line.':
                return "Starts a new line."
            if t:
                return "Prints " + t
        if c.name == "fprintf" and len(c.args) >= 2:
            t = self._printf(c.args[1], c.args[2:])
            stream = c.args[0]
            sname = stream.name if isinstance(stream, A.Identifier) else None
            if t and sname == "stderr":
                return "Prints to the error output " + t
            if t and sname == "stdout":
                return "Prints " + t
            if t:
                return f"Writes to {self.expr(stream)}: " + t
        if c.name == "puts" and len(c.args) == 1:
            return f"Prints {self._text_arg(c.args[0])} and starts a new line."
        if c.name == "putchar" and len(c.args) == 1:
            a = c.args[0]
            if isinstance(a, N.CharLit) and a.source == r"'\n'":
                return "Starts a new line."
            return f"Prints {self.expr(a)}."
        if c.name == "scanf" and c.args:
            t = self._scanf(c.args[0], c.args[1:])
            if t:
                return t
        if c.name == "memset" and len(c.args) == 3:
            return (f"Fills the first {self._group(c.args[2])} bytes of {self._group(c.args[0])} "
                    f"with the byte value {self._group(c.args[1])}.")
        if c.name in ("memcpy", "memmove") and len(c.args) == 3:
            return (f"Copies {self._group(c.args[2])} bytes from {self._group(c.args[1])} "
                    f"into {self._group(c.args[0])}.")
        if c.name == "strcpy" and len(c.args) == 2:
            return f"Copies {self._text_arg(c.args[1])} into {self._group(c.args[0])}."
        if c.name == "free" and len(c.args) == 1:
            return f"Frees the memory {self._group(c.args[0])} points to."
        if c.name == "exit" and len(c.args) == 1:
            return f"Ends the whole program with status {self.expr(c.args[0])}."
        args = _join_and([self.expr(a) for a in c.args])
        return f"Calls {c.name}" + (f" with {args}." if args else ".")

    _SCANF_WORDS = {"d": "a whole number", "i": "a whole number", "u": "a non-negative whole number",
                    "f": "a fractional number (single precision)", "lf": "a fractional number",
                    "s": "one word", "c": "one character"}

    def _scanf(self, fmt_node, args) -> Optional[str]:
        """scanf("%d", &age) -> 'Reads a whole number from the input into age.'
        Only when the format is nothing but conversions and spaces."""
        if not isinstance(fmt_node, N.StrLit):
            return None
        convs = re.findall(r"%(l?[diufsc])", fmt_node.value)
        rest = re.sub(r"%l?[diufsc]", "", fmt_node.value)
        if rest.strip() or len(convs) != len(args):
            return None
        parts = []
        for conv, a in zip(convs, args):
            if conv not in self._SCANF_WORDS:
                return None
            if isinstance(a, A.UnaryOp) and a.op == "&":
                dest = self.expr(a.operand)
            elif conv == "s":
                dest = self.expr(a)
            else:
                return None
            parts.append(f"{self._SCANF_WORDS[conv]} into {dest}")
        return "Reads " + _join_and(parts) + " from the input."

    def _text_arg(self, a) -> str:
        if isinstance(a, N.StrLit):
            return a.source
        return f"the text in {self._group(a)}"

    def _printf(self, fmt_node, args) -> Optional[str]:
        """printf("Total: %d\\n", t) -> '"Total: [t]" and starts a new line.'
        Returns None (caller falls back to a plain 'Calls printf ...') if the
        format can't be matched exactly to the arguments."""
        if not isinstance(fmt_node, N.StrLit):
            return None
        fmt = fmt_node.value
        newline_end = fmt.endswith("\n")
        if newline_end:
            fmt = fmt[:-1]
        out, pos, ai = [], 0, 0
        for m in _PRINTF_CONV.finditer(fmt):
            out.append(fmt[pos:m.start()])
            pos = m.end()
            flags, width, prec, length, conv = m.groups()
            if conv == "%":
                out.append("%")
                continue
            if width == "*" or prec == "*" or conv == "n" or ai >= len(args):
                return None
            arg = self.expr(args[ai])
            ai += 1
            simple = not flags and not width
            if simple and conv in "diu" and prec is None:
                out.append(f"[{arg}]")
            elif simple and conv in "fF":
                places = int(prec) if prec is not None else 6
                out.append(f"[{arg}, to {places} decimal place{'' if places == 1 else 's'}]")
            elif simple and conv == "s" and prec is None:
                out.append(f"[{arg}]")
            elif simple and conv == "c":
                out.append(f"[{arg} as a character]")
            elif simple and conv in "xX" and prec is None:
                out.append(f"[{arg} in hexadecimal]")
            elif simple and conv == "p":
                out.append(f"[{arg} as a memory address]")
            else:
                out.append(f"[{arg}, formatted as {m.group(0)}]")
        if ai != len(args):
            return None
        out.append(fmt[pos:])
        text = "".join(out)
        if any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
            return None
        text = text.replace("\n", "(new line)").replace("\t", "(tab)")
        return f'"{text}"' + (" and starts a new line." if newline_end else ".")

    # ------------------------------------------------------------ file level

    def file(self, result, with_facts=False) -> str:
        out = []
        for item in result.items:
            if isinstance(item, A.Action):
                out.append("\n".join(self.function(item, result.facts.get(item.name) if with_facts else None)))
            elif isinstance(item, A.VarDecl):
                if getattr(item, "is_extern", False):
                    out.append(f"Uses shared variable {item.name} ({item.type}), defined in another file.")
                elif isinstance(item.value, N.ListLit) and getattr(item.value, "fields", None):
                    out.append(f"Shared variable {item.name} ({item.type}), with {self.expr(item.value)}.")
                elif item.value is not None:
                    out.append(f"Shared variable {item.name} ({item.type}), starting at {self.expr(item.value)}.")
                else:
                    out.append(f"Shared variable {item.name} ({item.type}), starting at zero.")
            elif isinstance(item, A.Shape):
                fields = _join_and([f"{n} ({t})" for n, t in item.fields])
                out.append(f"Record {item.name} holds {fields}." if fields else f"Record {item.name} holds nothing.")
            elif isinstance(item, N.EnumDecl):
                opts = _join_and([f"{n} ({v})" for n, v in item.options])
                out.append(f"Choice {item.name} has the options {opts}." if opts
                           else f"Choice {item.name} has no options.")
            elif isinstance(item, N.Unsupported):
                out.append(f"[Not yet supported: {item.what} (line {item.line})]")
        cov = result.coverage
        covered = cov["statements"] - cov["not_yet_supported"]
        footer = f"Covered: {covered} of {cov['statements']} statements."
        if cov["not_yet_supported"]:
            footer += f" Not yet supported: {cov['not_yet_supported']} (shown in brackets above)."
        out.append(footer)
        return "\n\n".join(out) + "\n"

    def function(self, a: A.Action, facts=None) -> List[str]:
        takes = ", ".join(f"{n} ({t})" for n, t in a.params) or "nothing"
        lines = [f"Function {a.name} (line {a.line})",
                 f"  Takes: {takes}",
                 f"  Gives back: {_article(a.ret_type)}",
                 "  Steps:"]
        lines.extend(self.steps(a.body, "    ") if a.body else ["    (none)"])
        if facts:
            lines.append("  Facts:")
            lines.append(f"    Side effects: {facts['side_effects']}")
            for eff in facts["effects"]:
                lines.append(f"      - {eff}")
            for eff in facts["possible_effects"]:
                lines.append(f"      - (may) {eff}")
            if facts["reads_globals"]:
                lines.append("    Reads shared variables: " + ", ".join(facts["reads_globals"]))
            calls = facts["calls_local"] + facts["calls_external"]
            if calls:
                lines.append("    Calls: " + ", ".join(calls))
        return lines


def render(result, with_facts=False) -> str:
    return Renderer().file(result, with_facts)
