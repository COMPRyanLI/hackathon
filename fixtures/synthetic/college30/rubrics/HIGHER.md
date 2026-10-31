# HIGHER analytic rubric — demo-authored, 10 points

This is a locally authored analytic rubric. The online course uses a four-point,
correctness-based whole-assignment policy with one point lost per incorrect
problem. Our 10-point adaptation exposes different partial-credit interpretations.

| ID | Criterion | Max | Award guide |
|---|---|---:|---|
| H1 | Product's initialization and inclusive 1..n traversal | 2 | 2: identity 1 and complete traversal (including n=0 extension); 1: one localized identity, endpoint or early-return defect; 0: both accumulation and traversal fundamentally wrong/absent. |
| H2 | Product calls term and multiplies returned terms | 2 | 2: callback used at each visited index and multiplication is correct; 1: callback used but wrong combining operator, or correct multiplying pattern ignores callback; 0: neither. Endpoint failures are assessed under H1. |
| H3 | Accumulate preserves base and ascending fold | 2 | 2: starts at base, handles n=0, folds 1..n in order; 1: one localized initialization/traversal/empty-case defect; 0: several major defects or absent. |
| H4 | Accumulate uses the supplied merger and term | 2 | 2: general callbacks with accumulated result; 1: only one callback is respected, or each iteration resets to base; 0: both ignored/absent. |
| H5 | Two wrappers meet abstraction and syntax requirements | 2 | Each wrapper: 0.5 for correct merger/base/arguments passed to accumulate, 0.5 for a single return statement calling accumulate. A wrapper that bypasses accumulate earns 0 for that wrapper. |

Treat callback composition and traversal as distinct evidence. For example, an
exclusive endpoint loses H1/H3 credit but does not automatically lose H2/H4.
Judge wrapper wiring independently of a defect in the student's accumulate.
Purely different naming, recursion, helper use and standard-library reduction
are not deductions. State evidence for each non-full criterion; do not infer
quality from similarity to the reference answer.

For undefined names or syntax errors, grade readable intent provisionally and
request a single head-TA decision about any execution cap. No such cap is
preapproved in this dataset. Final totals remain blank until policy is resolved.

The ascending fold clarification is explicit in our task. A commutative but
non-associative merger such as `(a+b)/2` distinguishes a reverse fold from the
required fold; do not assume that every merger is associative.
