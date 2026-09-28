# Plainsight workflow for AI agents

You are an AI agent in a sandbox. A person wants C code that does what they
describe, and wants to be sure it does -- without reading C. Plainsight makes
no AI calls of its own: YOU write the plan and the code; Plainsight checks
them and keeps the person in charge of what gets built.

Follow these steps in order. Don't skip the approval.

## 0. Setup (once)
    plainsight doctor          # shows what's missing and the install command
Needs clang and gcc; graphviz for pictures.

## 1. Learn the plan language
    plainsight guide

## 2. Write the plan (a file, e.g. plan.mmd or plan.plan)
- A machine that moves between named modes -> a mode diagram.
- A function of numbers -> a rules plan.
- Lists, text, printing -> examples. Add examples AT every number the person
  mentions ("90 or more" -> an example at exactly 90), because that's where
  mistakes hide.
- If code already exists, use its exact names: `plainsight names FILE.c`
- If something isn't in the description, choose the simplest behaviour; the
  person will see it in the picture. Don't invent extra behaviour.

## 3. Check the plan and draw it
    plainsight plan-check plan.mmd [--code FILE.c]
Fix every problem it lists and re-run until it says USABLE. Take PLAN
WARNINGS seriously: "X can never be reached" usually means the plan (or
your reading of the request) is wrong. Fix it, or ask the person.

## 4. Get the person's approval -- the one step you can't do yourself
Show the person the picture (plan.png) and the plan text. Explain any
warnings in plain words. Ask: "Is this what you want?" Change the plan until
they say yes. Then, and only then:
    plainsight approve plan.mmd --by "THEIR NAME"
Never run approve without their explicit yes. Checks report the approval,
and flag it if the plan changes afterwards.

## 5. Write the code
C11, one file, exactly the names in the plan; no main() unless asked.

## 6. Check the code against the plan; fix until it matches
    plainsight check FILE.c plan.mmd --json
- "result": "matches" -> done with this step.
- "different" -> each problem is a concrete example of what the plan says vs
  what the code does. Fix the CODE (not the plan -- the plan is approved).
  If you believe the plan itself is wrong, stop and ask the person; a changed
  plan needs a new approval.
- Read "not_checked": tell the person about anything listed there.

## 7. Write the tests and hand over
    plainsight check FILE.c plan.mmd --make-tests FILE_plan_tests.c
    gcc -std=c11 FILE_plan_tests.c -o plan_tests -lm && ./plan_tests
Give the person: the code, the approved picture, the test file, and one line
per "not_checked" item.

## Other useful commands
    plainsight FILE.c --master chart       one picture of the whole program
    plainsight FILE.c --graph-out pics     a picture per function
    plainsight FILE.c --summary            what each function does, in plain words
