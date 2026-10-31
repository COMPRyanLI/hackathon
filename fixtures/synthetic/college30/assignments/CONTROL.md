# CONTROL: factors and sequences (10 points)

Synthetic practice assignment adapted from BYU CS 111 Spring 2023 Homework 1,
questions 3 and 5. These are two selected exercises, not the whole original homework.
Source and original grading policy: https://cs111.byu.edu/archives/sp23/hw/hw01/

Implement two functions in one Python file:

- `largest_factor(n)`: for an integer greater than 1, return its largest positive divisor strictly smaller than itself.
- `hailstone(n)`: for a positive integer, print the starting value and each subsequent value through 1, one per line. Even values are halved; odd values become `3*n+1`. Return the number of printed values, including the start and final 1.

Public sample answers: `largest_factor(15) == 5`, `largest_factor(80) == 40`,
`largest_factor(13) == 1`. For `hailstone(10)`, the output is
`10, 5, 16, 8, 4, 2, 1` on separate lines and the return value is 7.

Demo-specific scope: use Python 3.12 and valid integer inputs only; assess
`2 <= n <= 1000` for factors and `1 <= n <= 100` for hailstone. No input prompts,
external packages or top-level calls. Equivalent algorithms, recursion and list
storage are allowed. No speed, comment-count or defensive-input-validation marks.
Integer-looking output formatting versus `5.0` is deliberately left for head-TA
calibration; the required numeric sequence and line separation are explicit.

Local reference: `reference_answers/control.py`. Local rubric: `rubrics/CONTROL.md`.
