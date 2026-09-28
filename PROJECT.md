# Plainsight

## Description

Plainsight reads a C file and describes, in plain English, exactly what
each function does, step by step. It also lists hard facts about each
function: what it reads, what it changes, what it calls, and whether it
has side effects. It is for people who need to trust C code without
reading C (engineers checking Arduino/embedded code, clients checking a
freelancer's work) and for AI tools that need cheap, exact facts about
code instead of whole files.

- **Who runs it:** a person at a terminal, or another program (JSON output).
- **Platform:** Linux/macOS with Python 3.10+, the `clang` program
  (`sudo apt install clang`; version 18 tested), and this repo's Dictum
  compiler (for its syntax-tree node types). No pip dependencies.
- **What it must never do:**
  - Guess. Anything it can't describe exactly is shown as
    `[Not yet supported: ...]` with the line number, never skipped silently
    and never paraphrased loosely.
  - Describe code that doesn't compile. If the C has errors, it refuses and
    shows the first error.
  - Use an AI model in the core path. The English comes from rules only.
  - Modify the input file.

"Finished" (for the 5-week plan) means: English + facts + call graph +
checked AI summaries, verified by round-tripping through Dictum. This
file covers Milestone 1 in detail; later milestones are outlined.

## Files

- `plainsight/dictum_bridge.py` -- finds the Dictum compiler in this repo and imports its node types
- `plainsight/nodes.py`        -- extra node types C needs that Dictum lacks (pointer writes, for-loops, casts, "not yet supported")
- `plainsight/types_json.py`   -- C type -> plain words ("int" -> "whole number"), from clang's type spellings
- `plainsight/lift_json.py`    -- reads clang's full JSON syntax tree, builds Dictum-style nodes, records facts while walking
- `plainsight/lift.py`         -- entry point (`parse`) and helpers shared with the renderer
- `tools/corpus_census.py`     -- runs Plainsight over real open-source C; reports crashes and the most common gaps
- `plainsight/plan.py`         -- reads an approved plan (mode diagram) and runs it as a simulator
- `plainsight/harness.py`      -- finds the machine by types; compiles and runs the REAL code on many inputs
- `plainsight/check.py`        -- compares plan and code by behaviour; plain-words report
- `plainsight/maketests.py`    -- writes a standalone C test file from an approved plan
- `plainsight/planfile.py`     -- plan validation against code, the plan picture, approval stamps
- `plainsight/docs/`           -- PLAN_GUIDE.md (plan language), AGENTS.md (agent workflow); bundled
- `plainsight/_dictum/`        -- bundled copies of Dictum's ast_nodes.py and type_registry.py
- `plainsight/plancheck.py`    -- plan warnings: unreachable modes, arrows never taken, no way out
- `plainsight/examples.py`     -- example checks for any function (lists, text, printing, records)
- `plainsight/facts.py`        -- per-function fact collector and the side-effect rule
- `plainsight/render.py`       -- nodes -> English steps (exact grouping, no ambiguity)
- `plainsight/cli.py`          -- command-line entry point
- `tests/golden/*.c`           -- small C examples, one construct each
- `tests/golden/*.txt`         -- the reviewed, expected English for each example
- `tests/expected_facts.py`    -- hand-written facts each example must produce (written independently of the tool)
- `tests/test_plainsight.py`   -- runs every check below

All Python: this is text processing where a wrong answer "looks wrong"
rather than causing harm. The C itself is read by clang, which does the hard part.

## Milestones

### M1 -- C to plain English + per-function facts
Delivers: `python3 -m plainsight FILE.c` prints each function as numbered
English steps, plus facts with `--facts`/`--json`.

Checks:
- [x] Every golden `.c` file compiles: `gcc -std=c11 -Wall -Wextra -Werror -fsyntax-only` -> no output, for all 41 files
- [x] `python3 -m pytest tests/ -q` -> 134 passed, 0 failed
- [x] Golden English matches exactly for every example (41, each reviewed line by line before freezing)
- [x] Hand-written facts match for every example in `expected_facts.py` (written before running the tool; caught 1 real bug, see below)
- [x] Unsupported construct (`switch`) -> shows `[Not yet supported: switch statement (line 3)]`, and `--strict` exits 3
- [x] Broken C -> `error: bad.c does not compile: ...`, exits 2, prints no English
- [x] Missing file -> `error: no_such_file.c does not exist`, exits 1
- [x] Ambiguity check: `a + b * c` renders as `a plus (b times c)`
- [x] Integer division is "the whole-number part of a divided by b"; `%` is "the remainder of a divided by b"
- [x] Tests proven able to fail: removing grouping -> 13 failures; ignoring array-parameter writes -> 1 failure; calling every counting loop "Repeats N times" -> 2 failures. All restored -> all pass.
Last verified: 2026-09-27, in Claude's container (not yet committed -- record the commit hash here after you push)

Bugs found while verifying M1 (each now has a permanent test):
- Writes into an array parameter (`int a[]`) weren't counted as changing the caller's data. libclang reports the type as written (`int[]`), not as the pointer C actually passes. Caught by the hand-written facts for `21_array_param_write`.
- A shared ARRAY written by index was reported as "data a shared pointer points to". libclang wraps arrays in a hidden pointer conversion; the check now looks at the variable itself. Caught by the thermostat file.
- Initializers written as a macro (`bool on = false;`) weren't found, because libclang returns no tokens for a declaration ending in a macro. Now read from the file text. Caught by the thermostat file.
- `NULL` rendered as `?` for the same token reason. Now read from the file text.

Why the English renderer is new instead of reusing Dictum's `nl_mirror.py`:
nl_mirror writes each function as one paragraph and prints `a + b * c` as
"a plus b times c", which is ambiguous. Plainsight reuses Dictum's syntax-tree
node types (`ast_nodes.py`) and type vocabulary (`type_registry.py`) so that M2
can hand the same tree to `emit_c.py`; the grouping rules here could be ported
back into nl_mirror later.

### M1 trial -- ordinary C programs (2026-09-27)
Three programs written as normal C, not tailored to Plainsight (a ring
buffer, a button debouncer, a word counter). Each was compiled with
`-Wall -Wextra -Werror -fsanitize=address,undefined`, run, and its output
checked against intent written beforehand (debouncer traced by hand: 2 presses).
Now permanent tests `42_ringbuf`, `43_debounce`, `44_wordstats`.

- [x] Ring buffer: 29 of 29 steps described; facts correct
- [x] Fixed 3 exactness bugs found: an index that is itself a phrase is now grouped
      ("item (the head of what rb points to) of ..."); `sizeof(a[0])` no longer prints raw
      tokens and `sizeof(a)/sizeof(a[0])` reads "the number of items in a (16)";
      `printf("\n")` reads "Starts a new line."
- [x] `python3 -m pytest tests/ -q` -> 143 passed; removing the index-grouping fix -> 1 failure

Most common gaps in real code, in priority order (next work before M2):
1. `switch` / `case` (debouncer)
2. `enum` declarations (debouncer)
3. assignment inside a condition: `while ((c = getchar()) != EOF)` (word counter)
4. library functions that glibc defines as macros: `isspace(c)`, `isdigit(c)` (word counter)

C++ is not supported yet: files are read as C, so C++ code is refused as "does not compile".
(Clang's JSON covers C++ too, so this frontend is the right base for adding it later.)

### M1.5 -- read C through clang's full JSON syntax tree (2026-09-27)
Replaces the libclang frontend, which read token lists and guessed. Clang's
JSON states each operator, every hidden conversion, each for-loop slot, and
where every macro was used, so nothing is guessed.

Checks:
- [x] Parity: old and new frontends gave identical English and facts on all 44 golden files and the 4 trial programs (48/48)
- [x] Real-world code (cJSON, linenoise, sds, jsmn, tiny-AES, sha256): 271 functions, 0 crashes, 90.8% of statements described (`python3 tools/corpus_census.py`)
- [x] Every real-world difference between the frontends was checked against the source: the old frontend
      crashed on sds.c, claimed calls to functions that don't exist (calls through function pointers in
      cJSON), described glibc internals instead of `NAN`, and attached a macro's inner `!` to the outer
      macro's name (cJSON line 1097, which read with the opposite meaning)
- [x] Bugs the real-world code found in the NEW frontend, all fixed and each guarded by a test that fails
      when the fix is removed: `{0}` initializers lost their items; a doc comment was read as a starting
      value (sha256.c); `stderr` (a macro that names a variable) stopped counting as a read; facts went
      blind inside unsupported parts
- [x] Facts are now complete even where the English has a gap: one general walk covers macros, `switch`,
      assignments/increments inside expressions, and calls through function pointers. "Side effects:
      unknown" is kept for what truly can't be analysed (calls through pointers, inline assembly)
- [x] Improvements: struct initializers name fields ("with a set to 5 and b set to zero"); named constants
      show decimal values, following macro chains (`INT_MAX (2147483647)`, `BRKINT (2)`); function-pointer
      types read "pointer to a function (Callback)"; `-D` option for compiler defines
- [x] libclang uninstalled -> `python3 -m pytest tests/ -q` -> 149 passed; old frontend code deleted
Last verified: 2026-09-27, in Claude's container (not yet committed)

Hand-written expectation changed, with reason: `31_switch_unsupported` side effects "unknown" -> "none".
The switch only changes the local `r`; "unknown" reflected the old tool's blindness inside gaps, not the code.

Most common gaps in real code (from the census; replaces the earlier hand-made list):
1. function-like macros used as expressions (101) -- e.g. `isspace(c)`, cJSON's `can_read(...)`
2. `goto` + labels (53 + 15) -- cJSON's error handling
3. `++`/`--` inside larger expressions (30)
4. calls through function pointers (27)
5. `switch` (21)
6. assignment inside a larger expression (14) -- `while ((c = getchar()) != EOF)`
7. comma operator (7)
Not in the corpus but known: `enum` declarations.

### M1.6 -- the most common constructs (2026-09-27)
- [x] `switch`: "Checks the value of X" with one labelled block per case; fall-through is stated
      ("continues straight into the next case's steps"); `break` in a switch reads "Leaves the case check"
- [x] `enum`: "Choice Mode has the options OFF (0), LOW (5), and HIGH (6)"
- [x] assignment inside a condition: "While c, newly set to the result of calling getchar, is not equal to EOF"
- [x] function-like macros: standard ones (`isspace`...) read as calls; others as "the result of the macro
      LOOKUP with i". Only when every argument written in the source is found, whole, in clang's tree;
      otherwise the bracket stays. Facts for non-standard macros come from their full expansion
- [x] Fixed (found by review): a macro argument's text was read from where the macro was used
      ("item isalpha of s"); a loop with a `return`/`exit` inside said "Repeats n times" instead of
      "up to n times"; a stored `a ? b : c` had no clear boundaries
- [x] Golden files 47 (switch edge cases) and 48 (loops that stop early) added; each fix proven guarded
- [x] `python3 -m pytest tests/ -q` -> 155 passed
- [x] Census: 3100 of 3276 statements described (94.6%, was 90.8%), 0 crashes
- [x] **Reading test with a person who doesn't read C** (2026-09-27): the exact step-by-step English was NOT
      understandable to a non-coder ("I don't get it"). The same program as a mode table WAS understood.
      Conclusion: exact steps stay as the checkable layer; people need a summary layer on top (M1.7)
Last verified: 2026-09-27, in Claude's container

Remaining gaps (census): `goto` (57) + labels (15), `++`/`--` inside expressions (36), unusual macros (28),
calls through function pointers (27), comma operator (7).

### M1.7 -- mechanical summaries (`--summary`) (2026-09-27)
What each function does as a whole, built by fixed rules from the exact tree and facts. No AI model.
Where no rule fits, the exact step is shown, marked "(exact step)" -- never a guess.

Rules:
- **mode machine**: a switch on a named-option variable whose cases move it between options ->
  "In IDLE: if level is below 20 (LOW_LEVEL): goes to PUMPING; run_minutes becomes 0"
- **item loop**: a counted loop over one list where every part is a known pattern -> remembers the
  first match / stops at the first match (by return or break) / counts / adds up / keeps largest or smallest
- **straight line**: only simple changes -> "- count goes up by 1"
- **inputs in plain words**, from how the code uses them: a pointer read by position is a list;
  a pointer the facts show being written is "changed in place"
- **Changes / Also** lines from the facts: data it changes vs. other effects (input/output, ending the program)
- a record's fields are called by their own names only if no other name in the function matches

Checks:
- [x] Pump reading test: every answer read off the summary matches the running program (7/7)
- [x] Summary goldens for 8 files, reviewed line by line; review caught and fixed: "if it gets through
      every item" wrongly used after a loop that stops with `break`; "1 (true)"; "(head)" in parentheses;
      "Changes: only can end the whole program"; an empty case showing nothing
- [x] Each safety rule proven guarded (removing it fails a test): break-vs-return wording, a change after
      the mode change that reads the mode, fall-through switches, field names colliding with locals
- [x] `python3 -m pytest tests/ -q` -> 170 passed
- [x] Second reading test (traffic light): the user found the written summary still not working for them
      -> moved to diagrams (M1.8)

Not yet summarised (shown as exact steps): loops over two lists, clamping, loops containing a switch,
if/else chains outside a mode switch, nested loops.

### M1.8 -- diagrams (`--graph`, `--graph-out FOLDER`) (2026-09-27)
One diagram per function, drawn from the exact tree -- no pattern rules needed, so nothing is left out.
- **State diagram** when the whole function is a switch on a named-mode value that changes it
  (pump, traffic light): one box per mode, one arrow per change, labelled with the real condition and
  the changes in the order they happen. "No arrow applies = stays the same" is stated in the title.
- **Flowchart** for everything else: boxes for changes, diamonds for decisions, hexagons for counted
  loops, arrows back for repeats; fall-through, break and continue drawn as the arrows they are.
- Same graph in two spellings: **Mermaid** text (`.mmd`: people, AI, GitHub renders it) and **Graphviz**
  (`.dot` -> `.svg`/`.png`, needs `sudo apt install graphviz`).

Checks:
- [x] 348 diagrams from all golden files + the 271 real-world functions: 0 crashes, 0 Graphviz errors
- [x] Structure test on every golden file: one start, every box reachable, only end boxes have no way out
- [x] Mermaid goldens for 4 files; pump, traffic light, search loop and fall-through switch checked by eye
- [x] Review fixed: the second arrow of `a ? b : c` said "otherwise" instead of its real condition;
      a mode with no way out now says "never leaves this mode"
- [x] Each graph rule proven guarded (fall-through, the other arrow's condition, break, continue)
- [x] `python3 -m pytest tests/ -q` -> 229 passed
- [ ] Reading test with the diagrams (next)

### M1.9 -- one master chart of the whole program (`--master FOLDER`) (2026-09-27)
- Every function's diagram becomes a labelled group in ONE picture; dashed blue "runs X" arrows go from
  the box that calls a function into that function's group; shared variables are cylinders with dotted
  purple "changes"/"reads" arrows (label pinned at the variable end). Order: `main`, then functions
  nobody calls, then the rest in call order.
- Programs over 12 functions or 140 boxes get an OVERVIEW: one box per function (names only, left to
  right), "runs" arrows, and one legend box listing each shared variable's changers and readers.
  (cJSON: 113 functions -> readable call map; the per-function diagrams hold the detail.)
- Also written as Mermaid (`master.mmd`, groups as subgraphs) for AI and GitHub.

Checks:
- [x] Invariant on every golden file: the chart's "runs" arrows are exactly the calls the facts record
      (no missing arrow, no invented one), and Graphviz accepts the chart
- [x] Fixed before it could bite: calls made inside a state-diagram mode weren't recorded (missing arrow);
      the "changes" label floated next to an unrelated function
- [x] Goldens: `52_tank_sim` (master chart), `53_calls_everywhere` (calls inside a mode, a call only in a condition)
- [x] Each guard proven (calls in modes, calls in conditions, invented arrows)
- [x] `python3 -m pytest tests/ -q` -> 292 passed

### Chart-first loop, part 1: check code against an approved plan (`--check PLAN`) (2026-09-27)
The plan is a mode diagram in Plainsight's own diagram words (see PLAN_GUIDE.md), so an approved
diagram IS a plan. Checking is by behaviour, not by comparing pictures:
- the plan runs as a small simulator (`plan.py`); the user's C is compiled with a generated test program
  and run for real (`harness.py`) -- no interpreter of Plainsight's own is trusted for the code side;
- inputs: every mode x values around every number in the plan and the code (c-1, c, c+1), exhaustive up
  to 20,000 cases, else a fixed-seed sample that still covers every value of every input;
- differences are reported as concrete examples in plain words (`check.py`), grouped, plainest first;
  what isn't checked (changes outside the record, inside called functions) is always stated.
- The machine is found from TYPES (a record with a named-mode field), not from the code's shape, so the
  code may be written any way at all.

Checks:
- [x] Correct pump: MATCHES (10,368 cases). Same behaviour written with if/else and `>= 3` for "over 2":
      MATCHES (no false alarm)
- [x] Six realistic planted mistakes all caught with a concrete example: `>` for `>=` at 90; timer not
      reset; fault a minute early; faults compared the wrong way; FAULT quietly recovering; timer counted
      after the checks instead of before (code no longer shaped like the plan)
- [x] A difference that exists only NEXT to a number (plan "over 90", code ">= 92": differ only at 91) is caught
- [x] Round trip: Plainsight's own diagram of pump / traffic light / alarm machine, used as the plan, matches
- [x] Fixed on the way: multi-line diagram titles made invalid Mermaid; a plan with an extra mode gave a
      confusing error instead of "the plan has a mode SERVICE that the code doesn't have"; a code machine
      shaped differently from the plan couldn't be tested at all
- [x] Bad plans explain themselves (wrong arrow words, unknown change, contradiction "never leaves" + arrow)
- [x] Each guard proven (neighbour values, "then if" grouping, field comparison, planned calls, mode matching)
- [x] `python3 -m pytest tests/ -q` -> 312 passed (needs gcc for tests/test_check.py)

### Chart-first loop, part 2: AI drafting, generated tests, limits loosened (2026-09-27)
- **Draft** (`plainsight draft "words" --code FILE -o PLAN`, `draft.py`): an AI model (OpenAI-compatible
  server such as LM Studio via PLAINSIGHT_LLM_URL, or the Anthropic API via ANTHROPIC_API_KEY) gets the
  plan language and the code's exact names; its answer must parse AND use only names the code has, else
  the problem goes back to the model (3 tries). The plan is drawn as a picture for approval.
- **Generated tests** (`--make-tests OUT.c`, `maketests.py`): a standalone C file whose expected results
  come from the PLAN; per plan line the example where the most planned changes are visible, plus the
  boundary pairs one step apart. 15 tests for the pump catch all 6 planted mistakes.
- **Limits loosened**: plain functions (rules plans, `gives back`); shared variables set, read and
  compared; records containing lists (the lists are named as not checked); conditional every-time
  changes; `calls f` = "runs f, whatever f does" (what f changes is exempt, and said so).

Checks:
- [x] Whole loop on a new job (conveyor gate): words -> plan -> the approval picture exposed two plan
      mistakes (a counter that counted the wrong thing; a jam that could never happen) -> code -> the
      check caught a real `>` for `>=` -> fixed -> 20,000 cases match -> 16 generated tests pass
- [x] Draft tested with a stand-in model server (both API formats): a wrong name is sent back and fixed;
      three bad answers give up with the reasons; the full CLI loop runs from words to passing tests
- [x] Fixed on the way: the generated tests missed a forgotten reset (the example started at the reset
      value); a second "every time first" note replaced the first instead of adding to it
- [x] Each new guard proven (visible changes, shared-variable exemptions, gives-back comparison, error
      sent back to the model, unknown names refused, notes adding up)
- [x] `python3 -m pytest tests/ -q` -> 326 passed

### Chart-first loop, part 3: limits fixed (2026-09-27)
- **Plan warnings** (`plancheck.py`): the plan is run step by step from its start mode (`%% start:`),
  with inputs around every number at each step; modes that can never be reached, arrows that are never
  taken and modes with no way out are reported -- in `draft`, `--check` and `build`. It finds the
  impossible JAMMED of the first gate draft by itself.
- **Examples** (`examples.py`): `example: inputs -> results` for ANY function: lists in, lists changed
  in place, text, printing (captured per call), records (`p.field`), shared variables, values passed by
  pointer. Each example is C code around the real function, run in its own process (no leaking between
  examples; a crash fails only that example). The same file is the standalone test file. Compile errors
  are explained as "example N: the value X = ... doesn't fit that input".
- **One command** (`build.py`, `plainsight build "words" -o FILE.c`): draft -> picture + warnings ->
  person approves (y / n / "I edited it") -> AI writes the code -> compile errors and Plainsight's report
  go back to the AI (3 rounds) -> standalone tests written and run. Existing code is checked, and only
  rewritten with --fix.

Checks:
- [x] Plan warnings: the flawed gate draft and the flawed logger plan are caught; an arrow hidden by an
      earlier one is caught; a counter that needs 12 steps to build up is correctly reachable; good plans
      get no warnings
- [x] Examples: lists, in-place lists, printing, records, shared variables all match correct code; four
      mistakes caught with plain reports; shared variables proven not to leak; a crash fails only its example
- [x] Build with a scripted AI: words -> buggy code -> report sent back -> fixed; compile errors sent back;
      gives up after the rounds; nothing written for a refused plan; an edited plan is the one used;
      existing code untouched without --fix
- [x] Each new guard proven (search depth, input variety, isolation, list comparison, crash reports,
      pass-by-value, report sent back, approval, --fix)
- [x] `python3 -m pytest tests/ -q` -> 356 passed

### A CLI tool for AI agents in any sandbox (no API keys) (2026-09-27)
Decision (user): no API calls. In a sandbox the agent (Claude Code, Codex, a chat sandbox) IS the AI;
Plainsight only gives it commands, and keeps a person in charge of what gets built.
- Removed: `draft`, `build` and all model-calling code (and their stand-in-server tests).
- Added commands: `agent-guide` (the workflow: docs/AGENTS.md), `guide` (the plan language:
  docs/PLAN_GUIDE.md), `names FILE.c` (names a plan may use), `plan-check PLAN [--code FILE.c]` (usable?
  + picture + plan warnings), `approve PLAN --by NAME` (a stamp holding the plan's SHA-256), `check FILE.c
  PLAN [--make-tests] [--json]`, `doctor` (what's missing + install commands).
- Every check reports approval: approved by NAME / no person has approved it yet / the plan CHANGED after
  approval. (A stamp is a record, not a lock: an agent that ignores the guide could still run approve.)
- `--json` for agents; quiet exit when output is cut short (`| head`).
- Self-contained: Dictum's two pure-Python files it needs are bundled (`plainsight/_dictum/`); the repo's
  copies are used when present. `pip install .` in a clean venv outside the repo works.

Checks:
- [x] Agent commands tested end to end (usable/unusable plans, warnings in JSON, approval recorded,
      changed plans flagged, approve refuses unusable plans and needs a name, JSON check output)
- [x] Works copied away from the Dictum repo; real `pip install` into a clean venv runs doctor, guides, check
- [x] Each new guard proven (changed-after-approval, bundled fallback, plan-check exit code, approve refusal)
- [x] `python3 -m pytest tests/ -q` -> 352 passed

Still limited (stated, not hidden): calls through function pointers can't be followed; examples only
test what they show; examples need Linux/macOS (fork); C only (no C++); the plan warnings search is
bounded (it says so when it hits its limit).

### M2 -- round-trip proof through Dictum (outline)
C -> Dictum nodes -> `emit_c` -> C'. Compile both, run both, compare output.

### M3 -- call graph with must/may effects (outline)
Side effects passed up the call chain; "may" never collapsed into "yes"/"no".

### M4 -- AI summaries in a fixed claim format, checked against facts (outline)

### M5 -- impact queries, CLI polish, HTML report (outline)

## Interface

Command: `python3 -m plainsight FILE.c [--facts] [--json] [--strict]`

Output on success (example):
```
Function add
  Takes: a (whole number), b (whole number)
  Gives back: a whole number
  Steps:
    1. Creates s (whole number), starting at a plus (b times 2).
    2. Adds s to total.
    3. Gives back s.

Covered: 3 of 3 statements.
```
- `--facts`: adds a facts block under each function.
- `--json`: prints one JSON object (English + facts), nothing else.
- `--strict`: exit 3 if anything is not yet supported.
- Broken C: `error: FILE.c does not compile: <first error>` on stderr, exit 2.
- Missing file: `error: FILE.c does not exist` on stderr, exit 1.

## Install

From the `plainsight/` folder (inside the dictum_final repo):

    sudo apt install clang graphviz   # clang reads C; graphviz draws the diagrams (Kaggle: !apt-get install -y clang graphviz)
    pip install -e .            # gives you the `plainsight` command
    plainsight path/to/file.c --facts

Without installing: `python3 -m plainsight FILE.c` from the `plainsight/` folder.
Tests: `pip install pytest` then `python3 -m pytest tests/ -q`.

## Final Test Plan (M1)

Automated:
- [x] All M1 checks, re-run together (134 passed)
- [x] Runs from a directory other than the project folder (`test_runs_from_another_directory`)
- [x] A realistic multi-function file (a thermostat controller, `41_thermostat.c`) renders with its one gap listed (`enum`); found 3 bugs, all fixed and now tested

Checked by hand:
- [ ] A person who doesn't read C reads three outputs and says what each function does -- **needs you**

Deliberately not covered in M1:
- `switch`, `goto`, `enum`, unions, function pointers, `++`/`=` inside larger expressions,
  preprocessor macros with arguments -- shown as "not yet supported", to be added one at a time
- C++ -- C only for now
- Exact integer sizes (`int` is described as "whole number" regardless of platform width)

### Incident and rebuild: plainsight/snapshot.py (2026-09-27)
- What happened: work from another session (timelines, strength score, C++/Arduino via lang.py, lock/diff
  commands and tests/test_lock_diff_strength.py) was already in this folder. A later session, not checking
  first, wrote a different snapshot.py over the original with a shell redirect. The original source was
  lost (the folder isn't under git; the only bytecode copy was in a pytest temp folder that got cleaned up).
- What was kept from the original: its outline (every function name, arguments, docstring) read from that
  bytecode before it disappeared, and its 12 tests.
- Rebuild: plainsight/snapshot.py is NEW CODE written to that outline; it says so in its first lines. It
  passes the original 12 tests plus tests/test_snapshot_rebuild.py (4 more: a changed diff never says "NO
  CHANGES"; plain-function locks; record-filler locks; side-effect changes). Each guard proven by planting
  bugs (one arrow per mode pair, mode-only replay, zeroed recordings, "NO CHANGES" always).
- The colliding session's own edits were all reverted before the rebuild (its duplicate commands in cli.py,
  its behavior.py, its graph.py change). Nothing else from the other session was modified.
- `python3 -m pytest tests/ -q` -> 368 passed.
- Lesson, now practice: put plainsight/ under git; check a folder for others' work before building; never
  overwrite a file one didn't create (use create-only writes).
