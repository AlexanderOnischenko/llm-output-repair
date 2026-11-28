---
name: json-repair-loop-operator
description: Run a bounded offline JSON repair improvement loop on an immutable private regression corpus, with an independently qualified oracle.
---

# JSON repair Loop Operator

Read `experiments/loop_engineer/README.md` and the installed
`$loop-engineer` skill. Do not create a new workflow engine or replace the
existing parser test harness.

- **Goal:** first verified fix of one actual, independently qualified
  Fetchgraph-originated `llm-output-repair` regression with no previously
  passing regression becoming red. Do not optimize throughput yet.
- **Executor:** execute exact raw input with the library's `llm-repair replay`
  or `experiments/loop_engineer/evaluate.py`. Never call an LLM to reproduce
  a parser defect. Record commit, Python/dependency revisions and hashes.
- **Assessor:** the frozen, externally reviewed fixture expectation is
  authoritative. Confirm same corpus fingerprint and no losses on fixed or
  negative cases. Require ordinary pytest and targeted Fetchgraph checks as
  independent gates, not just evaluator's corpus-only green.
- **Fixer:** identify the earliest parsing transition violating the observed
  contract (candidate selection, escaping/quotes, structure, decode). Make
  the smallest semantic repair at its owner; check failing, positive and
  negative controls. Do not special-case fixture strings or change expected
  results to obtain a green verdict.
- **Operator:** start from pinned baseline in a dedicated worktree; run
  preflight, route exactly one qualified blocker, assess changed code, and
  stop at first accepted result or shared trial budget. Keep ordinary Codex
  as a comparable control arm, not an assumed inferior alternative.

## Reuse verified evidence

Before scheduling a check, compare its request with the original Executor
receipt. Reuse only a confirmed passing result with the same code tree SHA,
corpus fingerprint (or explicit corpus-free workload), environment digest and
verification command. Include command arguments, target, configuration and
relevant host revisions in the recorded inputs. Confirm that the receipt
covers the current gate. Missing fields, an unconfirmed receipt or a changed
input require a new native check and leave the gate pending.

For a commit created after verification, check its tree against the assessed
tree with `git rev-parse '<commit>^{tree}'`. Record the accepted commit and
link the original receipt without modifying it. Run again if the command
depends on commit identity or the gate specifically requires fresh execution
on that commit. Independent Assessor authority stays in force.

Record which checks were executed or reused and why in the
[runbook](../../../experiments/loop_engineer/README.md#verify-and-record-evidence).

Authority: one isolated candidate branch only; no changes to the corpus,
assessor, workflow contract, production branches, Fetchgraph runtime or
external services. Default provisional trial budget: one defect family,
at most two code interventions and 30 minutes per arm. No network requests
or inference during evaluation. If no qualified raw dump is available,
stop at readiness and report `NOT_RUN` rather than inventing an input.

Report status, actual case IDs, frozen fingerprint, code SHAs, the first
failing boundary, tests, intervention count, time/usage, and exact
next action. No private prompt or raw model output in public logs.
