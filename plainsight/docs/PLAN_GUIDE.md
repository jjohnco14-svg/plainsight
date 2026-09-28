# Writing a plan Plainsight can check

A plan says how a function should behave, in the same words Plainsight uses
in its own diagrams. A person approves it (by looking at its picture); then
code is checked against it by running both. For the full step-by-step
workflow an AI agent follows, run `plainsight agent-guide`.

There are three kinds of plan, and examples can be added to any of them.

## 1. A machine with modes (Mermaid state diagram)

    %% function: gate_tick
    stateDiagram-v2
        CLOSED --> OPEN : if box_arrived and not bin_full: open_seconds becomes 0, blocked_seconds becomes 0
        OPEN --> JAMMED : if blocked_seconds is at least 5
        OPEN --> CLOSED : otherwise, if open_seconds is at least 3 and not sensor_blocked
        JAMMED --> CLOSED : if reset_pressed: jams goes up by 1
        note right of OPEN : every time first: open_seconds goes up by 1
        note right of OPEN : every time first, if sensor_blocked: blocked_seconds goes up by 1
        note right of OPEN : every time first, if not sensor_blocked: blocked_seconds becomes 0

- `%% function: NAME` -- which function is the machine (needed only if there are several).
- `%% start: MODE` -- where the machine starts (default: the first mode written). Used by the plan
  warnings, which run the plan step by step from here and report modes that can never be reached and
  arrows that are never taken.
- `%% example: ...` -- an example (see 3) checked on top of the diagram.
- `A --> B : if CONDITION: CHANGES` -- in mode A, if CONDITION holds, make CHANGES in order, then go to B.
- `A --> B : always` -- always go from A to B.
- `..., then if CONDITION2` -- after the changes, go to B only if CONDITION2 holds. Write one arrow per
  outcome with the same "if ...: changes" part.
- Arrows out of a mode are checked top to bottom; the first match wins. `otherwise,` is an optional reminder.
- `note right of A : every time first: CHANGES` -- happens in mode A before any arrow is checked.
  `every time first, if CONDITION: CHANGES` -- the same, only when CONDITION holds.
  Several notes on one mode all happen, top to bottom.
- `note right of A : never leaves this mode`
- No arrow applies -> the mode stays the same.

## 2. A plain function (rules, one per line)

    function: grade
    every time first: graded goes up by 1
    if score plus bonus is at least 90: gives back 4
    otherwise, if score plus bonus is at least 80: gives back 3
    otherwise: gives back 0

- Rules are checked top to bottom; the first match wins. `otherwise: ...` catches everything left.
- If the function gives something back, every case must reach a `gives back` (end with `otherwise:`).

## 3. Examples (any function: lists, text, printing, records)

    function: minutes_to_fill
    example: levels = [50, 18, 40, 70, 95], n = 5 -> gives back 3
    example: levels = [10, 90], n = 2 -> gives back 1

    function: clamp_all
    example: values = [1.5, -3, 20], n = 3, lo = 0, hi = 10 -> values becomes [1.5, 0, 10]

    function: greet
    example: name = "Ana" -> prints "Hello, Ana!\n", greetings becomes 1

    function: pump_step
    example: p.state = PUMPING, p.run_minutes = 2, p.faults = 2, level = 50, flowing = false -> p.state becomes FAULT

- Inputs: `name = value` for every input; values are numbers, true/false, names from the code (modes,
  constants), "text", or [lists]. Record fields as `p.field`; shared variables by name.
- Results: `gives back V`, `NAME becomes V` (a list changed in place, a record field, a shared
  variable, a value passed by pointer), `prints "TEXT"` (with \n for line breaks).
- Each example runs on its own (nothing carries over between examples); a crash fails only that example.
- Examples test exactly what they show. For rules about numbers, a rules plan or a mode diagram is
  stronger (it tests around every number automatically); if you use examples, include one AT each
  number (exactly 90 for "90 or more").

## Words
- Conditions: `is below`, `is over`, `is at least`, `is at most`, `is`, `is not`, joined by `and`, `or`,
  `not`, with `( )` for grouping. `flowing` alone means "flowing is true".
- Changes: `x becomes 5`, `x goes up by 1`, `x goes down by 2`, `gives back V`, `calls f`.
- Values: numbers, `true`/`false`, `plus`/`minus`/`times`, names of the record's fields, the function's
  inputs, shared (file-level) variables, and named constants from the code (`LOW_LEVEL`).
  `20 (LOW_LEVEL)` is fine: the name in brackets is a reminder.
- Field names are the record's own names (`faults`, not `p->faults`).
- `calls f` means "runs f, whatever f does": what f changes is not compared (the report says so).

## What is and isn't checked
- Checked, by running the real compiled code: the new mode, every plain-number field of the record,
  every shared variable, and what a plain function gives back -- for every mode and for inputs around
  every number in the plan and the code (e.g. 89, 90, 91); and that each `calls f` really happens.
- Checked by examples: anything the examples show -- lists, text, printing, records, shared variables.
- Always stated in the report, never passed silently: what rules and mode plans can't see (record
  fields that aren't plain numbers, what named functions change, changes through other pointers,
  printing) -- use examples for those; and calls through function pointers.
- Rules and mode plans need inputs that are plain numbers or truth values; examples take anything.
- Examples run on Linux/macOS (each in its own process).
