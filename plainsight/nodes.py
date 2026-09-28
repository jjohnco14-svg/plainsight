"""Node types C needs that Dictum's AST doesn't have.

Everything subclasses Dictum's Node so the tree stays one type family.
Where Dictum already has an exact equivalent, lift.py uses Dictum's node.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from .dictum_bridge import A

Node = A.Node


@dataclass
class Store(Node):
    """Assignment to something that isn't a plain variable: a[i] = v, *p = v, p->x = v."""
    target: Node = None
    value: Node = None


@dataclass
class CompoundAssign(Node):
    """x += v, x -= v, ... kept as one node so English can say 'adds v to x'."""
    target: Node = None
    op: str = ""          # the arithmetic op without '=', e.g. '+'
    value: Node = None
    int_div: bool = False  # for '/=' on integers


@dataclass
class Step(Node):
    """i++ / i-- / ++i / --i used as a whole statement."""
    target: Node = None
    delta: int = 1


@dataclass
class ForLoop(Node):
    """A C for-loop that isn't the simple `for (int i = 0; i < N; i++)` shape."""
    init: List[Node] = field(default_factory=list)
    cond: Optional[Node] = None
    step: List[Node] = field(default_factory=list)
    body: List[Node] = field(default_factory=list)


@dataclass
class DoWhile(Node):
    body: List[Node] = field(default_factory=list)
    cond: Node = None


@dataclass
class Continue(Node):
    pass


@dataclass
class Index(Node):
    """base[index] where base isn't a plain name (Dictum's IndexAccess needs a name)."""
    base: Node = None
    index: Node = None


@dataclass
class Member(Node):
    """base.field or base->field."""
    base: Node = None
    field: str = ""
    arrow: bool = False


@dataclass
class Cast(Node):
    type_words: str = ""
    expr: Node = None


@dataclass
class Ternary(Node):
    cond: Node = None
    then: Node = None
    other: Node = None


@dataclass
class SizeOf(Node):
    what: str = ""        # for sizeof(TYPE): "one int"
    expr: Node = None     # for sizeof EXPR: the expression (never evaluated by C)


@dataclass
class ItemCount(Node):
    """sizeof(a) / sizeof(a[0]) on a real array: the number of items in a."""
    name: str = ""
    count: int = 0


@dataclass
class ListLit(Node):
    items: List[Node] = field(default_factory=list)
    rest_zero: bool = False  # C sets every item not listed to zero


@dataclass
class CharLit(Node):
    source: str = ""  # as written, including quotes: 'a', '\n'


@dataclass
class StrLit(Node):
    source: str = ""  # as written, including quotes
    value: str = ""   # decoded


@dataclass
class ExprStmt(Node):
    """An expression used as a statement that isn't a call/assignment/step."""
    expr: Node = None


@dataclass
class Unsupported(Node):
    """Something Plainsight can't describe exactly yet. Never guessed at."""
    what: str = ""
    source: str = ""


@dataclass
class CaseGroup(Node):
    """One run of steps in a switch, reached by any of its labels.
    A label of None means `default` (any other value)."""
    labels: List[Node] = field(default_factory=list)
    body: List[Node] = field(default_factory=list)
    falls_through: bool = False  # no break/return: C continues into the next group


@dataclass
class Switch(Node):
    value: Node = None
    groups: List[CaseGroup] = field(default_factory=list)
    has_default: bool = False


@dataclass
class EnumDecl(Node):
    name: str = ""
    options: List[tuple] = field(default_factory=list)  # (name, value)


@dataclass
class AssignExpr(Node):
    """x = v used inside a larger expression; its value is the new x."""
    target: Node = None
    value: Node = None


@dataclass
class MacroCall(Node):
    """A function-like macro, shown as written: name and arguments.
    `library` is True for standard ones (isspace...) that act like functions."""
    name: str = ""
    args: List[Node] = field(default_factory=list)
    library: bool = False
