"""Preview this fixture or explicitly import into a chosen demo database.

Use the backend venv. No database or model is touched without --commit.
"""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
sys.path.insert(0, str(REPO / "backend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, default=ROOT / "grading" / "scores_blank.csv")
    parser.add_argument("--assessment", choices=["CONTROL", "HIGHER", "both"], default="both")
    parser.add_argument("--database", type=Path, help="Explicit SQLite path; use a separate demo database")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if args.commit and not args.database:
        parser.error("--commit requires --database; the active/private database is never selected implicitly")

    from app.importers.service import build_preview, commit_import
    tasks = ["CONTROL", "HIGHER"] if args.assessment == "both" else [args.assessment]
    previews = []
    for task in tasks:
        preview = build_preview(args.scores.resolve(), ROOT / "submissions" / task,
                                r"^(?P<student_id>T[0-9]{3})(?:_.*)?\.py$",
                                "synthetic-college30-v1", task)
        expected = {f"T{i:03}" for i in range(1, 31)}
        if {row.student_code for row in preview.rows} != expected or len(preview.rows) != 30:
            raise ValueError("Expected exactly T001–T030, once each")
        if set(preview.files_by_student) != expected:
            raise ValueError("Every synthetic student must have a submission")
        for row in preview.rows:
            raw = row.cells[task].strip()
            if raw and not 0 <= float(raw) <= 10:
                raise ValueError(f"Invalid {task} total for {row.student_code}")
        previews.append(preview)
        print(json.dumps({"assessment": task, "students": len(preview.rows),
                          "files": sum(map(len, preview.files_by_student.values())),
                          "parse_failures": preview.report["quality"]["parse_failures"],
                          "mode": "commit" if args.commit else "preview"}))
    if not args.commit:
        return

    from app.db.session import init_engine, session_scope
    engine = init_engine("sqlite:///" + args.database.resolve().as_posix())
    try:
        with session_scope() as session:
            for preview in previews:
                task = preview.assessment_name
                instructions = (ROOT / "assignments" / f"{task}.md").read_text(encoding="utf-8")
                rubric = (ROOT / "rubrics" / f"{task}.md").read_text(encoding="utf-8")
                reference = (ROOT / "reference_answers" / f"{task.lower()}.py").read_text(encoding="utf-8")
                _, assessment, created = commit_import(session, preview, "synthetic-college30-loader",
                    is_synthetic=True, context={
                        "instructions_status": "synthetic_demo",
                        "instructions_text": instructions + "\n\n" + rubric,
                        "reference_solution_text": reference,
                        "reference_solution_status": "example_unconfirmed",
                        "max_points_confirmed": 10.0,
                        "multi_file_scope_note": "One file per student contains all required functions. Synthetic demonstration data.",
                    })
                print(json.dumps({"assessment": task, "assessment_id": assessment.id,
                                  "created": created, "synthetic": assessment.is_synthetic}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
