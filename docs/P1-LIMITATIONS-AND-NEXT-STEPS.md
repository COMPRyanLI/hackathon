# GradeLens P1 follow-up review — 2026-10-31

## What changed

This is a local grading-consistency assistant: a grader saves a draft, continues
working, and receives evidence-backed review cards. The head TA resolves a shared
policy question once; typed rules find potentially relevant cases, preserve
exceptions, and never rewrite grades. A draft revision requires a deliberate grader
action. Imported scores are historical observations, not ground truth.

The review found two gaps in the reported fast-feedback behavior: queue priority
cannot interrupt a model call that has already started, and the UI hid completed
source cards while waiting for that model. The implementation now has one CPU
feedback lane and one serialized model lane, and displays source cards immediately
after their stage completes. A regression test blocks an active model call and
verifies that another draft gets feedback before the model is released.

| Reported limitation | Implemented now | What still needs work or human evidence |
|---|---|---|
| No accuracy figures | Regression/browser tests and an explicit evaluation protocol below; no fabricated accuracy | Independently reviewed real cases, including unflagged cases and alternate methods |
| Missing instructions, starter code, screenshots | Missing evidence remains distinct from missing student work; source observations propose no deduction | Obtain course artifacts later; until then keep dependent criteria unresolved |
| No specific SA1/SA2/SA5 checks | Header observations for these assignments; SA2 function inventory; SA5 callback keyword and explicit-framerate observations | These are not correctness tests. Required functions, drawings and event behavior remain unverified |
| 13 ambiguous SA3 files | Source-view role editor; validated role choices; audit trail, manifest refresh and invalidation/recomputation of affected feedback | A person must inspect real files. Mixed-program files stay unresolved; file-level assignment cannot establish line-level scope |
| Long SA6 prompts overflow | Bounded windows for analyst/reviewer, original evidence references, direct dependencies when possible, pass/error manifests, partial results | Whole-program and transitive dependency reasoning are unverified; huge fixed context/single lines, >16 passes and note-model scans still have visible limits |
| Single-user labels are not authentication | Retained explicit local-only deployment and provenance labels | True multi-user operation needs authentication, assignment/course authorization and server-enforced head-TA permissions |

An additional correctness boundary was tightened: the SA6 diagonal/backtracking
detectors recognize syntax patterns. An absent pattern is not proof of absent
behavior, and a present pattern is not proof of correctness. On real submissions,
these facts therefore yield uncertain applicability and no numeric proposal,
including after a head TA approves a scale. Known synthetic fixtures still exercise
the propagation workflow. That demonstration must not be represented as proof that
real SA6 partial-credit grading has been validated.

## How to run and inspect the changes

From PowerShell in the repository root:

```powershell
.\start-gradelens.cmd
```

Open `http://127.0.0.1:8000`. Restart an older running server first so it loads the
revised worker and backend. The production frontend has already been rebuilt.
No new package or model installation is needed for these changes. Cloud inference
remains off; no credentials need to be added.

Use the existing **SYNTHETIC P1 demo** for the complete approval/impact demonstration
in `P1-RUN-AND-DEMO.md`. Its approvals concern invented examples only.

1. Save a total-only draft. Confirm the save completes without waiting for a model.
2. While model analysis is pending, source feedback should be visible and usable.
   Open the stage details to distinguish source-check completion from model status.
3. In the source viewer, inspect **Component role**. Confirm only a role supported
   by the file; choose **Unresolved / mixed components** when both SA3 programs are
   in one file. Saving this mapping changes no grade. Old cards become stale, and
   drafted peers are refreshed because their comparisons may depend on the mapping.
4. For SA1/SA2/SA5, inspect source-observation cards. Absence of a card does not mean
   correctness; the cards may appear under the full list when higher-priority
   issues occupy the initial three-card limit.
5. For a long-source review, inspect the partial-coverage status and stage/usage
   details. A failed window must remain visible. A partial pair does not support
   a whole-program equivalence judgment.
6. Confirm that approving guidance does not change an original total or a draft.
   Only **Revise** creates a new draft revision. Keep unresolved cases visible in
   the pre-release snapshot.

Run automated verification:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test.ps1
```

This now builds the frontend **before** browser tests, so Playwright exercises the
current UI. Browser tests use Edge (Chrome fallback), a separate scratch database,
synthetic data and a temporary loopback server. Check for skips when running on
another machine: a missing browser must not be mistaken for successful UI coverage.

Optional repeatable real-data check:

```powershell
backend\.venv\Scripts\python.exe scripts\check-long-sa6.py
```

This uses local Qwen only, imports into a new ignored database, reviews the two
longest SA6 bundles and compares before/after hashes of originals. It does not
approve guidance or create drafts in the active database. It deliberately sends
neither historical grades nor grader identities to the model.

## Validation record

- Baseline: 100 tests passed.
- First follow-up full run: 112 tests passed in 109.79 seconds, including three
  Playwright browser tests through Edge; TypeScript and production build passed.
- Additional targeted checks after the real-code SA6 applicability guard: 15 passed.
- Final complete run: **115 tests passed in 102.96 seconds**, including all three
  Playwright browser tests through Edge (none skipped); TypeScript and the
  production build passed.
- Actual local Ollama check: both source-window passes completed in 62.12 seconds,
  `partially_valid / partial / insufficient_evidence`. The bundles contained 7,613
  and 6,616 source characters. All 539 original files were byte-identical afterward.
  Private details: `data/private/long-sa6-check-20261031T084011559790/report.json`.
- Playwright images: `p1-feedback-pending.png` uses a controlled pending-model API
  response with real synthetic source cards; `p1-component-review.png` shows the
  mixed synthetic file left unresolved after exercising role persistence.

These are software and workload measurements. None is a grading-accuracy score.
No real grading policy was approved, no real component role was confirmed, no
student code was executed, and no code was committed or published.

## Head-TA involvement and the next real-data pilot

The head TA should answer policy questions in batches, not interrupt every save.
For a repeated issue, review representative examples and counterexamples, then
approve one assignment/criterion-scoped interpretation with explicit exceptions.
The issue remains shared across relevant cases and future drafts reuse that answer.
New evidence, changed policy, mixed components or interacting failures can create
an exception; they must not be silently forced into the earlier rule.

For simple observable conditions, code can apply an approved rule and show a
proposal for human review. For semantic claims such as a missing diagonal check,
policy approval and case applicability are separate decisions. In this version,
the head TA can settle the scale once, while graders inspect applicability and
record their reasons in their draft/revision notes. There is no per-case semantic
confirmation control that safely enables automatic propagation yet.

Prioritize a later applicability-confirmation workflow over adding more agents:

1. Store a reviewer-confirmed fact against a source hash, criterion, exact component
   span and evidence. Record who confirmed it and the relevant rule version.
2. Keep it distinct from a model suggestion or a pattern observation. Invalidate
   it when source, scope or guidance changes.
3. Permit numeric proposals only when the condition and all required exceptions
   are established. Approval of the scale never establishes these facts by itself.
4. Evaluate any future semantic matcher on held-out implementations before letting
   it reduce individual review. Until then use it to prioritize cases, not assign
   semantic certainty.

## Collect useful labels without pretending the historical totals are correct

Start with 20–30 real cases for an exploratory pilot, not a definitive accuracy
study. Sample across assignments, graders, same-score and different-score pairs,
unflagged submissions, parse/anonymization issues, alternate methods and ambiguous
components. Sampling only current flags measures neither overall usefulness nor
retrieval recall.

Have two reviewers independently inspect the original source and available guide,
initially hiding imported scores, grader identity and the model recommendation.
Record: assignment, criterion, source hashes, compared component, evidence lines,
whether the same issue applies, missing evidence, applicability decision,
acceptable points/range only if justified, and a brief reason. Preserve disagreement
as a label; ask the head TA to adjudicate repeated ambiguity once and record both
the initial judgments and the later resolution. Do not erase the disagreement.

Keep examples used to define an anchor separate from examples used to test it.
For retrieval recall, fully review all eligible pairs in a small selected subset,
or establish an independently curated reference set; reviewing only retrieved
pairs cannot reveal missed matches. Report the denominator and selection method.

Useful first measures are:

- Fraction of surfaced cards judged useful/actionable, with counts by card type.
- Pair precision and recall within the explicitly labelled subset at the actual
  review budget, with abstentions/unknown cases reported separately.
- Agreement with adjudicated applicability and point ranges, plus coverage: a
  system that abstains on every case cannot be described as accurate grading.
- Duplicate shared-question rate, number of head-TA answers, and feedback/save
  latency (median and tail), measured separately from model completion time.

With this small pilot, report counts and disagreement examples, not broad fairness
or bias claims. Historical total differences alone cannot identify which grader is
wrong. Different student groups, component scope, absent evidence and changed
policy can explain those differences.

## Grader severity and changes during a grading session

The imported CSV has no reliable grading chronology. File order, student ID and
import time are not substitutes for the time a TA originally graded the work.
Use new draft events for future timing analysis and distinguish an imported-total
draft from an actual new grading event.

A useful next experiment gives graders overlapping, blinded anchor responses at
several points in their session, with a fixed rubric/guidance version and recorded
timestamps. Compare their judgments on the same anchors and preserve uncertainty.
Flag possible drift for moderation; do not automatically curve scores, normalize
TA averages or infer harshness from different groups of students. Report observed
agreement within this sample rather than population fairness.

## Before a shared-server version

Keep the current app on loopback for the hackathon. A shared deployment should
first add authenticated identities, server-enforced grader/head-TA roles, course
and assignment access checks, secure sessions and authorization tests. Bind every
audit event to an authenticated actor rather than a user-entered label. Then add
transaction-safe multi-worker job claiming, migration/backup/restore checks and
concurrent-save/approval tests before replacing the single-process SQLite design.
Changing the bind address, adding a tunnel or adding a password only to the UI
does not supply these controls.
