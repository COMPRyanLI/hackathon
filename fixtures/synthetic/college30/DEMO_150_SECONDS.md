# GradeLens recording plan — exactly 2 minutes 30 seconds

Recommended story: **one grading question, one human decision, every affected
submission reviewed**. Use CONTROL from this packet for the entire main cut.
The cohort is synthetic; disclose that visibly. Precompute slow model work.

## Before recording

1. Have teammates grade using `grading/TEAM_WORKFLOW.md`. Use actual team marks
   or clearly labeled staged marks; do not call invented marks human evaluation.
2. Import into the separate demonstration database using the README. Check the
   SYNTHETIC banner, correct assessment, 30 submissions, max 10, and Context text.
   Show one task rather than squeezing both into the video.
3. Run the CONTROL pilot in `director_only/DEMO_CASES.md` using **Configured
   providers**, not the unrelated canned mock responses. Allow ample preparation
   time: the repository reports minutes for comparable local-model pilot runs.
   Check every quoted line and choose an actual useful review case. Nothing in
   this packet guarantees what a model or retrieval budget will return.
4. Prepare a draft Head TA note about C4 float-text formatting. The head TA
   chooses the treatment. For the scripted rehearsal below, choose full C4
   credit for numeric-equivalent, one-value-per-line output. Leave component and
   requirement scope fields blank; state "C4 only" in the interpretation and
   scoring implication. Use the literal condition from `DEMO_CASES.md` and verify
   its four matches. It finds this known source pattern, not all equivalent code.
5. Keep T025 out of the pilot. Do not approve the note before the live approval
   shot. On a prior rehearsal database, check approval → impact → human decision
   → export. Recreate the recording database from scratch if a clean take is needed;
   do not erase real data or try to unapprove historical policy.
6. Close private course-data windows and notifications. Record at 1080p, enlarge
   the UI enough for code/evidence to read, and use one narrator plus one operator.
   The head TA can be off camera. Prepare cut points at the timestamps below.

## Main cut: 150 seconds

| Time | Show / action | Narration |
|---|---|---|
| 0:00–0:15 (15s) | Title overlay, then CONTROL submission list and recorded grader totals. | "Thirty students, two graders, one rubric. A small interpretation can become inconsistent feedback across a class. GradeLens helps teaching teams find the question, agree on an answer, and check who else it affects. These submissions are synthetic." |
| 0:15–0:35 (20s) | Context with C4, then a short source view; briefly show source attribution and reference solution. | "We supplied a college-style Python assignment, a reference answer, and a detailed rubric. Our teammates grade independently. The rubric leaves one interpretation open: should a correct sequence lose credit when values print as five-point-zero instead of five?" |
| 0:35–1:00 (25s) | A completed review case, side-by-side source, evidence lines and focused question. Label prerecorded inference if edited. | "GradeLens retrieves potentially comparable work and brings the original code into review. Here, the relevant evidence is the division and printed sequence. We can inspect the exact lines and the alternative explanation before making a decision. Similarity selects candidates; it is not a grade or a misconduct finding." |
| 1:00–1:25 (25s) | Head TA notes: prepared interpretation, condition and scoring implication; click **Approve and scan every in-scope submission**. | "The head TA resolves the interpretation once: numerically equivalent values on separate lines receive full formatting credit. We limit that decision to this criterion. Approval records a versioned decision and triggers a review of the whole assignment. The original grades stay unchanged." |
| 1:25–1:50 (25s) | Impact review: full scope, four literal matches, then T025's source and its separate factor defect. | "The important part is who else we would have missed. This scan includes all thirty submissions, not just the initial review sample. T025 was outside that sample, but contains the same selected pattern. Its separate factor bug remains a separate issue. The scan selects matches; a person confirms the treatment." |
| 1:50–2:15 (25s) | Record a human applicability/decision or score proposal; show original alongside proposal, then Audit & export. | "A reviewer accepts, explains, or rejects the proposed treatment. Changes are human decisions, with the original score and the supporting evidence retained. We can export the moderation record so the team can see what was considered and why. The app does not overwrite the gradebook." |
| 2:15–2:30 (15s) | Close on the approved note and impact list; overlay "Evidence → shared guidance → wider review". | "The value is consistent reasoning across the class: one clarification reused, overlooked work revisited, and teachers in control. GradeLens turns a grading question into a traceable team decision." |

Approximately 330 spoken words; rehearse at about 135–145 words per minute,
leaving the remaining seconds for clicks and pauses. The timeline totals 150
seconds. Trim narration before rushing the evidence or the approval shot.

If teammate marks do not disagree, say "potentially different treatment" and
show the unresolved rubric question. Do not claim the software found an actual
inconsistency that the completed review does not support.

## Reliable fallback and editing guidance

- If inference is slow, cut from run setup to a previously completed run in the
  same dataset. Overlay "Analysis completed before recording"; do not imply
  live sub-second inference. Keep source and result identifiers consistent.
- If no useful model case is returned, show the source and raise the note manually:
  "Our grader noticed this interpretation; GradeLens records it and checks its
  wider impact." Do not say the AI discovered it. The note/scan workflow still works.
- If a scan is still pending, use the rehearsed completed-impact shot with a
  visible time cut. Show the actual scope and statuses; never paint over failures.
- Avoid spending the recording on installation, CSV paths, model downloads or
  all navigation tabs. Use one understandable policy question as the thread.
- Do not claim proven accuracy, universal fairness, automatic regrading,
  plagiarism detection, or that all 435 possible student pairs were reviewed.
  Budgeted retrieval and a full-scope note scan are different kinds of coverage.

## Alternate cut: showcase existing P1 features instead

If shared-question deduplication and draft feedback are the main pitch, use the
existing **Load SYNTHETIC P1 demo (SA3/SA6)** fixture throughout that cut.
Its students are P001 etc., not this 30-person cohort. Disclose "scripted P1
workflow simulation" and follow `docs/P1-RUN-AND-DEMO.md` for exact controls.
Do not claim the new BYU exercises have SA3/SA6 detector support.

| Time | Existing P1 action |
|---|---|
| 0:00–0:15 | Explain inconsistent partial-credit decisions; show the synthetic banner. |
| 0:15–0:35 | SA3 rubric evidence and P001 draft save; move to another student while feedback queues. |
| 0:35–0:55 | P001 loop-bound card → Request head-TA calibration; show previously saved P002/P003 joined to the same issue. |
| 0:55–1:20 | Shared questions: one issue, three examples; head TA resolves with the prefilled condition, exception and treatment. |
| 1:20–1:45 | Open impact review: all 11 SA3 submissions; P006 found outside earlier drafts, P005 exception stays uncertain. |
| 1:45–2:05 | Save P004 to show guidance reuse; human revises P001 from 9 to 8. |
| 2:05–2:30 | Pre-release & snapshots: unresolved items and original/draft/proposal separated; close on teacher control. |

Keep SA6 anchor propagation as a backup clip or post-pitch walkthrough. Adding
another problem narrative usually costs more than it communicates in 150 seconds.
