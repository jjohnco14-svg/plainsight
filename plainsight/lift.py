"""Entry point for reading C, plus helpers shared by the frontend and renderer.

The frontend is lift_json.py (clang's full JSON syntax tree). The original
token-reading libclang frontend was removed on 2026-09-27 after a parity run:
identical on all golden files, and on real-world code it crashed (sds.c) and
misdescribed calls through function pointers and macros (see PROJECT.md).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .dictum_bridge import A
from . import nodes as N


class CompileError(Exception):
    pass


@dataclass
class FileResult:
    path: str
    items: List[object] = field(default_factory=list)   # top-level nodes, in source order
    facts: Dict[str, dict] = field(default_factory=dict)  # function name -> facts
    coverage: Dict[str, int] = field(default_factory=dict)


def parse(path: str, extra_args: Optional[List[str]] = None) -> FileResult:
    """Read a C file through clang's JSON syntax tree."""
    from .lift_json import parse as parse_json
    return parse_json(path, extra_args)


_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'",
        "a": "\a", "b": "\b", "f": "\f", "v": "\v", "?": "?"}


def decode_c_string(tok: str) -> str:
    body = tok[tok.index('"') + 1: -1]
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "x":
                j = i + 2
                while j < len(body) and body[j] in "0123456789abcdefABCDEF":
                    j += 1
                out.append(chr(int(body[i + 2:j], 16)))
                i = j
                continue
            if nxt in "01234567" and not (nxt == "0" and (i + 2 >= len(body) or body[i + 2] not in "01234567")):
                j = i + 1
                while j < len(body) and j < i + 4 and body[j] in "01234567":
                    j += 1
                out.append(chr(int(body[i + 1:j], 8)))
                i = j
                continue
            out.append(_ESC.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------- tree queries

def _children(n):
    import dataclasses
    if not dataclasses.is_dataclass(n):
        return
    for f in dataclasses.fields(n):
        v = getattr(n, f.name)
        if isinstance(v, list):
            for x in v:
                if dataclasses.is_dataclass(x):
                    yield x
        elif dataclasses.is_dataclass(v):
            yield v


def _any_node(nodes, pred):
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if pred(n):
            return True
        stack.extend(_children(n))
    return False

def names_in(e):
    """Variable names an expression reads, or None if it contains a call/unknown node."""
    if isinstance(e, A.Identifier):
        return {e.name}
    if isinstance(e, A.Literal) or isinstance(e, (N.CharLit, N.StrLit, N.SizeOf)):
        return set()
    if isinstance(e, A.BinaryOp):
        l, r = names_in(e.left), names_in(e.right)
        return None if l is None or r is None else l | r
    if isinstance(e, A.UnaryOp):
        return names_in(e.operand)
    if isinstance(e, A.FieldAccess):
        return {e.obj}
    if isinstance(e, A.IndexAccess):
        i = names_in(e.index)
        return None if i is None else {e.collection} | i
    if isinstance(e, N.Member):
        return names_in(e.base)
    if isinstance(e, N.Cast):
        return names_in(e.expr)
    return None


def _target_root_name(t):
    if isinstance(t, A.Identifier):
        return t.name
    if isinstance(t, A.IndexAccess):
        return t.collection
    if isinstance(t, A.FieldAccess):
        return t.obj
    if isinstance(t, (N.Index, N.Member)):
        return _target_root_name(t.base)
    if isinstance(t, A.UnaryOp):
        return _target_root_name(t.operand)
    return None


def written_names(stmts):
    """Names of variables assigned anywhere in a statement list (including nested blocks)."""
    out = set()
    for s in stmts:
        if isinstance(s, A.Assignment):
            out.add(s.target)
        elif isinstance(s, (N.Store, N.CompoundAssign, N.Step)):
            r = _target_root_name(s.target)
            if r:
                out.add(r)
        elif isinstance(s, A.VarDecl):
            out.add(s.name)
        elif isinstance(s, N.Unsupported):
            out.add("\0unknown")
        for attr in ("then_body", "else_body", "body", "init", "step"):
            sub = getattr(s, attr, None)
            if isinstance(sub, list):
                out |= written_names(sub)
    return out
