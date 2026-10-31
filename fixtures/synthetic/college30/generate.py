"""Rebuild invented submissions and blank forms; never uses private class data.

Run only before grading: this overwrites generated blank forms and submissions.
The scenario key is for the demo director, not the blind graders.
"""
from pathlib import Path
import csv
import hashlib
import json
import textwrap

ROOT = Path(__file__).resolve().parent


def clean(s):
    return textwrap.dedent(s).strip() + "\n"


FACTORS = [
    ("correct descending search", """
    def largest_factor(n):
        candidate = n - 1
        while candidate > 0:
            if n % candidate == 0:
                return candidate
            candidate -= 1
    """),
    ("correct smallest-divisor shortcut", """
    def largest_factor(n):
        for divisor in range(2, n + 1):
            if n % divisor == 0:
                return n // divisor
    """),
    ("correct ascending search", """
    def largest_factor(n):
        answer = 1
        for candidate in range(2, n):
            if n % candidate == 0:
                answer = candidate
        return answer
    """),
    ("returns smallest nontrivial divisor; primes OK", """
    def largest_factor(n):
        for candidate in range(2, n):
            if n % candidate == 0:
                return candidate
        return 1
    """),
    ("includes n as its own factor", """
    def largest_factor(n):
        candidate = n
        while n % candidate != 0:
            candidate -= 1
        return candidate
    """),
    ("prime case returns None", """
    def largest_factor(n):
        for candidate in range(n - 1, 1, -1):
            if n % candidate == 0:
                return candidate
    """),
    ("assumes every input is even", """
    def largest_factor(n):
        return n // 2
    """),
    ("correct comprehension", """
    def largest_factor(n):
        return max(d for d in range(1, n) if n % d == 0)
    """),
    ("tests quotient instead of remainder", """
    def largest_factor(n):
        answer = 1
        for divisor in range(2, n):
            if n // divisor == 0:
                answer = divisor
        return answer
    """),
    ("only checks divisors up to square root, omits paired factor", """
    def largest_factor(n):
        answer = 1
        for divisor in range(2, int(n ** 0.5) + 1):
            if n % divisor == 0:
                answer = divisor
        return answer
    """),
    ("correct half-range search", """
    def largest_factor(n):
        for divisor in range(n // 2, 0, -1):
            if n % divisor == 0:
                return divisor
    """),
    ("prints result instead of returning it", """
    def largest_factor(n):
        for divisor in range(n // 2, 0, -1):
            if n % divisor == 0:
                print(divisor)
                break
    """),
]

HAIL = [
    ("correct iterative sequence", """
    def hailstone(n):
        length = 1
        print(n)
        while n != 1:
            if n % 2 == 0:
                n //= 2
            else:
                n = 3 * n + 1
            print(n)
            length += 1
        return length
    """),
    ("counts transitions, not printed values", """
    def hailstone(n):
        count = 0
        print(n)
        while n > 1:
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            print(n)
            count += 1
        return count
    """),
    ("omits final printed 1 and counts transitions", """
    def hailstone(n):
        steps = 0
        while n != 1:
            print(n)
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            steps += 1
        return steps
    """),
    ("uses true division; numeric sequence OK but prints floats", """
    def hailstone(n):
        length = 1
        print(n)
        while n != 1:
            if n % 2 == 0:
                n = n / 2
            else:
                n = 3 * n + 1
            print(n)
            length += 1
        return length
    """),
    ("correct recursive sequence", """
    def hailstone(n):
        print(n)
        if n == 1:
            return 1
        following = n // 2 if n % 2 == 0 else 3 * n + 1
        return 1 + hailstone(following)
    """),
    ("correct count but prints whole list on one line", """
    def hailstone(n):
        values = [n]
        while n != 1:
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            values.append(n)
        print(values)
        return len(values)
    """),
    ("missing return", """
    def hailstone(n):
        count = 1
        while n != 1:
            print(n)
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            count += 1
        print(1)
    """),
    ("correct list-based implementation", """
    def hailstone(n):
        sequence = [n]
        while sequence[-1] != 1:
            value = sequence[-1]
            sequence.append(value // 2 if value % 2 == 0 else 3 * value + 1)
        for value in sequence:
            print(value)
        return len(sequence)
    """),
    ("computes sequence length without printing", """
    def hailstone(n):
        count = 1
        while n > 1:
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            count += 1
        return count
    """),
    ("special case n=1 returns zero", """
    def hailstone(n):
        if n == 1:
            print(1)
            return 0
        count = 1
        print(n)
        while n > 1:
            n = n // 2 if n % 2 == 0 else 3 * n + 1
            print(n)
            count += 1
        return count
    """),
]

PRODUCTS = [
    ("correct while loop", """
    def product(n, term):
        result = 1
        k = 1
        while k <= n:
            result *= term(k)
            k += 1
        return result
    """),
    ("exclusive endpoint", """
    def product(n, term):
        result = 1
        for k in range(1, n):
            result *= term(k)
        return result
    """),
    ("zero initial product", """
    def product(n, term):
        result = 0
        for k in range(1, n + 1):
            result *= term(k)
        return result
    """),
    ("ignores term callback", """
    def product(n, term):
        result = 1
        for k in range(1, n + 1):
            result *= k
        return result
    """),
    ("correct recursive product", """
    def product(n, term):
        if n == 0:
            return 1
        return product(n - 1, term) * term(n)
    """),
    ("zero-based range", """
    def product(n, term):
        total = 1
        for k in range(n):
            total *= term(k)
        return total
    """),
    ("adds instead of multiplying", """
    def product(n, term):
        result = 1
        for k in range(1, n + 1):
            result += term(k)
        return result
    """),
    ("correct for loop", """
    def product(n, term):
        answer = 1
        for index in range(1, n + 1):
            answer = answer * term(index)
        return answer
    """),
    ("returns inside loop", """
    def product(n, term):
        answer = 1
        for index in range(1, n + 1):
            answer *= term(index)
            return answer
    """),
    ("correct reduction", """
    def product(n, term):
        from functools import reduce
        return reduce(mul, (term(k) for k in range(1, n + 1)), 1)
    """),
]

ACCUMULATORS = [
    ("correct left fold", """
    def accumulate(merger, base, n, term):
        result = base
        for k in range(1, n + 1):
            result = merger(result, term(k))
        return result
    """),
    ("drops base", """
    def accumulate(merger, base, n, term):
        result = 0
        for k in range(1, n + 1):
            result = merger(result, term(k))
        return result
    """),
    ("exclusive endpoint", """
    def accumulate(merger, base, n, term):
        result = base
        for k in range(1, n):
            result = merger(result, term(k))
        return result
    """),
    ("hardcodes addition instead of merger", """
    def accumulate(merger, base, n, term):
        result = base
        for k in range(1, n + 1):
            result += term(k)
        return result
    """),
    ("correct recursion preserving ascending fold", """
    def accumulate(merger, base, n, term):
        if n == 0:
            return base
        return merger(accumulate(merger, base, n - 1, term), term(n))
    """),
    ("ignores term", """
    def accumulate(merger, base, n, term):
        result = base
        for k in range(1, n + 1):
            result = merger(result, k)
        return result
    """),
    ("resets to base on each iteration", """
    def accumulate(merger, base, n, term):
        result = base
        for k in range(1, n + 1):
            result = merger(base, term(k))
        return result
    """),
    ("correct while loop", """
    def accumulate(merger, base, n, term):
        k = 1
        while k <= n:
            base = merger(base, term(k))
            k += 1
        return base
    """),
    ("returns None for n=0", """
    def accumulate(merger, base, n, term):
        if n == 0:
            return
        value = base
        for k in range(1, n + 1):
            value = merger(value, term(k))
        return value
    """),
    ("reverse traversal; differs for commutative non-associative mergers", """
    def accumulate(merger, base, n, term):
        value = base
        for k in range(n, 0, -1):
            value = merger(value, term(k))
        return value
    """),
]

WRAPPERS = [
    ("correct one-line wrappers", """
    def summation_using_accumulate(n, term):
        return accumulate(add, 0, n, term)

    def product_using_accumulate(n, term):
        return accumulate(mul, 1, n, term)
    """),
    ("product wrapper starts at zero", """
    def summation_using_accumulate(n, term):
        return accumulate(add, 0, n, term)

    def product_using_accumulate(n, term):
        return accumulate(mul, 0, n, term)
    """),
    ("wrappers bypass required accumulate abstraction", """
    def summation_using_accumulate(n, term):
        return sum(term(k) for k in range(1, n + 1))

    def product_using_accumulate(n, term):
        return product(n, term)
    """),
    ("correct values but two-statement wrappers", """
    def summation_using_accumulate(n, term):
        result = accumulate(add, 0, n, term)
        return result

    def product_using_accumulate(n, term):
        result = accumulate(mul, 1, n, term)
        return result
    """),
]

# Explicit combinations keep recurring defects and counterexamples across graders.
CONTROL = [(0,0),(1,0),(2,1),(7,1),(0,3),(1,3),(2,3),(10,0),(3,0),(3,4),
           (4,0),(5,0),(6,0),(8,0),(9,0),(11,0),(0,2),(1,5),(2,6),(7,8),
           (10,9),(0,4),(1,7),(5,1),(3,3),(7,4),(10,7),(6,2),(8,6),(0,0)]
HIGHER = [(0,0,0),(7,0,0),(1,0,0),(1,7,0),(2,0,0),(2,7,0),(3,0,0),(4,4,0),
          (0,1,0),(7,1,0),(0,2,0),(0,3,0),(0,5,0),(0,6,0),(0,8,0),(0,9,0),
          (5,0,0),(6,0,0),(8,0,0),(9,0,0),(0,0,1),(0,0,2),(7,7,3),(1,2,0),
          (3,5,0),(4,7,0),(9,4,0),(2,1,1),(8,6,2),(0,0,0)]


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    manifest = []
    key = []
    worksheets = []
    assignments = []
    for i, ((factor, hail), (product, accumulate, wrapper)) in enumerate(zip(CONTROL, HIGHER), 1):
        student = f"T{i:03}"
        # No author names, grades or intended defects embedded in submitted source.
        sources = {
            "CONTROL": clean(FACTORS[factor][1]) + "\n" + clean(HAIL[hail][1]),
            "HIGHER": "from operator import add, mul\n\n" + clean(PRODUCTS[product][1]) + "\n" + clean(ACCUMULATORS[accumulate][1]) + "\n" + clean(WRAPPERS[wrapper][1]),
        }
        if i == 30:
            # A deliberately broken parse, confined to one file in the cohort.
            sources["CONTROL"] = sources["CONTROL"].replace("def hailstone(n):", "def hailstone(n)")
            sources["HIGHER"] = sources["HIGHER"].replace("    return result\n", "    return answer\n", 1)
        for task, source in sources.items():
            path = ROOT / "submissions" / task / f"{student}_{task.lower()}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8", newline="\n")
            manifest.append({"student_id": student, "assessment": task,
                             "path": path.relative_to(ROOT).as_posix(),
                             "sha256": hashlib.sha256(source.encode()).hexdigest(),
                             "synthetic": True, "expected_parse": "error" if i == 30 and task == "CONTROL" else "ok"})
            criteria = ["C1", "C2", "C3", "C4", "C5"] if task == "CONTROL" else ["H1", "H2", "H3", "H4", "H5"]
            for criterion in criteria:
                for review in ("primary", "secondary"):
                    worksheets.append({"student_id": student, "assessment": task, "review": review,
                                       "criterion": criterion, "grader": "", "points": "", "source_lines": "",
                                       "reason": "", "question_for_head_ta": ""})
        key.append({"student_id": student, "control": [FACTORS[factor][0], HAIL[hail][0]],
                    "higher": [PRODUCTS[product][0], ACCUMULATORS[accumulate][0], WRAPPERS[wrapper][0]],
                    "extra": "CONTROL missing colon; HIGHER product returns undefined answer" if i == 30 else ""})
        assignments.append({"student_id": student, "primary_grader_slot": ["A","B","C"][(i-1)%3],
                            "secondary_grader_slot": ["B","C","A"][(i-1)%3]})
    write_csv(ROOT / "grading" / "scores_blank.csv", ["student_id", "grader", "CONTROL", "HIGHER"],
              [{"student_id": f"T{i:03}", "grader": "", "CONTROL": "", "HIGHER": ""} for i in range(1,31)])
    write_csv(ROOT / "grading" / "criterion_reviews_blank.csv", list(worksheets[0]), worksheets)
    write_csv(ROOT / "grading" / "review_assignments.csv", list(assignments[0]), assignments)
    (ROOT / "director_only").mkdir(exist_ok=True)
    (ROOT / "director_only" / "scenario_key.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    (ROOT / "manifest.json").write_text(json.dumps({"dataset_key": "synthetic-college30-v1", "students": 30,
        "assessments": ["CONTROL", "HIGHER"], "files": manifest}, indent=2) + "\n", encoding="utf-8")
    print("Created 30 synthetic students, 60 submissions, 600 blank criterion-review rows; no grades assigned.")


if __name__ == "__main__":
    main()
