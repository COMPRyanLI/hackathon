# GradeLens P1: run guide, demo walkthrough, results and limits

Implements rubric-guided calibration on top of P0. The primary workflow is now:

**save draft grade → asynchronous feedback → keep/explain, revise, defer or link a
shared question → head TA resolves a repeated issue once → the answer applies to
existing and later matching work → pre-release review → versioned moderation
snapshot.**

Everything runs locally (FastAPI + SQLite + React, Ollama for models). Cloud
inference stays off. Student programs are parsed, never executed. Nothing is
written back to the gradebook CSV or an LMS, and no approval changes a grade.

## Start

```powershell
# Open PowerShell in the repository root first.
.\setup-gradelens.cmd     # only after pulling new code: installs the pinned packages (now incl. pypdf, playwright) and rebuilds the UI
.\start-gradelens.cmd     # open http://127.0.0.1:8000
```

The existing database is migrated additively on first start. Before new tables
are added, a copy is written next to it as `gradelens.db.backup-<UTC time>`.
No P0 table, row, decision or audit event is changed.

Run all checks (build the UI first, then backend tests including three Playwright browser tests):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

The browser tests drive the installed Microsoft Edge (falling back to Chrome)
through Playwright. They start their own server on a free port with a scratch
database and skip cleanly if no browser can be launched.

## Demo walkthrough (synthetic data, about 5 minutes)

All data in this demo is invented (`fixtures/synthetic/p1/`, see its README).
Synthetic assessments automatically use the labeled mock provider; every screen
shows a SYNTHETIC banner.

**Choose the demo assessment, not a real `cs1` import.** The dropdown must show
`[SYNTHETIC] synthetic-p1 · SA3` and the student must be **P001**, not S001.
If feedback says `content_only` / "No rubric linked", this is not the configured
demo. The calibration button appears inside a policy-question feedback card;
it is not a global button and does not appear on every saved grade.

1. **Import** tab → *Load SYNTHETIC P1 demo (SA3/SA6)*. This imports a synthetic
   guide, an SA3-style and an SA6-style task, and three synthetic external flags.
   Select the SA3 assessment.
2. **Rubric** tab: the synthetic SA5 lists 11 points under a 10-point heading
   (blocks totals), and SA3 has an unresolved practice exemption. Each criterion
   shows its page quote and classification (explicit, interpretation required,
   external evidence required).
3. **Grading workspace**, P001: enter a total only (9.0), grader label
   "Synthetic Grader A", save. "Saved revision 1 · feedback queued" appears at
   once; click **Next** and keep grading while feedback runs.
4. Back on P001, the feedback card *Inclusive loop end bound in Program 1* quotes
   `while year <= END_YEAR:` and the rubric clause. It says that the intended
   iteration count is unresolved and asks one question. Click **Request head-TA
   calibration**.

   If the card is absent, verify the dropdown/student above and that a draft was
   saved. If the issue was already requested, open **Shared questions** instead.
5. Save drafts for P002 (Grader B, 10.0) and P003 (Grader C, 8.5). Their cards say
   *already awaiting head-TA review (#1, N affected)*: no duplicate question.
6. **Shared questions**: one issue, 3 affected submissions, with examples,
   drafts and recorded totals. Resolve it as an interpretation. The form is
   prefilled with the typed condition (`program_1` / `loop_inclusive_bound == true`),
   the exception (counter starts at 1) and the point treatment (award 0 on the
   final-balance criterion).
7. **Shared questions → select the resolved question → Open impact review**
   (also available directly in the table, or under **6 · Head TA notes → approved
   note → Impact review**). A question marked `awaiting_head_ta` has no approved
   guidance and therefore no impact scan yet: complete the resolve form first.
   Requesting calibration alone does not approve it. All 11 SA3 submissions are evaluated. P006 *applies*
   although it was never drafted or flagged and has a blank recorded score. P005 is
   *uncertain* (listed exception), P010 is *uncertain* (ambiguous component
   mapping), P008 is *uncertain* (no loop, so the fact is unknown, not false) and
   P007 does not apply. No grade changed.
8. Save a draft for P004: the card says *Covered by guidance v1*; no new
   question is created. Save P005: it gets an individual-review exception card.
9. On P001, use **Revise** on the covered card: total 8.0 with the final-balance
   criterion at 0. This explicit human action is the only way a draft changes.
10. **Pre-release & snapshots**: unresolved policy, unavailable evidence, failed
    checks and unanswered feedback are listed separately, with unchecked pairs
    disclosed. *Create moderation snapshot* shows version 1: P001's recorded total
    9.0 beside draft revision 2 (8.0), the rule-derived proposal, the disposition
    and the unresolved rows.

SA6 (partial credit) on the same demo: saving a draft for P002 shows a candidate
reason (*no diagonal conflict check*) and no numeric proposal (draft mode). After
the head TA approves one anchor (missing diagonal → 3–4 of 6, exception: also no
backtracking), P002, P004 and P007 get the 3–4 proposal. Complete solutions and
the *incomplete diagonal* case are not swept in, and the incomplete diagonal stays a
separate question. Starter-code and required-file criteria stay unresolved.

## What was built (by milestone)

| Milestone | Delivered |
|---|---|
| A | Ignore rules for SA2–SA6, `anon_pt1/` and the supplied flags/guide; additive migration with backup; SA1–SA6 batch preview with each assignment committed separately; evidence manifests with explicit states (available, not_in_current_export, confirmed_not_submitted never inferred, unreadable, anonymization_affected, not_applicable, unknown); guide PDF → draft rubric with page/quote provenance and validation issues; typed AST facts; component roles inferred from code only; external flags as unverified leads; immutable, idempotent drafts with optimistic concurrency; asynchronous feedback status. |
| B | SA3 feedback engine: approved guidance first, typed detectors (inclusive loop bound, exponentiation rule as human-confirmed review item, explicit allowance, evidence absent for full credit), same-assignment/component peer comparison (total-only gaps never claim a deduction), bounded cached model pass; at most three initial cards; suppression of unchanged repeats; shared calibration issues with deterministic matching, auto-linking, audited merge/split/defer/resolve; resolution through the versioned guidance service with typed, component-scoped conditions, exceptions and point treatment; scope-wide impact scan; batched refresh of linked and later drafts. |
| C | SA6 candidate reason taxonomy (descriptive facts: diagonal check, backtracking) with draft mode, one approved anchor with a range treatment, traceable proposals, exceptions; pre-release summary; versioned moderation snapshots (CSV + JSON) pinning revisions, guidance versions and unresolved items. |
| D (partial) | Rubric extraction and validation for all of SA1–SA6; SA4 while-loop and warning-only rules scoped to SA4; workload metrics endpoint. Evaluation against human labels has not been done (no labels exist). |

UI tabs: Grading workspace (default), Shared questions, Rubric, Import &
coverage (incl. six-assignment preview, manifests, flags), Pre-release &
snapshots, plus all P0 screens.

## Backend fix made during P1

All 58 API routes now open their database session with `scope="function"`.
FastAPI's default for yield dependencies committed *after* the response was
sent. A client could then read stale data right after a save, and a failed commit
could follow a success response (a silently lost draft). Now the commit happens
before the response, and a failed commit returns HTTP 500. A regression test
fails without the fix.

Second fix, prompted by the real-data timings: the bounded model pass now runs
as its own lower-priority job (`draft_model`). The original ordering test covered
drafts queued before a model call started; priority alone did not prevent a newly
arriving draft from waiting behind an already-running model call.

The follow-up review adds a separate CPU feedback lane alongside the single model
lane. A regression test deliberately blocks an active model call, saves another
draft and verifies its source feedback arrives before releasing the model. The
frontend now displays those cards while model analysis is still pending; previously
it hid them until the entire feedback job finished. This does not promise a fixed
latency under every workload, and model jobs still run one at a time.

Further changes from this review:

- Source view now has a component-role editor. Confirmation is audited, refreshes
  the evidence manifest, invalidates old feedback/criterion assessments and affected
  guidance applicability, and queues recomputation. Old cards cannot be acted on.
  Mixed files remain unresolved; file-level confirmation does not split a file.
- SA1/SA2/SA5 now have limited source-observation cards: header checks, SA2 function
  inventory, and SA5 callback keywords/framerate. These propose no points and do
  not claim to verify required functions, graphics or screenshots.
- Oversized local analyst/reviewer prompts fall back to bounded source windows,
  with original chunk/line references, direct dependency context when it fits,
  per-window quote validation and recorded coverage/errors. At most 16 passes
  (each permits one schema repair) use the same configured model. The result stays
  partial and a windowed pair never becomes a whole-program equivalence verdict.
- Real-data SA6 diagonal/backtracking patterns now return **uncertain** for numeric
  rule applicability. An approved scale does not prove that a new implementation
  has the same defect. Known synthetic cases still demonstrate anchor propagation.
  See the limitations guide for the distinction and future applicability workflow.

## Test results

Latest full verification on 2026-10-31 after the rubric/import corrections: **121 tests passed in 114.54 s**, including three
Playwright browser tests through Edge; TypeScript and the production build passed.
The baseline had 100 tests. P0 behaviour tests are retained. Details and remaining
limits are recorded in `P1-LIMITATIONS-AND-NEXT-STEPS.md`.

The SA4 correction preserves PDF indentation, closes the nested deduction list
before the separate 1-point award, and retains the width/height warning. Reparse
creates a new rubric version; prior parses and grades remain intact. The live
synthetic demo was upgraded to parser `guide-parse-v2-layout`. The real PDF was
read and validated for all six assignments, but was not automatically linked to
real grades or used to approve any grading policy.

For real data, import/select the **real** PDF in Rubric and link it to the correct
assessment. A synthetic guide cannot be linked to a real assessment. The earlier
local `cs1 · SA3 v1` import used the SA1 folder; it is retained with an **IMPORT
MISMATCH** warning. `cs1 · SA3 v2` uses the correct SA3 folder. New mismatched
folder/score-column imports are rejected, and editing import inputs clears the
previous preview. Linking a new rubric invalidates and refreshes existing draft
feedback without changing grades.

| File | Covers |
|---|---|
| `test_p1_workflow.py` | The eight required demonstration points end to end through the API |
| `test_p1_acceptance.py` (31) | Plan section 11, scenarios 1–25 and 27–40, each named by number |
| `test_p1_sa6.py` | Milestone C gate: draft mode, one anchor, traceable range proposals, counterexamples and starter-code criteria unresolved |
| `test_p1_backend_fixes.py` | Commit before response (fails without the fix), read-after-save, deterministic-before-model job ordering, SA4 rule scoping (10), candidate refresh after channel recovery (23) |
| `browser/test_p1_browser.py` | Scenario 41 (save, keep grading while pending, link, resolve once, impact, reuse, revise, snapshot) and scenario 26 (rubric conflicts, unverified flags, unknown evidence, unchecked pairs) |
| `test_p1_limitations.py` | Feedback during an active model call, mapping validation/invalidation, stale-card rejection, source-only observations, unknown dynamic values, real-code semantic-pattern guard |
| `test_source_windows.py` | Full source-window coverage, budget checks, per-window evidence rejection, partial failure, conservative pair aggregation, repair budget, pass limit and integrated analyst/reviewer fallback |
| `test_import.py` | The private SA1 snapshot test is now **versioned by source-manifest hash** (v1: 84/80, kept as history; v2: 85/81). An unrecorded export fails rather than passing on loosened counts |

These checks verify software behaviour on synthetic fixtures. They do not
measure grading accuracy.

## Real-data smoke check (separate ignored database)

Run on 2026-10-31 in `data/private/p1-smoke/` (git-ignored), with local Ollama
(`qwen3.5:4b-q4_K_M`, `qwen3-embedding:0.6b`) and the real guide PDF, all six assignments
and the supplied flags. No real policy was approved and nothing was sent off the machine.
**Originals unchanged:** the hash of every SA1–SA6 file and the CSV was identical before and after.

| Check | Observed |
|---|---|
| Six-assignment preview | Matches plan section 2.1 exactly: SA1 85/81/4/0/1, SA2 86/83/3/0/1, SA3 162/81/81/0/0, SA4 76/75/1/0/0, SA5 73/73/0/1/0, SA6 56/55/1/9/0 (files/bundles/multi-file/without score/parse failures) |
| Rubric validation (corrected after PDF visual review) | SA4 is **2 + 7 + 1 = 10**; the earlier 9-point/ambiguity report was a parser error, not a source conflict. SA5 genuinely lists 11 under a 10-point heading. Other retained issues: SA1–SA3 practice exemption, resubmit policies needing case review, SA6 discretionary partial credit, missing scaffold and external evidence |
| Component roles (SA3) | 68 of 81 bundles map to two programs from code alone; 13 files stay ambiguous |
| Evidence manifest | Students without code in this export are `not_in_current_export` (SA1 11 … SA6 37); never inferred as not submitted |
| External flags | 33 sides link to exactly one bundle, 1 to a file in a larger bundle, 2 SA3 sides unresolved; 34 linked sides agree with gradebook score/grader (record linkage only); 2 notes contradicted by source headers; 1 blank similarity kept as blank |
| Draft save latency | 3–18 ms (4 saves; two created explicitly from imported totals) |
| Feedback latency (first run) | Deterministic stage 31–93 ms of compute; local model source analysis 39–53 s per SA3 draft (two files, first-time content). With the model pass inline, later drafts' cards waited behind earlier drafts' model calls |
| Feedback latency (after the fix below) | 3 drafts saved back to back: deterministic cards ready 1.7–1.9 s after each save; model passes finished 50 s, 93 s and 132 s after saving (sequential, 38–48 s each) |
| Shared questions | 4 SA3 drafts with an inclusive program-1 loop bound → **1** shared issue awaiting the head TA with **4** affected cases; 0 duplicate questions |
| SA6 pipeline (3 students, budget 1) | Analyst 3/3, reviewer 1/1 completed in 108 s; no prompt overflow for these files (longer SA6 pairs are not yet tested on real data; the overflow guard is tested synthetically) |
| SA3 pre-release (no review run yet) | 3,240 possible pairs, 0 model-reviewed, all disclosed as unchecked; 1 unresolved policy issue; total proposals blocked by the practice exemption |

These are workload and behaviour observations, not accuracy measurements.

### Follow-up long-SA6 check

On 2026-10-31, `scripts/check-long-sa6.py` used local
`qwen3.5:4b-q4_K_M` on the two longest submission bundles (7,613 and 6,616 source
characters). Both window passes completed in **62.12 s**, with `partially_valid`,
`coverage=partial`, and `relation=insufficient_evidence`; no prompt-overflow failure.
All **539 original files** (SA1–SA6 plus gradebook) had identical SHA-256 hashes
before and after. This was a source-only check, without instructions, approved
policy or analyst summaries, using its own ignored database. It measures operation,
not grading accuracy. The private report is at
`data/private/long-sa6-check-20261031T084011559790/report.json`.

To repeat deliberately (uses the local GPU, does not modify the active database):

```powershell
backend\.venv\Scripts\python.exe scripts\check-long-sa6.py
```

## Remaining limitations

- **No accuracy claims.** No human-labelled criterion judgments exist, so feedback
  usefulness, retrieval recall and proposal agreement are unmeasured. Passing tests
  verify behaviour, not grading quality.
- **Assignment instructions, starter code, screenshots and outputs are absent.**
  Criteria depending on them stay unresolved, by design (for example SA3 correct
  balance/year, SA6 preserved scaffold and required files, all screenshot criteria).
- **Typed detectors remain narrow.** They cover SA3 (inclusive loop bound,
  exponentiation, constants), SA4 (while loop, warning-only window size) and SA6
  (diagonal/backtracking taxonomy). Other criteria rely on human judgment, the
  cached model summary and peer comparison. SA1/SA2/SA5 now have source observations,
  not full correctness checks. SA6 syntax patterns are unvalidated candidates;
  real-data numeric applicability remains uncertain even after an anchor. The
  synthetic propagation demo must not be presented as a real-data accuracy result.
- **Component roles are heuristic.** Real SA3 maps 68 of 81 bundles to two
  programs; the rest stay *ambiguous* until an operator confirms a mapping
  in the source-view editor (or `POST /api/artifacts/{id}/component-mapping`). No
  real role was confirmed during this review. A file containing both programs
  must stay unresolved until component spans/splitting are implemented. Other
  assignments use a whole-assignment role, which does not establish completeness.
- **Long prompts.** A conservative budget guard checks every local prompt (and
  Ollama's reported prompt tokens) against the 8K context. An over-budget prompt
  falls back to source-window analysis. Direct module dependencies are added when
  they fit; missing dependencies, failed windows and unverified cross-window
  behavior remain explicit. An oversized single line/fixed context or more than
  16 passes still fails visibly. Windows do not prove semantic equivalence.
  Note-applicability model scans retain the existing overflow guard; windowing
  is implemented for analyst summaries and pair review only.
- **Model feedback latency.** The real model takes about 40–50 s per first-time SA3
  draft in the earlier pilot, and model passes run one at a time. Source checks
  now have their own worker lane and are shown before model completion. The earlier
  1.7–1.9 s measurements were three back-to-back saves, not a universal guarantee.
  Unchanged content reuses cached analysis; the new schema version invalidates
  pre-window analyst/reviewer caches once.
- **Single-user loopback prototype.** Grader and head-TA labels are provenance,
  not authentication. Do not expose the app publicly to simulate several graders.
- **Model stage in draft checks** reuses the P0 analyst (source facts with
  validated quotes). Rubric-dependent judgments come from typed facts, approved
  guidance and humans, not from the 4B model.
- Browser tests cover the primary workflow and the rubric/flags/manifest/
  pre-release views, pending-model source cards and role confirmation, but not
  merge/split in the UI or the real-guide import button.

For a concrete pilot, label collection, grading-drift caveats and a shared-server
roadmap, read [P1 limitations and next steps](P1-LIMITATIONS-AND-NEXT-STEPS.md).
