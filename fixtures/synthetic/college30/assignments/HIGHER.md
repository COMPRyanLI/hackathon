# HIGHER: higher-order functions (10 points)

Synthetic practice assignment adapted from BYU CS 111 Spring 2023 Homework 2,
required questions 1 and 2. Optional Church numerals are excluded.
Source and original grading policy: https://cs111.byu.edu/archives/sp23/hw/hw02/

Implement `product(n, term)` over indices 1 through n, multiplying the results of
`term`. Implement `accumulate(merger, base, n, term)` by starting with `base` and
merging in each `term(k)`. A merger is a commutative two-argument function, not
necessarily addition or multiplication. Define `summation_using_accumulate` and
`product_using_accumulate` using one return statement that calls `accumulate`.

Public sample answers include: identity product for n=3 is 6; square product for
n=3 is 36. Accumulation with addition, base 11, n=0 returns 11; with addition,
base 11, n=3 and squared terms returns 25. Summation of the first five squares
is 55.

Demo-specific scope: `0 <= n <= 20`; extend product to n=0 with value 1.
Use the ascending left fold `value = merger(value, term(k))`. This explicit local
clarification matters because commutativity alone does not imply associativity.
Functions and results are numeric. Standard-library helpers, for/while loops
and recursion are allowed. No top-level execution, input prompts or packages.
For wrappers, docstrings do not count as executable statements.

Local reference: `reference_answers/higher.py`. Local rubric: `rubrics/HIGHER.md`.
