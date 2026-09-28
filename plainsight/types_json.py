"""C type strings (as clang's JSON AST spells them) -> plain words.

Clang gives types as strings ("const int *", "double[5]", "struct Pos *").
This parses the common shapes exactly; anything else is shown as written,
never guessed.
"""
import re

from .dictum_bridge import TR
# Dictum's own plain-English names, where it has one for the same C type.
_DICTUM_WORDS = {p.c_type: p.name for p in TR.PRIMITIVES
                 if " " in p.name or p.name in ("count", "byte")}

_NAMED = {
    "size_t": "count",
    "bool": "truth value",
    "_Bool": "truth value",
    "int8_t": "small whole number (8-bit)",
    "int16_t": "whole number (16-bit)",
    "int32_t": "whole number",
    "int64_t": "large whole number (64-bit)",
    "uint8_t": "byte",
    "uint16_t": "non-negative whole number (16-bit)",
    "uint32_t": "non-negative whole number (32-bit)",
    "uint64_t": "non-negative whole number (64-bit)",
    "FILE": "file",
    "bool": "truth value",
}
for _c, _w in _DICTUM_WORDS.items():
    _NAMED.setdefault(_c, _w)


def plural(words: str) -> str:
    """Pluralise the head noun of a type phrase ('whole number' -> 'whole numbers')."""
    if words.endswith(")"):
        head, sep, tail = words.partition(" (")
        return plural(head) + sep + tail
    for noun, pl in (("number", "numbers"), ("character", "characters"),
                     ("value", "values"), ("byte", "bytes"), ("record", "records"),
                     ("text", "texts"), ("count", "counts"), ("choice", "choices")):
        if words.endswith(noun):
            return words[: -len(noun)] + pl
    return words + " items"


_BUILTIN = {
    "void": "nothing",
    "_Bool": "truth value",
    "char": "character", "signed char": "character", "unsigned char": "byte",
    "short": "short whole number", "short int": "short whole number",
    "signed short": "short whole number", "signed short int": "short whole number",
    "unsigned short": "non-negative short whole number",
    "unsigned short int": "non-negative short whole number",
    "int": "whole number", "signed": "whole number", "signed int": "whole number",
    "unsigned": "non-negative whole number", "unsigned int": "non-negative whole number",
    "long": "long whole number", "long int": "long whole number",
    "signed long": "long whole number", "signed long int": "long whole number",
    "unsigned long": "non-negative long whole number",
    "unsigned long int": "non-negative long whole number",
    "long long": "long whole number", "long long int": "long whole number",
    "signed long long": "long whole number",
    "unsigned long long": "non-negative long whole number",
    "unsigned long long int": "non-negative long whole number",
    "float": "fractional number (single precision)",
    "double": "fractional number",
    "long double": "fractional number (extended precision)",
}

_INTEGER_BASES = {k for k, v in _BUILTIN.items() if "whole number" in v or k in
                  ("_Bool", "char", "signed char", "unsigned char")} | {"bool", "size_t"}

_ARRAY = re.compile(r"^(.*?)\s*\[(\d*)\]$")
_PTR = re.compile(r"^(.*)\*\s*((?:const|volatile|restrict|\s)*)$")


class TypeWords:
    """typedefs: name -> underlying type string, collected from the whole file
    (including headers), so a typedef'd struct reads as '<Name> record'."""

    def __init__(self, typedefs=None):
        self.typedefs = typedefs or {}

    # ---- queries used by the lifter

    def resolve(self, t: dict) -> str:
        """The most desugared spelling clang gave for a type object."""
        return (t or {}).get("desugaredQualType") or (t or {}).get("qualType", "")

    def is_pointer(self, t: dict) -> bool:
        s = self._through_typedefs(self.resolve(t))
        return bool(_PTR.match(s)) or "(*)" in s

    def is_array(self, t: dict) -> bool:
        return bool(_ARRAY.match(self._through_typedefs(self.resolve(t))))

    def array_count(self, t: dict):
        m = _ARRAY.match(self._through_typedefs(self.resolve(t)))
        return int(m.group(2)) if m and m.group(2) else None

    def is_integer(self, t: dict) -> bool:
        s = self._through_typedefs(self.resolve(t))
        base = self._strip_quals(s)
        return base in _INTEGER_BASES or base.startswith("enum ")

    def pointee(self, qt: str) -> str:
        m = _PTR.match(qt.strip())
        return m.group(1).strip() if m else qt

    # ---- words

    def words(self, t) -> str:
        qt = t if isinstance(t, str) else (t or {}).get("qualType", "")
        return self._plain(qt.strip())

    def _plain(self, s: str) -> str:
        if "(*)" in s:
            return "pointer to a function"
        m = _ARRAY.match(s)
        if m:
            elem, n = m.group(1), m.group(2)
            return f"list of {n + ' ' if n else ''}{plural(self._plain(elem))}"
        m = _PTR.match(s)
        if m:
            inner, quals = m.group(1).strip(), m.group(2)
            prefix = "read-only " if "const" in quals else ""
            base = self._strip_quals(inner)
            if self._through_typedefs(base) in ("char", "signed char"):
                return prefix + ("text" if self._is_const(inner) else "changeable text")
            if self._through_typedefs(base) == "void":
                return prefix + "pointer to anything"
            words = self._plain(inner)
            if words.startswith("function ("):
                return prefix + "pointer to a " + words
            return prefix + "pointer to " + words
        words = self._base(self._strip_quals(s))
        return ("read-only " + words) if self._is_const(s) else words

    def _base(self, name: str) -> str:
        if name in _BUILTIN:
            return _BUILTIN[name]
        for tag, suffix in (("struct ", " record"), ("union ", " record"), ("enum ", " choice")):
            if name.startswith(tag):
                inner = name[len(tag):]
                if inner.startswith("(unnamed") or inner.startswith("(anonymous"):
                    return "(unnamed)" + suffix
                return inner + suffix
        if name in _NAMED:
            return _NAMED[name]
        if name in self.typedefs:
            under = self._strip_quals(self.typedefs[name])
            if "(" in under and "(*)" not in under:
                return f"function ({name})"
            if under.startswith(("struct ", "union ")):
                return name + " record"
            if under.startswith("enum "):
                return name + " choice"
        return name if re.fullmatch(r"[A-Za-z_]\w*", name) else f"C type '{name}'"

    def _through_typedefs(self, s: str) -> str:
        """Follow a chain of typedef names to what they stand for
        ('Buf' -> 'char *'); anything that isn't a typedef name is returned as is."""
        base = self._strip_quals(s)
        for _ in range(10):
            if base in self.typedefs and base not in _NAMED:
                base = self._strip_quals(self.typedefs[base])
            else:
                break
        return base

    @staticmethod
    def _is_const(s: str) -> bool:
        return bool(re.match(r"^(?:volatile\s+)?const\b", s.strip())) or s.strip().endswith(" const")

    @staticmethod
    def _strip_quals(s: str) -> str:
        s = re.sub(r"\b(const|volatile|restrict)\b", "", s)
        return re.sub(r"\s+", " ", s).strip()
