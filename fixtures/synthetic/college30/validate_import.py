"""Integration check in a temporary database; never touches the active database."""
from pathlib import Path
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parents[2] / "backend"))


def main():
    from sqlalchemy import select
    from app.db.models import AssessmentVersion, Student, Submission
    from app.db.session import init_engine, session_scope
    from app.guidance.conditions import evaluate_submission

    with tempfile.TemporaryDirectory(prefix="gradelens-college30-") as temporary:
        database = Path(temporary) / "check.db"
        command = [sys.executable, str(ROOT / "load_into_gradelens.py"), "--database", str(database), "--commit"]
        for expected_created in (True, False):
            result = subprocess.run(command, check=True, capture_output=True, text=True)
            records = [json.loads(line) for line in result.stdout.splitlines()]
            imports = [r for r in records if "created" in r]
            assert len(imports) == 2 and all(r["created"] == expected_created for r in imports)
        with closing(sqlite3.connect(database)) as connection:
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("students", "submissions", "artifacts", "assessment_versions")}
            assert counts == {"students": 30, "submissions": 60, "artifacts": 60, "assessment_versions": 2}
            assert connection.execute("SELECT COUNT(*) FROM score_records WHERE score IS NOT NULL").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM assessment_versions WHERE is_synthetic = 1").fetchone()[0] == 2
        engine = init_engine("sqlite:///" + database.as_posix())
        try:
            with session_scope() as session:
                assessment = session.scalar(select(AssessmentVersion).where(AssessmentVersion.name == "CONTROL"))
                assert "C4" in assessment.instructions_text and assessment.reference_solution_text
                assert assessment.max_points_confirmed == 10
                submissions = list(session.scalars(select(Submission).where(Submission.assessment_id == assessment.id)))
                matched = []
                condition = {"source_regex": r"^\s*n = n / 2\s*$", "present": True}
                for submission in submissions:
                    status, _, _ = evaluate_submission(condition, submission)
                    if status == "applies":
                        matched.append(session.get(Student, submission.student_id).code)
                assert sorted(matched) == ["T005", "T006", "T007", "T025"]
                print(json.dumps({"import_counts": counts, "reimport_idempotent": True,
                                  "populated_scores": 0, "note_condition_scope": len(submissions),
                                  "literal_condition_matches": sorted(matched),
                                  "model_calls": 0, "active_database_modified": False}, indent=2))
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
