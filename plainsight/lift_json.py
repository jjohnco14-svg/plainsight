"""Read C through clang's full JSON syntax tree and build Plainsight's nodes.

Why this replaced the libclang lifter: clang's JSON states everything the
old lifter had to guess from tokens -- each operator, prefix/postfix, every
hidden conversion (e.g. an array quietly becoming a pointer), each for-loop
slot, and where every macro was used. No token lists are read anywhere.

Rule (unchanged): every construct is lifted exactly or becomes Unsupported.
"""
import json
import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional

from .dictum_bridge import A
from . import nodes as N
from .facts import (FnFacts, PURE_LIB, WRITES_FIRST_ARG, WRITES_LATER_ARGS,
                    ALLOC_FUNCS, FREE_FUNCS, END_FUNCS, LIBRARY_MACROS, IO_FUNCS)
from .lift import CompileError, FileResult, decode_c_string, names_in, written_names, _any_node
from .types_json import TypeWords, plural

STD = "-std=c11"


def find_clang() -> str:
    exe = os.environ.get("PLAINSIGHT_CLANG") or shutil.which("clang")
    if not exe:
        raise CompileError("clang is not installed (needed to read C). Install it "
                           "(e.g. `sudo apt install clang`) or set PLAINSIGHT_CLANG.")
    return exe


def _run(args) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def parse(path: str, extra_args: Optional[List[str]] = None) -> FileResult:
    from .lang import prepare, read_flags
    clang = find_clang()
    extra = extra_args or []
    path = prepare(path)  # an Arduino sketch is read from a prepared copy (same line numbers)
    flags = read_flags(path)
    dump = _run([clang, "-fsyntax-only", *flags, *extra, "-Xclang", "-ast-dump=json", path])
    if dump.returncode != 0:
        first = next((l for l in dump.stderr.splitlines() if "error:" in l), dump.stderr.strip())
        m = re.search(r":(\d+):\d+: error: (.*)", first)
        raise CompileError(f"{m.group(2)} (line {m.group(1)})" if m else first)
    macros = _run([clang, "-E", "-dM", *flags, *extra, path]).stdout
    tree = json.loads(dump.stdout)
    _fill_locations(tree)
    with open(path, "rb") as fh:
        source = fh.read()
    return JsonLifter(tree, path, source, _parse_macros(macros)).run()


def _parse_macros(text: str) -> Dict[str, Optional[str]]:
    """name -> value for object-like macros with a one-token value;
    name -> None for function-like macros (isspace(c), ...)."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"#define (\w+)(\(?)(.*)$", line)
        if not m:
            continue
        name, paren, rest = m.groups()
        if paren:
            out[name] = None
        else:
            v = rest.strip()
            if v and " " not in v:
                out[name] = v
    for name in list(out):
        v, seen = out[name], 0
        while v is not None and v in out and out[v] is not None and seen < 10:
            v, seen = out[v], seen + 1
        if v is not None:
            out[name] = v
    return out


def _fill_locations(tree):
    """Clang's JSON leaves out a location's file/line when unchanged from the
    previous location it printed. Walk in print order and fill them back in."""
    state = {"file": None, "line": None}
    stack = [tree]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if "offset" in o:
                if "file" in o:
                    state["file"] = o["file"]
                else:
                    o["file"] = state["file"]
                if "line" in o:
                    state["line"] = o["line"]
                else:
                    o["line"] = state["line"]
            stack.extend(reversed(list(o.values())))
        elif isinstance(o, list):
            stack.extend(reversed(o))


def _loc(l: dict) -> dict:
    """The place in the user's file: for macro-produced code, where the macro was used."""
    return (l or {}).get("expansionLoc", l or {})


_IMPLICIT = ("ImplicitCastExpr", "ParenExpr", "ConstantExpr", "ExprWithCleanups", "MaterializeTemporaryExpr",
             "CXXBindTemporaryExpr")


def _unwrap(n):
    while n and n.get("kind") in _IMPLICIT and len(n.get("inner", [])) == 1:
        n = n["inner"][0]
    return n


def _kids(n):
    return n.get("inner", []) if n else []


class JsonLifter:
    def __init__(self, tree, path, source, macros):
        self.tree = tree
        self.path = path
        self.source = source
        self.macros = macros
        self.fn: Optional[FnFacts] = None
        self._quiet = False
        self._breakables: List[str] = []  # innermost last: "loop" or "switch"
        self.total = 0
        self.unsupported_count = 0
        self.global_ids = set()
        self.static_ids = set()
        self.param_ids = set()
        typedefs, self.record_names = {}, {}
        for d in tree.get("inner", []):
            if d.get("kind") == "TypedefDecl":
                typedefs[d.get("name")] = d.get("type", {}).get("qualType", "")
                for t in d.get("inner", []):
                    owned = t.get("ownedTagDecl")
                    if owned:
                        self.record_names[owned["id"]] = d.get("name")
            if d.get("kind") == "VarDecl":
                self.global_ids.add(d.get("id"))
            if d.get("kind") in ("CXXRecordDecl", "RecordDecl") and d.get("name") and d.get("completeDefinition"):
                typedefs.setdefault(d["name"], f"struct {d['name']}")   # C++: a struct's name is a type
            if d.get("kind") == "EnumDecl" and d.get("name"):
                typedefs.setdefault(d["name"], f"enum {d['name']}")
        # record type spelling -> its field names, for `{.a = 5}` style initializers
        self.record_fields = {}
        for d in tree.get("inner", []):
            if d.get("kind") in ("RecordDecl", "CXXRecordDecl") and d.get("completeDefinition"):
                names = [f.get("name", "") for f in _kids(d) if f.get("kind") == "FieldDecl"]
                if d.get("name"):
                    self.record_fields[f"struct {d['name']}"] = names
                if self.record_names.get(d.get("id")):
                    self.record_fields[self.record_names[d["id"]]] = names
        self.types = TypeWords(typedefs)

    # ------------------------------------------------------------ utilities

    def _in_main(self, l) -> bool:
        return _loc(l).get("file") == self.path

    def _line(self, n) -> int:
        if "loc" in n and _loc(n["loc"]).get("line"):
            return _loc(n["loc"])["line"]
        return _loc(n.get("range", {}).get("begin", {})).get("line", 0)

    @staticmethod
    def _written(l: dict) -> dict:
        """Where a token was physically written. For a macro ARGUMENT that's
        its spelling location (inside the macro's parentheses), not the
        place the macro name was used."""
        ex = (l or {}).get("expansionLoc")
        if ex and ex.get("isMacroArgExpansion") and "spellingLoc" in l:
            return l["spellingLoc"]
        return _loc(l)

    def _text(self, n) -> Optional[str]:
        """Exact source text of a node, if it lies in the user's file."""
        r = n.get("range", {})
        b, e = self._written(r.get("begin", {})), self._written(r.get("end", {}))
        if b.get("file") != self.path or e.get("file") != self.path or "offset" not in b:
            return None
        return self.source[b["offset"]: e["offset"] + e.get("tokLen", 0)].decode("utf-8", "replace")

    # -------- facts for parts the English can't describe yet
    #
    # Every gap in the English still gets complete facts: the node is walked
    # (quietly: nothing counted, nothing rendered) through clang's full tree,
    # recording reads, writes and calls. One general walk, not per-case code.

    _GAP_STMTS = ("SwitchStmt", "CaseStmt", "DefaultStmt", "LabelStmt", "GotoStmt",
                  "IndirectGotoStmt")

    def _facts_walk(self, n):
        """Called only in quiet mode. Never lifts `n` itself through the
        normal path again (that is what just failed) -- only its parts."""
        k = n.get("kind", "")
        kids = _kids(n)
        if k in ("GCCAsmStmt", "MSAsmStmt"):
            self.fn.opaque = True
        elif k == "BinaryOperator" and n.get("opcode") == "=" and len(kids) == 2:
            self._record_write(kids[0])
            self._expr(kids[0], lvalue=True)
            self._expr(kids[1])
        elif k == "CompoundAssignOperator" and len(kids) == 2:
            self._record_write(kids[0])
            self._expr(kids[0])
            self._expr(kids[1])
        elif k == "UnaryOperator" and n.get("opcode") in ("++", "--") and kids:
            self._record_write(kids[0])
            self._expr(kids[0])
        elif k == "CallExpr":
            callee = _unwrap(kids[0]) if kids else {}
            decl = callee.get("referencedDecl") if callee.get("kind") == "DeclRefExpr" else None
            if decl and decl.get("kind") == "FunctionDecl":
                self._call(n)
            else:
                self.fn.calls_through_pointer = True
                for x in kids:
                    self._expr(x)
        elif k in self._GAP_STMTS or (k.endswith("Stmt") and k not in self._LIFTABLE_STMTS):
            for x in kids:
                self._facts_node(x)
        else:
            for x in kids:
                self._expr(x)

    _LIFTABLE_STMTS = ("CompoundStmt", "DeclStmt", "ReturnStmt", "IfStmt", "WhileStmt", "DoStmt",
                       "ForStmt", "BreakStmt", "ContinueStmt", "NullStmt")

    def _facts_node(self, x):
        k = x.get("kind", "")
        if not k:
            return
        if k in self._GAP_STMTS:
            self._facts_walk(x)
        elif k.endswith("Stmt"):
            self._stmt(x)
        else:
            self._expr_stmt(x)

    def _unsupported(self, n, what):
        if self.fn is not None:
            if self._quiet:
                self._facts_walk(n)
            else:
                self._quiet = True
                try:
                    self._facts_walk(n)
                finally:
                    self._quiet = False
        if self._quiet:
            return N.Unsupported(what=what, line=self._line(n))
        self.unsupported_count += 1
        line = self._line(n)
        if self.fn is not None:
            self.fn.unsupported.append({"what": what, "line": line})
        return N.Unsupported(what=what, source=(self._text(n) or "")[:80], line=line)

    def _words(self, t):
        return self.types.words(t)

    # ------------------------------------------------------------ top level

    def run(self) -> FileResult:
        res = FileResult(path=self.path)
        top = [d for d in self.tree.get("inner", [])
               if not d.get("isImplicit") and self._in_main(d.get("loc", {}))]
        self.local_functions = {d["name"] for d in top if d.get("kind") == "FunctionDecl"
                                and any(k.get("kind") == "CompoundStmt" for k in _kids(d))}
        for d in top:
            node = self._top(d)
            if node is not None:
                res.items.append(node)
                if isinstance(node, A.Action):
                    res.facts[node.name] = node.facts.finalize(self.local_functions)
        res.coverage = {"statements": self.total, "not_yet_supported": self.unsupported_count}
        return res

    def _top(self, d):
        k = d.get("kind")
        if k == "FunctionDecl":
            body = [x for x in _kids(d) if x.get("kind") == "CompoundStmt"]
            return self._function(d, body[0]) if body else None
        if k == "VarDecl":
            self.total += 1
            v = A.VarDecl(name=d.get("name", ""), type=self._words(d.get("type")),
                          value=self._init(d), line=self._line(d))
            v.is_global = True
            v.is_extern = d.get("storageClass") == "extern" and "init" not in d
            v.c_type = d.get("type", {}).get("qualType", "")
            return v
        if k in ("RecordDecl", "CXXRecordDecl"):
            if d.get("tagUsed") == "union":
                self.total += 1
                return self._unsupported(d, "union")
            if not d.get("completeDefinition"):
                return None
            name = d.get("name") or self.record_names.get(d.get("id")) or "(unnamed)"
            fields = [(f.get("name", ""), self._words(f.get("type")))
                      for f in _kids(d) if f.get("kind") == "FieldDecl"]
            shape = A.Shape(name=name, fields=fields, line=self._line(d))
            # how C spells this type, and each field's C type (used to build test programs)
            shape.c_name = f"struct {d['name']}" if d.get("name") else self.record_names.get(d.get("id"))
            shape.c_fields = [(f.get("name", ""), f.get("type", {}).get("qualType", ""),
                               f.get("type", {}).get("desugaredQualType", ""))
                              for f in _kids(d) if f.get("kind") == "FieldDecl"]
            return shape
        if k == "EnumDecl":
            self.total += 1
            name = d.get("name") or self.record_names.get(d.get("id")) or "(unnamed)"
            options, value = [], -1
            for c in _kids(d):
                if c.get("kind") != "EnumConstantDecl":
                    continue
                given = [x for x in _kids(c) if x.get("kind") == "ConstantExpr" and "value" in x]
                value = int(given[0]["value"]) if given else value + 1
                options.append((c.get("name", ""), value))
            return N.EnumDecl(name=name, options=options, line=self._line(d))
        return None  # typedefs and prototypes change no behaviour

    def _function(self, d, body_node):
        params_json = [p for p in _kids(d) if p.get("kind") == "ParmVarDecl"]
        params = [(p.get("name", ""), self._param_words(p)) for p in params_json]
        ret = self._words(self._return_type(d.get("type", {}).get("qualType", "")))
        facts = FnFacts(name=d["name"], line=self._line(d),
                        params=[{"name": n, "type": t} for n, t in params], returns=ret)
        facts.pointer_params = {p.get("name", "") for p in params_json
                                if self.types.is_pointer(p.get("type"))}
        self.param_ids = {p.get("id") for p in params_json}
        self.fn = facts
        body = self._block(body_node)
        self.fn = None
        act = A.Action(name=d["name"], params=params, ret_type=ret, body=body, line=self._line(d))
        act.facts = facts
        act.c_params = [(p.get("name", ""), p.get("type", {}).get("qualType", ""),
                         p.get("type", {}).get("desugaredQualType", "")) for p in params_json]
        act.macros = self.macros
        act.c_return = self._return_type(d.get("type", {}).get("qualType", ""))
        return act

    def _param_words(self, p) -> str:
        """Clang reports `int a[]` as `int *` (which is what C passes). The
        declaration's own text says it was written as a list; any size in the
        brackets isn't enforced, so it isn't shown."""
        text = self._text(p) or ""
        t = p.get("type", {}).get("qualType", "")
        if "[" in text and self.types.is_pointer(p.get("type")):
            return "the caller's list of " + plural(self._words(self.types.pointee(t)))
        return self._words(p.get("type"))

    @staticmethod
    def _return_type(fn_qt: str) -> str:
        depth = 0
        for i in range(len(fn_qt) - 1, -1, -1):
            ch = fn_qt[i]
            if ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
                if depth == 0:
                    return fn_qt[:i].strip()
        return fn_qt

    # ------------------------------------------------------------ statements

    def _count(self):
        if self._quiet:
            return
        self.total += 1
        if self.fn:
            self.fn.statements += 1

    def _uncount(self):
        if self._quiet:
            return
        self.total -= 1
        if self.fn:
            self.fn.statements -= 1

    def _block(self, n) -> List[object]:
        if n.get("kind") == "CompoundStmt":
            out = []
            for s in _kids(n):
                out.extend(self._stmt(s))
            return out
        return self._stmt(n)

    def _stmt(self, n) -> List[object]:
        k = n.get("kind")
        if not k or k == "NullStmt":
            return []
        if k == "CompoundStmt":
            return self._block(n)
        self._count()
        line = self._line(n)
        kids = _kids(n)
        if k == "DeclStmt":
            self._uncount()
            out = []
            for d in kids:
                self._count()
                if d.get("kind") != "VarDecl":
                    kind = re.sub(r"(?<!^)(?=[A-Z])", " ", d.get("kind", "decl")).lower()
                    out.append(self._unsupported(d, f"local {kind}"))
                    continue
                static = d.get("storageClass") == "static"
                if static:
                    self.static_ids.add(d.get("id"))
                v = A.VarDecl(name=d.get("name", ""), type=self._words(d.get("type")),
                              value=self._init(d), line=self._line(d))
                v.is_static = static
                if static and self.fn:
                    self.fn.static_state.add(v.name)
                out.append(v)
            return out
        if k == "ReturnStmt":
            if self.fn:
                self.fn.return_count += 1
            return [A.Return(value=self._expr(kids[0]) if kids else None, line=line)]
        if k == "IfStmt":
            has_else = n.get("hasElse", False)
            cond, then = kids[0], kids[1]
            other = self._block(kids[2]) if has_else and len(kids) > 2 else []
            return [A.If(cond=self._expr(cond), then_body=self._block(then),
                         else_body=other, line=line)]
        if k == "WhileStmt":
            if self.fn:
                self.fn.loops += 1
            cond = self._expr(kids[0])
            return [A.While(cond=cond, body=self._in("loop", kids[1]), line=line)]
        if k == "DoStmt":
            if self.fn:
                self.fn.loops += 1
            body = self._in("loop", kids[0])
            return [N.DoWhile(body=body, cond=self._expr(kids[1]), line=line)]
        if k == "SwitchStmt":
            return [self._switch(n)]
        if k == "ForStmt":
            if self.fn:
                self.fn.loops += 1
            return [self._for(n)]
        if k == "BreakStmt":
            brk = A.Break(line=line)
            brk.target = self._breakables[-1] if self._breakables else "loop"
            return [brk]
        if k == "ContinueStmt":
            return [N.Continue(line=line)]
        if k in ("GotoStmt", "LabelStmt", "CaseStmt", "DefaultStmt",
                 "IndirectGotoStmt"):
            name = re.sub(r"(?<!^)(?=[A-Z])", " ", k[:-4]).lower() + " statement"
            return [self._unsupported(n, name)]
        if k.endswith("Stmt") and k not in ("CompoundAssignOperator",):
            return [self._unsupported(n, re.sub(r"(?<!^)(?=[A-Z])", " ", k).lower())]
        return [self._expr_stmt(n)]

    def _in(self, kind, n):
        """Lift a body where `break` belongs to this loop or switch."""
        self._breakables.append(kind)
        try:
            return self._block(n)
        finally:
            self._breakables.pop()

    _LEAVES = (A.Return,)

    def _switch(self, n):
        """switch -> groups of steps, each reached by its case labels, in source
        order. A group that doesn't end in break/return continues into the next
        (C's fall-through), and that is shown, not hidden."""
        line = self._line(n)
        kids = _kids(n)
        value_n, body_n = kids[0], kids[-1]
        if len(kids) != 2 or body_n.get("kind") != "CompoundStmt":
            return self._unsupported(n, "switch statement")
        raw_groups, has_default = [], False
        for child in _kids(body_n):
            if child.get("kind") in ("CaseStmt", "DefaultStmt"):
                labels, x = [], child
                while x.get("kind") in ("CaseStmt", "DefaultStmt"):
                    parts = _kids(x)
                    if x["kind"] == "CaseStmt":
                        if len(parts) != 2:  # GNU case ranges
                            return self._unsupported(n, "switch statement")
                        labels.append(parts[0])
                        x = parts[1]
                    else:
                        labels.append(None)
                        has_default = True
                        x = parts[0] if parts else {}
                raw_groups.append([labels, [x] if x.get("kind") else []])
            elif raw_groups:
                raw_groups[-1][1].append(child)
            else:
                return self._unsupported(n, "switch statement")  # code before the first case
        # case labels hidden deeper inside a case (Duff's device) aren't described
        def has_label(x):
            return x.get("kind") in ("CaseStmt", "DefaultStmt") or any(has_label(c) for c in _kids(x))
        if any(has_label(st) for _, stmts in raw_groups for st in stmts):
            return self._unsupported(n, "switch statement")

        value = self._expr(value_n)
        groups = []
        self._breakables.append("switch")
        try:
            for labels, stmts in raw_groups:
                body = []
                for st in stmts:
                    body.extend(self._stmt(st))
                label_nodes = [None if l is None else self._expr(_unwrap(l)) for l in labels]
                ends = False
                if body and isinstance(body[-1], A.Break) and getattr(body[-1], "target", "") == "switch":
                    body.pop()
                    ends = True
                elif body and isinstance(body[-1], (A.Return, N.Continue, A.Break)):
                    ends = True
                elif body and isinstance(body[-1], A.FuncCall) and body[-1].name in END_FUNCS:
                    ends = True
                groups.append(N.CaseGroup(labels=label_nodes, body=body, falls_through=not ends))
        finally:
            self._breakables.pop()
        if groups:
            groups[-1].falls_through = False  # the last group simply ends the switch
        return N.Switch(value=value, groups=groups, has_default=has_default, line=line)

    def _expr_stmt(self, n):
        mac = self._macro(n)
        if mac is not None:
            return mac if isinstance(mac, N.Unsupported) else N.ExprStmt(expr=mac, line=self._line(n))
        e = _unwrap(n)
        k = e.get("kind")
        line = self._line(e)
        if k == "BinaryOperator" and e.get("opcode") == "=":
            lhs, rhs = _kids(e)
            value = self._expr(rhs)
            target = self._target(lhs)
            if isinstance(target, A.Identifier):
                return A.Assignment(target=target.name, value=value, line=line)
            return N.Store(target=target, value=value, line=line)
        if k == "CompoundAssignOperator":
            lhs, rhs = _kids(e)
            value = self._expr(rhs)
            target = self._target(lhs, also_reads=True)
            node = N.CompoundAssign(target=target, op=e.get("opcode", "?=")[:-1], value=value, line=line)
            node.int_div = node.op == "/" and self.types.is_integer(lhs.get("type"))
            return node
        if k == "UnaryOperator" and e.get("opcode") in ("++", "--"):
            target = self._target(_kids(e)[0], also_reads=True)
            return N.Step(target=target, delta=1 if e["opcode"] == "++" else -1, line=line)
        if k == "CallExpr":
            return self._call(e)
        if k == "CXXMemberCallExpr":
            return self._member_call(e)
        return N.ExprStmt(expr=self._expr(e), line=line)

    def _for(self, n):
        line = self._line(n)
        slots = _kids(n)
        init_n, _cond_var, cond_n, inc_n, body_n = (slots + [{}] * 5)[:5]
        init = []
        if init_n.get("kind"):
            init = self._stmt(init_n)
            self._uncount()
        cond = self._expr(cond_n) if cond_n.get("kind") else None
        step = [self._expr_stmt(inc_n)] if inc_n.get("kind") else []
        body = self._in("loop", body_n) if body_n.get("kind") else []
        simple = self._as_repeat(init, cond, step, body, line)
        if simple is not None:
            return simple
        return N.ForLoop(init=init, cond=cond, step=step, body=body, line=line)

    @staticmethod
    def _as_repeat(init, cond, step, body, line):
        """`for (int i = 0; i < N; i++)` with nothing in the body changing i or N,
        and N free of calls, is exactly Dictum's Repeat (emit_c emits this same loop)."""
        if len(init) != 1 or not isinstance(init[0], A.VarDecl) or len(step) != 1:
            return None
        v = init[0]
        if v.type != "whole number" or not (isinstance(v.value, A.Literal) and v.value.value == 0):
            return None
        if not (isinstance(cond, A.BinaryOp) and cond.op == "<"
                and isinstance(cond.left, A.Identifier) and cond.left.name == v.name):
            return None
        st = step[0]
        if not (isinstance(st, N.Step) and st.delta == 1
                and isinstance(st.target, A.Identifier) and st.target.name == v.name):
            return None
        bound_names = names_in(cond.right)
        if bound_names is None:
            return None
        written = written_names(body)
        if "\0unknown" in written or v.name in written or bound_names & written:
            return None
        if _any_node(body, lambda x: isinstance(x, A.FuncCall)) and \
                _any_node([cond.right], lambda x: getattr(x, "is_global", False)):
            return None
        if _any_node(body, lambda x: isinstance(x, A.UnaryOp) and x.op == "&"
                     and isinstance(x.operand, A.Identifier) and x.operand.name == v.name):
            return None
        return A.Repeat(count=cond.right, counter=v.name, body=body, line=line)

    def _init(self, d):
        if "init" not in d:
            return None
        exprs = [k for k in _kids(d) if k.get("kind")
                 and not k["kind"].endswith(("Decl", "Comment", "Attr", "Type"))]
        return self._expr(exprs[-1]) if exprs else None

    # ------------------------------------------------------------ what gets written

    def _decl_of(self, n):
        """The declaration a DeclRefExpr names, as {'id','kind','name'}."""
        return n.get("referencedDecl") if n.get("kind") == "DeclRefExpr" else None

    def _root(self, n):
        """Walk a write target to the variable it's rooted at.
        Returns (decl or None, through_pointer)."""
        through = False
        n = _unwrap(n)
        while n:
            k = n.get("kind")
            if k == "DeclRefExpr":
                return n.get("referencedDecl"), through
            if k == "ArraySubscriptExpr":
                base = _kids(n)[0]
                # Indexing a real array stays inside it; indexing a pointer
                # (including an array parameter, which C passes as a pointer)
                # reaches memory the pointer points at.
                if self.types.is_pointer(_unwrap(base).get("type")):
                    through = True
                n = _unwrap(base)
                continue
            if k == "MemberExpr":
                if n.get("isArrow"):
                    through = True
                n = _unwrap(_kids(n)[0])
                continue
            if k == "UnaryOperator" and not n.get("isPostfix"):
                if n.get("opcode") == "*":
                    through = True
                    n = _unwrap(_kids(n)[0])
                    continue
                if n.get("opcode") == "&":
                    n = _unwrap(_kids(n)[0])
                    continue
            return None, through
        return None, through

    def _origin(self, decl) -> str:
        if not decl:
            return "unknown"
        if decl.get("kind") == "ParmVarDecl":
            return "parameter"
        if decl.get("kind") == "VarDecl":
            if decl.get("id") in self.global_ids:
                return "global"
            if decl.get("id") in self.static_ids:
                return "static"
            return "local"
        return "unknown"

    def _record_write(self, target):
        if not self.fn:
            return
        decl, through = self._root(target)
        origin = self._origin(decl)
        name = decl.get("name", "(expression)") if decl else "(expression)"
        if through:
            self.fn.writes_through[name] = origin if origin != "unknown" else "local"
        elif origin == "global":
            self.fn.writes_globals.add(name)
        elif origin == "parameter":
            self.fn.reassigned_params.add(name)
        elif origin == "static":
            self.fn.static_state.add(name)

    def _target(self, n, also_reads=False):
        self._record_write(n)
        u = _unwrap(n)
        if u.get("kind") == "DeclRefExpr" and not also_reads:
            return A.Identifier(name=u.get("referencedDecl", {}).get("name", ""), line=self._line(u))
        return self._expr(n, lvalue=not also_reads)

    # ------------------------------------------------------------ calls

    def _call(self, n):
        kids = _kids(n)
        callee = _unwrap(kids[0]) if kids else {}
        decl = callee.get("referencedDecl") if callee.get("kind") == "DeclRefExpr" else None
        if not decl or decl.get("kind") != "FunctionDecl":
            return self._unsupported(n, "call through a function pointer")
        name = decl.get("name", "")
        args = kids[1:]
        if self.fn:
            self.fn.calls.add(name)
            self._call_effects(name, args)
        return A.FuncCall(name=name, args=[self._expr(a) for a in args], line=self._line(n))

    def _member_call(self, n):
        """obj.method(args), e.g. Serial.println(x): a call named 'Serial.println'."""
        kids = _kids(n)
        callee = _unwrap(kids[0]) if kids else {}
        if callee.get("kind") != "MemberExpr":
            return self._unsupported(n, "call through a function pointer")
        obj = _unwrap(_kids(callee)[0]) if _kids(callee) else {}
        owner = obj.get("referencedDecl", {}).get("name") if obj.get("kind") == "DeclRefExpr" else None
        name = f"{owner}.{callee.get('name', '')}" if owner else callee.get("name", "")
        args = kids[1:]
        if self.fn:
            self.fn.calls.add(name)
        return A.FuncCall(name=name, args=[self._expr(a) for a in args], line=self._line(n))

    def _call_effects(self, name, args):
        if name in PURE_LIB:
            return
        io_readonly = {"printf", "fprintf", "puts", "fputs", "putchar", "fputc", "perror"}
        for i, a in enumerate(args):
            decl, _ = self._root(a)
            origin = self._origin(decl)
            u = _unwrap(a)
            passes_address = u.get("kind") == "UnaryOperator" and u.get("opcode") == "&"
            is_ptr = self.types.is_pointer(u.get("type")) or passes_address or \
                self.types.is_array(u.get("type"))
            if not is_ptr or decl is None:
                continue
            writes_it = ((name in WRITES_FIRST_ARG and i == 0)
                         or (name in WRITES_LATER_ARGS and i >= 1))
            if writes_it:
                if passes_address and origin == "global":
                    self.fn.writes_globals.add(decl.get("name"))
                elif origin in ("parameter", "global", "local"):
                    self.fn.writes_through[decl.get("name")] = origin
                continue
            if name in (io_readonly | WRITES_FIRST_ARG | WRITES_LATER_ARGS
                        | ALLOC_FUNCS | FREE_FUNCS | END_FUNCS):
                continue
            if origin in ("parameter", "global"):
                self.fn.pointer_args.add((decl.get("name"), name))

    # ------------------------------------------------------------ macros

    def _macro(self, n):
        """If the whole expression was written as one macro in the user's file,
        return what to show for it: a named constant (NULL, MAX, true, EOF), or
        Unsupported for a function-like macro such as isspace(c). Clang marks
        both ends of such an expression with the same expansion point."""
        if self._quiet:
            return None  # walking a macro's expansion for facts: look at everything inside it
        r = n.get("range", {})
        b, e = r.get("begin", {}), r.get("end", {})
        if "expansionLoc" not in b or "expansionLoc" not in e:
            return None
        bx, ex = b["expansionLoc"], e["expansionLoc"]
        if bx.get("isMacroArgExpansion") or ex.get("isMacroArgExpansion"):
            return None
        if bx.get("file") != self.path or bx.get("offset") != ex.get("offset"):
            return None
        u = _unwrap(n)
        if u.get("kind") == "DeclRefExpr" and \
                u.get("referencedDecl", {}).get("kind") in ("VarDecl", "ParmVarDecl"):
            return None  # a macro that just names a variable (glibc: #define stderr stderr) IS that variable
        start = bx["offset"]
        name = self.source[start:start + bx.get("tokLen", 0)].decode("utf-8", "replace")
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            return None
        sb = b.get("spellingLoc", {})
        if sb.get("file") == self.path and self.macros.get(name, "") is None:
            span = self._call_parens(start, name)
            if span and span[0] < sb.get("offset", -1) < span[1]:
                return None  # written inside the macro's parentheses: an argument
        if name in self.macros and self.macros[name] is None:
            call = self._macro_call(n, name, start)
            if call is not None:
                return call
            return self._unsupported(n, "expression written with a macro")
        ident = A.Identifier(name=name, line=bx.get("line", 0))
        ident.named_constant = True
        value = self.macros.get(name)
        if value is not None and re.fullmatch(r"[0-9][0-9A-Fa-fxX.uUlL]*", value):
            ident.value_text = value
        return ident

    def _call_parens(self, start, name):
        """For a macro name written at byte `start`: the byte offsets of its
        '(' and matching ')', plus the argument spans; None if no parentheses."""
        text = self.source.decode("latin-1")  # one char per byte: indexes == clang's byte offsets
        i = start + len(name)
        while i < len(text) and text[i] in " \t":
            i += 1
        if i >= len(text) or text[i] != "(":
            return None
        open_at, depth, j, args, arg_start, in_str = i, 0, i, [], i + 1, None
        while j < len(text):
            ch = text[j]
            if in_str:
                if ch == "\\":
                    j += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch in "\"'":
                in_str = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    args.append((arg_start, j))
                    return open_at, j, args, text
            elif ch == "," and depth == 1:
                args.append((arg_start, j))
                arg_start = j + 1
            j += 1
        return None

    def _macro_call(self, n, name, start):
        """isspace(c) -> MacroCall('isspace', [c]). Only when every argument
        written in the source is found, whole and exact, in clang's expanded
        tree; otherwise None (the caller shows the bracket instead)."""
        span = self._call_parens(start, name)
        if span is None:
            return None
        open_at, close_at, args, text = span
        wanted = []
        for a, b in args:
            seg = text[a:b]
            lead = len(seg) - len(seg.lstrip())
            seg = seg.strip()
            if seg:
                wanted.append((a + lead, a + lead + len(seg), seg))
        if len(args) == 1 and not wanted:
            wanted = []  # MACRO() with no arguments
        found = {}

        def spell(loc):
            sl = loc.get("spellingLoc", loc)
            return sl if sl.get("file") == self.path else None

        def collect(x):
            r = x.get("range", {})
            b, e = spell(r.get("begin", {})), spell(r.get("end", {}))
            if b and e and open_at < b["offset"] and e["offset"] < close_at:
                key = (b["offset"], e["offset"] + e.get("tokLen", 0))
                found.setdefault(key, x)
                return
            for c in _kids(x):
                collect(c)
        collect(n)
        args_nodes = []
        for a, b, seg in wanted:
            x = found.get((a, b))
            if x is None:
                return None
            args_nodes.append(x)
        # Facts: library macros act as their documented function; any other
        # macro's facts come from its full expansion.
        if self.fn is not None and not self._quiet:
            if name in LIBRARY_MACROS:
                self.fn.calls.add(name)
            else:
                self._quiet = True
                try:
                    self._facts_walk(n)
                finally:
                    self._quiet = False
        line = _loc(n.get("range", {}).get("begin", {})).get("line", 0)
        return N.MacroCall(name=name, args=[self._expr(x) for x in args_nodes],
                           library=name in LIBRARY_MACROS, line=line)

    # ------------------------------------------------------------ expressions

    def _expr(self, n, lvalue=False):
        """lvalue=True: n is the storage side of a write, so the variable it
        names is being written, not read."""
        node = self._macro(n)
        if node is None:
            u = _unwrap(n)
            node = self._macro(u) or self._expr_inner(u, lvalue)
            n = u
        try:
            t = _unwrap(n).get("type") or n.get("type")
            node.ctype = self._words(t)
            node.is_pointer = self.types.is_pointer(t)
        except Exception:  # type info is a rendering aid only
            pass
        return node

    def _literal_text(self, n) -> str:
        return self._text(n) or str(n.get("value", "?"))

    def _expr_inner(self, n, lvalue=False):
        k = n.get("kind")
        line = self._line(n)
        kids = _kids(n)
        if k == "DeclRefExpr":
            decl = n.get("referencedDecl", {})
            ident = A.Identifier(name=decl.get("name", ""), line=line)
            if self._origin(decl) == "global":
                ident.is_global = True
                if self.fn and not lvalue:
                    self.fn.reads_globals.add(ident.name)
            return ident
        if k in ("IntegerLiteral", "FloatingLiteral"):
            text = self._literal_text(n)
            digits = text.rstrip("uUlL")
            value = int(digits) if digits.isdigit() and (digits == "0" or digits[0] != "0") else text
            lit = A.Literal(value=value, line=line)
            lit.source = text
            return lit
        if k == "CharacterLiteral":
            return N.CharLit(source=self._text(n) or "?", line=line)
        if k == "StringLiteral":
            src = self._text(n) or n.get("value", '""')
            return N.StrLit(source=src, value=decode_c_string(n.get("value", '""')), line=line)
        if k == "BinaryOperator":
            op = n.get("opcode")
            if op == "=":
                lhs, rhs = kids
                value = self._expr(rhs)
                target = self._target(lhs)
                return N.AssignExpr(target=target, value=value, line=line)
            if op == ",":
                return self._unsupported(n, "comma operator")
            lhs, rhs = kids
            if op == "/":
                count = self._item_count(lhs, rhs)
                if count is not None:
                    return count
            node = A.BinaryOp(op=op, left=self._expr(lhs), right=self._expr(rhs), line=line)
            node.int_div = op == "/" and self.types.is_integer(n.get("type"))
            return node
        if k == "CompoundAssignOperator":
            return self._unsupported(n, "assignment inside a larger expression")
        if k == "UnaryOperator":
            op = n.get("opcode")
            if op in ("++", "--"):
                return self._unsupported(n, "increment/decrement inside a larger expression")
            if n.get("isPostfix") or op not in ("-", "+", "!", "~", "&", "*"):
                return self._unsupported(n, f"operator {op}")
            # Taking an address (&x) hands out x's location; it doesn't read x's value.
            operand = self._expr(kids[0], lvalue=(op == "&"))
            return A.UnaryOp(op=op, operand=operand, line=line)
        if k == "CallExpr":
            return self._call(n)
        if k == "ArraySubscriptExpr":
            base, idx = kids
            ub = _unwrap(base)
            index = self._expr(idx)
            # Writing a[i] of an ARRAY doesn't read a; of a POINTER it reads the pointer.
            base_lv = lvalue and not self.types.is_pointer(ub.get("type"))
            if ub.get("kind") == "DeclRefExpr" and self._macro(ub) is None:
                self._expr(ub, lvalue=base_lv)
                return A.IndexAccess(collection=ub["referencedDecl"].get("name", ""), index=index, line=line)
            return N.Index(base=self._expr(base, lvalue=base_lv), index=index, line=line)
        if k == "MemberExpr":
            base = kids[0]
            arrow = bool(n.get("isArrow"))
            ub = _unwrap(base)
            base_lv = lvalue and not arrow
            if ub.get("kind") == "DeclRefExpr" and not arrow and self._macro(ub) is None:
                self._expr(ub, lvalue=base_lv)
                return A.FieldAccess(obj=ub["referencedDecl"].get("name", ""), field=n.get("name", ""), line=line)
            return N.Member(base=self._expr(base, lvalue=base_lv), field=n.get("name", ""),
                            arrow=arrow, line=line)
        if k == "CXXBoolLiteralExpr":
            lit = A.Literal(value=1 if n.get("value") else 0, line=line)
            lit.source = "true" if n.get("value") else "false"
            ident = A.Identifier(name=lit.source, line=line)
            ident.named_constant = True
            return ident
        if k == "CXXNullPtrLiteralExpr":
            ident = A.Identifier(name="NULL", line=line)
            ident.named_constant = True
            return ident
        if k == "CXXMemberCallExpr":
            return self._member_call(n)
        if k in ("CXXStaticCastExpr", "CXXFunctionalCastExpr"):
            return N.Cast(type_words=self._words(n.get("type")), expr=self._expr(kids[-1]), line=line)
        if k == "CStyleCastExpr":
            return N.Cast(type_words=self._words(n.get("type")), expr=self._expr(kids[-1]), line=line)
        if k == "ConditionalOperator":
            a, b, c = kids
            return N.Ternary(cond=self._expr(a), then=self._expr(b), other=self._expr(c), line=line)
        if k == "UnaryExprOrTypeTraitExpr":
            if n.get("name") != "sizeof":
                return self._unsupported(n, n.get("name", "operator"))
            if "argType" in n:
                return N.SizeOf(what="one " + n["argType"].get("qualType", "?"), line=line)
            # C never evaluates a sizeof operand, so it reads nothing.
            saved, self.fn = self.fn, None
            try:
                inner = self._expr(kids[0])
            finally:
                self.fn = saved
            return N.SizeOf(expr=inner, line=line)
        if k == "InitListExpr":
            # With unlisted items, clang moves the listed ones to "array_filler"
            # (after the filler itself) and C sets every other item to zero.
            filler = n.get("array_filler")
            listed = filler[1:] if filler else kids
            lit = N.ListLit(items=[self._expr(x) for x in listed], rest_zero=bool(filler), line=line)
            t = n.get("type", {})
            fields = self.record_fields.get(t.get("qualType")) or self.record_fields.get(t.get("desugaredQualType"))
            if fields and len(fields) == len(lit.items) and not filler:
                lit.fields = fields
            return lit
        if k == "ImplicitValueInitExpr":
            zero = A.Literal(value=0, line=line)
            zero.source = "zero"
            return zero
        words = re.sub(r"(?<!^)(?=[A-Z])", " ", k or "expression").lower()
        return self._unsupported(n, words)

    def _item_count(self, lhs, rhs):
        """sizeof(a) / sizeof(a[0]) where a is a real (non-parameter) array
        with a known size: exactly the number of items in a."""
        def operand(x):
            x = _unwrap(x)
            if x.get("kind") != "UnaryExprOrTypeTraitExpr" or x.get("name") != "sizeof" \
                    or "argType" in x or len(_kids(x)) != 1:
                return None
            return _unwrap(_kids(x)[0])
        a, b = operand(lhs), operand(rhs)
        if not a or not b or a.get("kind") != "DeclRefExpr":
            return None
        decl = a.get("referencedDecl", {})
        if decl.get("kind") == "ParmVarDecl":
            return None
        count = self.types.array_count(a.get("type"))
        if count is None or b.get("kind") != "ArraySubscriptExpr":
            return None
        base, idx = [_unwrap(x) for x in _kids(b)]
        if base.get("kind") == "DeclRefExpr" and base.get("referencedDecl", {}).get("id") == decl.get("id") \
                and idx.get("kind") == "IntegerLiteral" and idx.get("value") == "0":
            return N.ItemCount(name=decl.get("name", ""), count=count, line=self._line(a))
        return None
