"""Verify manifest, structure and reference answers; never execute submissions."""
import ast
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
from operator import add, mul
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def reference(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "reference_answers" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["students"] == 30 and len(manifest["files"]) == 60
    expected_ids = {f"T{i:03}" for i in range(1, 31)}
    assert {item["student_id"] for item in manifest["files"]} == expected_ids
    paths = {item["path"] for item in manifest["files"]}
    assert len(paths) == 60
    assert paths == {p.relative_to(ROOT).as_posix() for p in (ROOT / "submissions").rglob("*.py")}
    failures = []
    for item in manifest["files"]:
        path = ROOT / item["path"]
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"], path
        try:
            tree = ast.parse(data.decode("utf-8"))
        except SyntaxError:
            assert item["expected_parse"] == "error", path
            failures.append(item["path"])
        else:
            assert item["expected_parse"] == "ok", path
            required = {"largest_factor", "hailstone"} if item["assessment"] == "CONTROL" else {
                "product", "accumulate", "summation_using_accumulate", "product_using_accumulate"}
            assert required <= {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}, path
            assert all(isinstance(n, (ast.FunctionDef, ast.ImportFrom)) for n in tree.body), path
    assert failures == ["submissions/CONTROL/T030_control.py"]
    with (ROOT / "grading" / "scores_blank.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 30 and {row["student_id"] for row in rows} == expected_ids
    assert all(not row[k] for row in rows for k in ("grader", "CONTROL", "HIGHER"))
    with (ROOT / "grading" / "criterion_reviews_blank.csv").open(newline="", encoding="utf-8") as f:
        reviews = list(csv.DictReader(f))
    assert len(reviews) == 600 and all(not r["points"] for r in reviews)

    c = reference("control")
    h = reference("higher")
    count = 0
    for n in range(2, 1001):
        expected = max(d for d in range(1, n) if n % d == 0)
        assert c.largest_factor(n) == expected
        count += 1
    for start in range(1, 101):
        values = [start]
        while values[-1] != 1:
            value = values[-1]
            values.append(value // 2 if value % 2 == 0 else value * 3 + 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            length = c.hailstone(start)
        assert out.getvalue() == "".join(f"{v}\n" for v in values)
        assert length == len(values)
        count += 1
    for n in range(21):
        for term in (lambda k: k, lambda k: k*k, lambda k: k+1, lambda k: 3*k):
            values = [term(k) for k in range(1, n+1)]
            expected_product = 1
            for value in values:
                expected_product *= value
            assert h.product(n, term) == expected_product
            assert h.product_using_accumulate(n, term) == expected_product
            assert h.summation_using_accumulate(n, term) == sum(values)
            for merger, base in ((add, 11), (mul, 2), (lambda a,b: (a+b)/2, 0), (lambda a,b: a+b+1, 2)):
                expected = base
                for value in values:
                    expected = merger(expected, value)
                assert h.accumulate(merger, base, n, term) == expected
                count += 1
            count += 3
    print(json.dumps({"students": 30, "submission_files": 60, "parse_ok": 59,
                      "intentional_parse_errors": failures, "blank_review_rows": 600,
                      "reference_checks_passed": count,
                      "student_programs_executed": 0}, indent=2))


if __name__ == "__main__":
    main()
