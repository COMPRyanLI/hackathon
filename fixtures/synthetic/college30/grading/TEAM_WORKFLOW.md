# Team grading workflow

1. Give graders only `assignments/`, `rubrics/`, `reference_answers/`,
   `submissions/` and copies of the blank forms. Keep `director_only/` and
   `generate.py` out of the grading packet; they reveal designed defects.
2. Map slots A/B/C in `review_assignments.csv` to team members. Each person gets
   ten students as primary and ten as secondary. Each student has two assignments.
   With two graders, divide the primary work 15/15 and swap for a second reading.
3. Read the task and rubric, then grade independently. In
   `criterion_reviews_blank.csv`, complete your primary/secondary rows with
   points, exact function/line evidence, reasoning and any unresolved question.
   Each criterion gets two reviews: 600 rows total. Do not read the other grader's
   scores until both reviews are saved. For a shorter rehearsal, double-grade
   T003–T007, T009–T010, T016, T023 and T030 first.
4. Keep unresolved totals blank. A blank means pending, not zero. The head TA
   records decisions with a date, rationale, applicable criterion and exceptions.
   Revisit every submission affected by that decision, including full-credit work.
5. Copy `scores_blank.csv` to a new `scores_team.csv`; enter actual first-pass
   totals and grader labels there after any blocking policy is settled. Preserve
   the first-pass sheet unchanged as the original gradebook. Use separate CSVs
   per assessment if different people graded the two tasks: GradeLens's one
   `grader` column applies to every score in that row.
6. Keep later proposals and final decisions separate from the original scores.
   Do not manufacture disagreement in a file labeled team-graded. For rehearsal,
   describe hypothetical contrasts verbally or label any staged grades clearly.

Suggested shared question: "For numerically correct hailstone output printed
as floats, should C4 receive full credit or lose 0.5 for formatting?" T005–T007
are useful after blind grading. This asks about a point policy, not algorithm
correctness. The supplied rubric intentionally leaves that interpretation open.

After grading, count disagreements and inspect why. Separate rubric ambiguity,
missed source evidence, and different error interpretations. Those human labels
can support a later evaluation; this packet alone provides no accuracy measure.

CSV files are UTF-8 and may be edited in a spreadsheet or text editor. Blank
forms contain no assigned grades or invented team judgments. Do not rerun the
generator on completed forms; use copies for your work.
