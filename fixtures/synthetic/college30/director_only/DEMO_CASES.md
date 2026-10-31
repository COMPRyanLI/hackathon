# Director's case map — keep away from blind graders

`scenario_key.json` records intended code behavior, not validated human grades.
No model recall or grading-accuracy results are implied.

| Case | IDs | What to inspect |
|---|---|---|
| Equivalent correct approaches | CONTROL T001/T002/T008/T022/T023/T026/T027 | Different loops, recursion and lists should not attract style deductions. |
| Same return-count defect | CONTROL T003/T004 | Correct output, but count excludes the starting value. |
| Main calibration example | CONTROL T005/T006/T007 | Numerically correct hailstone values printed as floats; C4 formatting interpretation. |
| Previously unselected affected work | CONTROL T025 | Same true-division formatting issue plus an unrelated factor error; apply the C4 decision without erasing the other defect. |
| Similar but different defect | CONTROL T017 | Missing final output and wrong count; do not sweep into a float-formatting policy. |
| Output representation boundary | CONTROL T018 | Prints a list on one line; different from numeric-equivalent per-line floats. |
| Missing return and syntax | CONTROL T019/T030 | Distinguish readable code evidence from unexecutable/missing behavior. |
| Inclusive endpoint | HIGHER T003/T004/T024 | Product omits term(n); T024 also has an accumulate endpoint problem. |
| Same zero-initialization defect | HIGHER T005/T006/T028 | Related product defect with other differences. |
| Counterexample to shallow similarity | HIGHER T016 | Reverse fold appears fine for add/mul; fails the explicitly required ascending order for a non-associative merger. |
| Correct values, requirement breach | HIGHER T022/T023 | Wrappers bypass accumulate / use two statements. |

Suggested live-review pilot: CONTROL T003, T004, T005, T006, T007, T008, T017,
T018, T022, T023. Leave T025 out to show the later assessment-wide note scan.
Run 10 students, up to 20 reviewer pairs, before recording. Retrieval is budgeted;
the pair you want is not guaranteed. Choose an actually produced case and verify
its source evidence; never fabricate a model result or relabel a different pair.

The existing deterministic note evaluator accepts this source condition:

```json
{"source_regex":"^\\s*n = n / 2\\s*$","present":true}
```

For this exact fixture it matches CONTROL T005, T006, T007 and T025, because each
submission is one file and these functions contain that line. Confirm those
four source files before recording. It is a literal-source selector, not a
general semantic detector for floating-point mistakes. A spacing change or an
equivalent expression can evade it. Keep the note's scope assignment-wide with
the C4 limit stated in its text; adding component scope can require extra review
under the current implementation. Scoring implication must say "C4 only; other
criteria unchanged." A human chooses full credit or a formatting deduction.

After approval, all 30 submissions are scanned. Expect these four matches, other
files not matching this literal expression, and no automatic grade updates.
Do not state that nonmatching files are defect-free or that the scan proves
semantic equivalence. Head-TA judgment establishes the actual applicability.

The team may independently give identical scores. That is valid. Frame the demo
as avoiding repeated policy work rather than inventing a measured disagreement.
