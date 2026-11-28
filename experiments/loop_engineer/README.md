# Loop Engineer experiment: JSON repair, first success

This experiment is intentionally **offline** and starts from a **private,
reviewed regression corpus**. No inference is run by the evaluator. It does
not turn a crash into a ground-truth expectation: a maintainer must first
qualify and promote the dump using the existing llm-repair CLI.

## Scope and starting point

- Target product: llm-output-repair public parse/repair entry points.
- Target integration: Fetchgraph (branch \`dev\`, not its still-older \`main\`).
- Experiment source revision: \`bbb7ade390d1696651a35816cebfe6b3ee838e5f\`.
- Fetchgraph \`dev\` was verified at \`2c053f4578ad32997c624c73ffa17bc9c2295c5d\`;
  its dependency metadata pins \`llm-output-repair\` at \`717d4ee101799e4d0cc0be49038d83300abfa849\`.
  **Do not claim Fetchgraph is exercising the newest library** until the
  intended revision and environment have been confirmed.
- Active path to observe: Fetchgraph's \`PlanParser\`/RetrievalEngine →
  \`llm_output_repair.parse_json(policy="safe")\`; generic \`JsonParser\` →
  \`repair_json\`. Check which path generated each dump.
- No raw dump or corpus content belongs in a public commit, CI artifact,
  LLM prompt, issue, or log unless its owner has reviewed and authorized it.

This is a **FIRST_SUCCESS** experiment, not yet a repeatability/efficiency
claim. Consumer: maintainer deciding whether the Loop Engineer operator can
make one admissible, small repair safely.

## Prerequisites

1. Install [Loop Engineer](https://github.com/AlexanderOnischenko/loop-engineer)
   in Codex (or use an already installed copy).
2. Configure Fetchgraph's explicit opt-in:
   \`FETCHGRAPH_REPAIR_CRASH_CAPTURE=failed\` and, if desired, an absolute
   \`FETCHGRAPH_REPAIR_DUMP_DIR\`. Confirm from startup logs that capture is ON.
3. Collect at least one **real** library failure. For old \`qa_008/qa_009\`
   use a saved raw model response if present; error messages alone cannot
   reproduce it. Do not rerun expensive inference merely to create inputs.
4. Qualify each candidate: check which parser was called, which policy it
   used, whether the observed failure is an actual repair defect or an
   intended rejection, and what its independently established expectation is.
5. Use \`llm-repair promote\` to build a **private** corpus with at least one
   \`known_bad\` case, one previously passing \`fixed\` case and a \`fixed\`
   intentional-rejection case. The evaluator refuses an incomplete cohort.

## Freeze once, then compare

Place fixtures in \`$PRIVATE_CORPUS/{known_bad,fixed}/*.json\`, OUTSIDE Git.
Use **two isolated working trees** derived from the same baseline commit;
neither agent can write to the other's tree or shared corpus. Use the same
predefined workload, prequalified oracle and max intervention budget.

\`\`\`sh
# Run at the exact starting commit of llm-output-repair:
python experiments/loop_engineer/evaluate.py snapshot \
  --corpus "$PRIVATE_CORPUS" --out "$PRIVATE_RESULTS/manifest.json"

python experiments/loop_engineer/evaluate.py run \
  --corpus "$PRIVATE_CORPUS" \
  --manifest "$PRIVATE_RESULTS/manifest.json" \
  --arm baseline --out "$PRIVATE_RESULTS/baseline.json"
\`\`\`

The manifest records case IDs, fixture hashes, and a deterministic corpus
fingerprint, **not raw model text**. Run refuses mismatched corpora or changed
expectations. Keep the private corpus immutable for both arms; record its
physical snapshot location, Python version, commit SHA, and a digest of installed
dependencies in private experiment notes.

Assign equivalent limits (proposed initial trial: one defect family, up to
two candidate patches, up to 30 minutes of active agent work per arm).
Set these limits **before** running. One worker/worktree per arm;
no simultaneous writes to the same files.

### Arm A: control

Run ordinary Codex, WITHOUT invoking Loop Engineer, with the same task,
inputs, acceptance, budget and permission boundaries. Save its code commit
and evidence to the control worktree. Then, from that worktree:

\`\`\`sh
python experiments/loop_engineer/evaluate.py run \
  --corpus "$PRIVATE_CORPUS" \
  --manifest "$PRIVATE_RESULTS/manifest.json" \
  --arm control --out "$PRIVATE_RESULTS/control.json"
python experiments/loop_engineer/evaluate.py compare \
  --baseline "$PRIVATE_RESULTS/baseline.json" \
  --candidate "$PRIVATE_RESULTS/control.json" \
  --out "$PRIVATE_RESULTS/control-comparison.json"
\`\`\`

### Arm B: Loop Engineer

Invoke \`$loop-engineer\` (optionally via the reusable role skill in
\`.agents/skills/json-repair-loop-operator/SKILL.md\`). Fix the **same**
preselected defect, same initial commit and budget. Its Executor uses the
existing CLI and pytest; its Assessor uses the frozen corpus and the already
qualified expectation. Its Fixer traces the first violated parsing contract.
It must not rewrite the expected result, quietly delete cases, or relax
intentional rejections. Run the analogous \`run --arm loop\` and \`compare\`.

### Acceptance

A **candidate FIRST_SUCCESS** requires:

- At least one previously failing, independently qualified \`known_bad\`
  case matches its fixed expectation.
- All pre-existing \`fixed\` cases still match their expectations.
- Every negative/rejection guardrail remains green.
- \`python -m pytest -q\` and any relevant integration checks pass at the
  candidate revision; evaluate.py does **not** substitute for the full suite.
- Baseline/candidate use the same fingerprint. The accepted patch is committed
  only to its own worktree/branch and reviewed; no production merge.
- Report time, number of model turns, tokens or cost if available, number of
  attempted patches, interventions, and first bad boundary for **both arms**.

The evaluator reports \`eligible_first_success\` for *corpus-only* acceptance.
Do not equate it with full product acceptance until pytest and relevant
Fetchgraph integration tests have been checked.

**Stop:** no qualified real failure; ambiguous oracle; drifted corpus;
two distinct non-progressing interventions; budget exhausted; or all gates
satisfied. Missing real evidence yields \`NOT_RUN\`, never a fabricated success.

## Safety and ownership

The loop may edit only the isolated candidate worktree's library code/tests.
The corpus and evaluator/assessor stay frozen. Promotion of changed expectations
requires a fresh baseline and maintainer decision. Do not modify Fetchgraph,
Loop Engineer's own skill, public \`main\`, or confidential fixtures within this
experiment. Store only sanitized verdicts/digests in experiment reports.
