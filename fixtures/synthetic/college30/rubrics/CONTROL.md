# CONTROL analytic rubric — demo-authored, 10 points

This detailed partial-credit scheme is authored for GradeLens. It is not BYU's
official rubric. The source's whole HW1 is correctness-based, out of 2 points,
with one point lost per incorrect problem. We use a selected, rescaled exercise
set to study criterion-level consistency; do not compare these scores to BYU grades.

| ID | Criterion | Max | Award guide |
|---|---|---:|---|
| C1 | Largest proper divisor on composite inputs | 3 | 3: correct generally; 2: coherent divisor search with one localized bound/selection defect; 1: meaningful divisibility reasoning with a major selection defect; 0: absent/unrelated or only an unsupported guess. |
| C2 | Factor function contract and prime/boundary handling | 1 | 1: returns an integer, including 1 for primes and n=2; 0.5: a correct prime/boundary path but a return/type defect elsewhere; 0: prime/boundary handling absent or incorrect. |
| C3 | Hailstone transitions and termination | 3 | 3: correct even/odd transitions and termination on the declared domain; 2: one localized transition/control defect; 1: only one branch coherent; 0: absent or fundamentally wrong. |
| C4 | Hailstone printed sequence | 1 | 1: all values, start and final 1, one per line; 0.5: one localized omission/formatting defect; 0: no sequence or fundamentally different output structure. Numeric-equivalent float text requires head-TA policy; record a provisional 0.5–1 range, not a secretly decided point value. |
| C5 | Hailstone return count | 2 | 2: count includes every printed sequence value, including n=1; 1: correct counting structure with a one-off error; 0: missing or unrelated return. |

Grade each criterion independently from source evidence. Do not deduct C3 for a
correct transition that happens to print `5.0`; assess formatting under C4.
Missing output belongs to C4, not automatically C3 or C5. Multiple effects may
affect different criteria, but do not add an extra overall penalty.

For syntax errors, identify the exact line. Judge readable intent provisionally
and mark confidence low; the whole-file execution cap is **unresolved** until the
head TA sets it. Do not quietly copy the MIT or any other course's cap.
For unresolved C4 or syntax policy, leave the final total blank, record the
criterion's range and ask one focused question. No grader should invent a policy.

Use `grading/criterion_reviews_blank.csv` for evidence and explanations. A human
head TA adjudicates borderline interpretations. A reference implementation is
one acceptable answer, not a requirement to use its particular algorithm.
