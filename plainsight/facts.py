"""Per-function facts, collected while lifting, and the side-effect rule.

Facts are exact statements about the source, not guesses:
- "yes"     = the code definitely does this on some path
- "may"     = it might, depending on where a pointer points or what a
              called function does (resolved further in M3's call graph)
Nothing uncertain is ever reported as "yes" or as "none".
"""
from dataclasses import dataclass, field
from typing import Dict, List, Set

IO_FUNCS = {"pinMode", "digitalWrite", "digitalRead", "analogRead", "analogWrite", "delay", "millis", "micros",
            "Serial.begin", "Serial.print", "Serial.println",
            "printf", "fprintf", "puts", "putchar", "fputs", "fputc", "scanf",
            "fscanf", "getchar", "fgets", "fgetc", "fopen", "fclose", "fread",
            "fwrite", "perror", "fflush", "remove", "rename"}
ALLOC_FUNCS = {"malloc", "calloc", "realloc", "strdup"}
FREE_FUNCS = {"free"}
END_FUNCS = {"exit", "abort", "_Exit"}
# Library functions that only read their arguments and change nothing.
PURE_LIB = {"abs", "labs", "llabs", "fabs", "sqrt", "pow", "sin", "cos", "tan",
            "floor", "ceil", "round", "exp", "log", "log10", "fmin", "fmax",
            "strlen", "strcmp", "strncmp", "strchr", "strstr", "memcmp",
            "isdigit", "isalpha", "isalnum", "isspace", "isupper", "islower",
            "toupper", "tolower", "atoi", "atof",
            # glibc implements isspace() etc. as macros around these read-only lookups
            "__ctype_b_loc", "__ctype_tolower_loc", "__ctype_toupper_loc",
            "__builtin_nanf", "__builtin_inff", "__builtin_huge_valf", "__builtin_isnan",
            "__builtin_isinf_sign", "__builtin_fpclassify"}
# Standard function-like macros that behave as the documented function.
LIBRARY_MACROS = {"isalnum", "isalpha", "iscntrl", "isdigit", "isgraph", "islower", "isprint",
                  "ispunct", "isspace", "isupper", "isxdigit", "tolower", "toupper",
                  "getc", "putc", "getchar", "putchar"}
PURE_LIB |= {"iscntrl", "isgraph", "isprint", "ispunct", "isxdigit"}

# Library functions that write into the memory their FIRST argument points to.
WRITES_FIRST_ARG = {"memcpy", "memmove", "memset", "strcpy", "strncpy", "strcat",
                    "strncat", "sprintf", "snprintf"}
# Library functions that write into what every argument after the first points to.
WRITES_LATER_ARGS = {"scanf", "sscanf", "fscanf"}


@dataclass
class FnFacts:
    name: str
    line: int
    params: List[Dict[str, str]] = field(default_factory=list)
    returns: str = "nothing"
    calls: Set[str] = field(default_factory=set)
    reads_globals: Set[str] = field(default_factory=set)
    writes_globals: Set[str] = field(default_factory=set)
    pointer_params: Set[str] = field(default_factory=set)
    reassigned_params: Set[str] = field(default_factory=set)
    # pointer name -> origin ("parameter" | "global" | "local")
    writes_through: Dict[str, str] = field(default_factory=dict)
    # (pointer, function) pairs: a pointer parameter/global handed to a call
    pointer_args: Set[tuple] = field(default_factory=set)
    static_state: Set[str] = field(default_factory=set)
    calls_through_pointer: bool = False
    opaque: bool = False  # inline assembly etc.: can do anything
    loops: int = 0
    return_count: int = 0
    statements: int = 0
    unsupported: List[Dict[str, object]] = field(default_factory=list)

    def finalize(self, local_functions: Set[str]) -> dict:
        through = []
        for ptr, origin in sorted(self.writes_through.items()):
            if origin == "parameter":
                certainty = "may" if ptr in self.reassigned_params else "yes"
            elif origin == "global":
                certainty = "yes"
            else:
                certainty = "may"
            through.append({"pointer": ptr, "origin": origin, "certainty": certainty})

        calls_local = sorted(c for c in self.calls if c in local_functions)
        calls_external = sorted(c for c in self.calls if c not in local_functions)

        definite, maybe = [], []
        for g in sorted(self.writes_globals):
            definite.append(f"changes shared variable {g}")
        for t in through:
            if t["certainty"] == "yes":
                if t["origin"] == "parameter":
                    definite.append(f"changes data the caller passed in through {t['pointer']}")
                else:
                    definite.append(f"changes data that shared pointer {t['pointer']} points to")
            else:
                maybe.append(f"may change data through pointer {t['pointer']}")
        for s in sorted(self.static_state):
            definite.append(f"keeps {s} between calls")
        if self.calls & IO_FUNCS:
            definite.append("does input/output (" + ", ".join(sorted(self.calls & IO_FUNCS)) + ")")
        if self.calls & ALLOC_FUNCS:
            definite.append("allocates memory")
        if self.calls & FREE_FUNCS:
            definite.append("frees memory")
        if self.calls & END_FUNCS:
            definite.append("can end the whole program")
        for ptr, fn in sorted(self.pointer_args):
            maybe.append(f"passes pointer {ptr} to {fn}, which may change what it points to")
        known = IO_FUNCS | ALLOC_FUNCS | FREE_FUNCS | END_FUNCS | PURE_LIB | WRITES_FIRST_ARG | WRITES_LATER_ARGS
        unknown_calls = [c for c in calls_external if c not in known]
        if self.opaque:
            maybe.append("contains inline assembly, which can do anything")
        if self.calls_through_pointer:
            maybe.append("calls a function through a pointer, which could be any function")
        for c in calls_local:
            maybe.append(f"calls {c}, whose effects are checked in the call graph (M3)")
        for c in unknown_calls:
            maybe.append(f"calls {c}, a function not defined in this file")

        if definite:
            side = "yes"
        elif maybe:
            # Gaps in the English don't make this unknown: facts are collected
            # from clang's full tree even where the English can't describe it yet.
            side = "unknown"
        else:
            side = "none"

        return {
            "name": self.name,
            "line": self.line,
            "params": self.params,
            "returns": self.returns,
            "calls_local": calls_local,
            "calls_external": calls_external,
            "reads_globals": sorted(self.reads_globals),
            "writes_globals": sorted(self.writes_globals),
            "writes_through_pointers": through,
            "keeps_state_between_calls": sorted(self.static_state),
            "loops": self.loops,
            "return_statements": self.return_count,
            "side_effects": side,
            "effects": definite,
            "possible_effects": maybe,
            "not_yet_supported": self.unsupported,
        }
