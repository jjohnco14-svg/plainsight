"""Which language a file is, and how to read and compile it.

- .c                       C11
- .cpp .cc .cxx .hpp       C++17
- .ino (Arduino sketch)    C++17 plus what the Arduino IDE adds before compiling: Arduino.h
                           (here a stand-in, docs/plainsight_arduino.h -- no real hardware) and a
                           declaration of every function, so they can be used before they're
                           defined. The declarations are inserted ON the line of the first function
                           (a copy of the sketch is read), so every line number stays the same.
"""
import hashlib
import os
import re
import shutil
import tempfile

DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
ARDUINO_H = os.path.join(DOCS, "plainsight_arduino.h")
CPP_EXT = (".cpp", ".cc", ".cxx", ".hpp", ".hh")


def language(path: str) -> str:
    p = path.lower()
    if p.endswith(".ino"):
        return "arduino"
    if p.endswith(CPP_EXT):
        return "c++"
    return "c"


def read_flags(path: str):
    lang = language(path)
    if lang == "c":
        return ["-std=c11", "-x", "c"]
    flags = ["-std=c++17", "-x", "c++"]
    if lang == "arduino":
        flags += ["-include", ARDUINO_H]
    return flags


def compiler(path: str):
    """The command that compiles a test program which #includes this source."""
    if language(path) == "c":
        return [shutil.which("gcc") or shutil.which("clang") or "gcc", "-std=c11"]
    cxx = shutil.which("g++") or shutil.which("clang++") or "g++"
    cmd = [cxx, "-std=c++17", "-x", "c++"]
    if language(path) == "arduino":
        cmd += ["-include", ARDUINO_H]
    return cmd


_FUNC = re.compile(r"^(?!\s)((?:[A-Za-z_][\w:<>]*[\s\*&]+)+?)([A-Za-z_]\w*)\s*\(([^;{}()]*)\)\s*(?:const\s*)?\{",
                   re.M)
_NOT_A_TYPE = {"if", "for", "while", "switch", "return", "else", "do"}


def prepare(path: str) -> str:
    """The path to actually read: the file itself, or for a sketch a copy with
    every function declared up front (on the first function's line)."""
    if language(path) != "arduino":
        return path
    text = open(path, "rb").read().decode("latin-1")
    protos, first = [], None
    for m in _FUNC.finditer(text):
        ret, name, params = m.group(1).strip(), m.group(2), " ".join(m.group(3).split())
        if ret.split()[-1] in _NOT_A_TYPE or name in _NOT_A_TYPE:
            continue
        if first is None:
            first = m.start()
        protos.append(f"{ret} {name}({params});")
    body = text if first is None else text[:first] + " ".join(protos) + " " + text[first:]
    digest = hashlib.sha256(body.encode("latin-1")).hexdigest()[:12]
    folder = os.path.join(tempfile.gettempdir(), "plainsight_sketches", digest)
    os.makedirs(folder, exist_ok=True)
    copy = os.path.join(folder, os.path.basename(path))
    if not os.path.isfile(copy):
        with open(copy, "wb") as fh:
            fh.write(body.encode("latin-1"))
    return copy
