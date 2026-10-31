# SYNTHETIC P1 FIXTURE - not real class data

Invented students P001-P012, graders, scores, an SA3-style compound-interest task,
an SA6-style puzzle task, a synthetic grading guide (`guide.txt`) and synthetic
external flags. Nothing here describes real students, graders or findings.

SA3 scenarios (program roles are inferred from code, never from file names):

| Student | Scenario |
|---|---|
| P001, P002, P003 | Program 1 loop with an inclusive end bound (`<=` / `range(..., END + 1)`). Three graders save drafts; one shared head-TA question. |
| P004 | Same pattern, drafted after the head TA answers: reuses the answer, no new question. |
| P005 | Inclusive bound but the counter starts at 1 (iteration count unchanged): a materially different exception. |
| P006 | Inclusive bound, never drafted or flagged, blank SA3 score: found by the impact scan. |
| P007 | Exclusive bound: control (does not apply). |
| P008 | Uses `**` for compound interest: exceptional resubmit policy, human confirmation only. |
| P009 | Percentage rate form (0.05 with 1 + RATE): explicit guide allowance. |
| P010 | Both programs in one file: ambiguous component mapping. |
| P011 | Lower-case constants. |
| P012 | No files in this export (not in current export, not zero). |

SA6 scenarios: complete solution, missing diagonal check (several), grid representation,
missing backtracking, and an incorrect diagonal test, for partial-credit calibration.
