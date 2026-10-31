"""Seed invented grader drafts through the existing API; never approve guidance.

Used by start-calibration-demo.ps1 against a fresh, isolated demo database.
Student source files are not executed. All model stages use synthetic mocks.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from urllib.parse import urlparse


def prepare(base: str) -> dict:
    parsed = urlparse(base)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.path not in ("", "/"):
        raise ValueError("Use an isolated GradeLens server at http://127.0.0.1:PORT")

    def call(path: str, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base.rstrip("/") + "/api" + path, data=data,
            headers={"Content-Type": "application/json", "X-GradeLens-Client": "1"})
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)

    if call("/assessments"):
        raise RuntimeError("Preparation requires an empty demo database. Existing data was not changed.")
    fixture = call("/demo/p1", {})
    aid = fixture["assessments"]["SA6"]["assessment_id"]
    queue = call(f"/assessments/{aid}/grading-queue")
    ids = {row["student_id"]: row["submission_id"] for row in queue}
    ws = call(f"/submissions/{ids['P002']}/workspace")
    criterion = next(c["criterion_key"] for c in ws["criteria"] if "correctness" in c["criterion_key"] and c["max_points"] == "6")
    for student, points, total, grader in (("P002", "2", "6", "A"), ("P004", "5", "9", "B"), ("P007", "1", "5", "C")):
        call(f"/submissions/{ids[student]}/draft-grades", {
            "total": total, "criterion_points": {criterion: points}, "expected_revision_id": None,
            "grader_label": f"Synthetic Grader {grader}", "note": "Invented grader decision for calibration walkthrough."})
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            ws = call(f"/submissions/{ids[student]}/workspace")
            feedback = ws.get("feedback") or {}
            if feedback.get("status") in ("completed", "partial") and feedback.get("is_current"):
                if not any(c["type"] == "calibration_question" and "no diagonal" in c["title"] for c in feedback["all_cards"]):
                    raise RuntimeError(f"Expected synthetic feedback missing for {student}")
                break
            time.sleep(0.25)
        else:
            raise RuntimeError(f"Feedback did not finish for {student}; inspect the isolated server log.")
    if call(f"/calibration-issues?assessment_id={aid}"):
        raise RuntimeError("Unexpected policy question during preparation; inspect the isolated database.")
    return {"assessment_id": aid, "submission_id": ids["P007"],
            "url": base.rstrip("/") + f"/?demo=calibration#/grading?submission={ids['P007']}",
            "status": "Three synthetic drafts ready. No calibration question requested and no policy approved."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.base_url), ensure_ascii=False, indent=2))
