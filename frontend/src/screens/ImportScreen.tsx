import { useEffect, useState } from "react";
import { api, type Assessment, type Json, type PublicSettings } from "../api";
import ImportP1 from "./ImportP1";
import { Badge, ErrorBox, KeyValues, List, Notice, errorText } from "../components/common";

interface PreviewStudent {
  student_id: string;
  grader: string | null;
  score_raw: string;
  score: number | null;
  score_status: string;
  files: string[];
  distinct_contents: number;
}

interface PreviewIssue {
  kind: string;
  severity: string;
  message: string;
  path?: string;
  student_id?: string;
  row?: number;
  extracted_id?: string;
}

interface Report {
  csv: Json & { columns: string[]; duplicate_ids: string[] };
  assessment: Json & { score_value_counts: Record<string, number>; observed_max_note: string };
  files: Json;
  matching: Json & {
    files_per_student: Record<string, number>;
    multi_file_students: string[];
    students_without_files: string[];
    students_without_files_note: string;
    matched_without_score: string[];
    within_bundle_identical_files: { student_id: string; copies: number; paths: string[] }[];
    cross_student_identical_files: { content_sha256: string; student_ids: string[] }[];
    cross_student_note: string;
  };
  quality: {
    parse_failures: { student_id: string; path: string; line: number | null }[];
    possible_anonymization_artifacts: { student_id: string; path: string; line: number | null }[];
    possible_identifying_text: { student_id: string; path: string; line: number | null }[];
  };
  assumptions: string[];
}

interface Preview {
  report: Report;
  issues: PreviewIssue[];
  students: PreviewStudent[];
}

const str = (v: unknown) => (v === null || v === undefined ? "—" : String(v));

export default function ImportScreen({
  settings, onImported, assessment = null,
}: { settings: PublicSettings | null; onImported: (id: number) => void; assessment?: Assessment | null }) {
  const [form, setForm] = useState({ csv_path: "", source_root: "", filename_regex: "", dataset_key: "", assessment_name: "" });
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (settings) {
      setForm({
        csv_path: settings.csv_path,
        source_root: settings.sa1_path,
        filename_regex: settings.filename_id_regex,
        dataset_key: settings.dataset_key,
        assessment_name: settings.assessment_name,
      });
    }
  }, [settings]);

  const update = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm({ ...form, [key]: e.target.value });
    setPreview(null); // A preview of different inputs cannot authorize this commit.
  };

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await action();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const doPreview = () => run(async () => setPreview(await api.post<Preview>("/imports/preview", form)));
  const doCommit = () =>
    run(async () => {
      const result = await api.post<{ assessment_id: number; created: boolean; message: string }>("/imports/commit", form);
      setMessage(`${result.message} (assessment id ${result.assessment_id})`);
      onImported(result.assessment_id);
    });
  const doDemo = () =>
    run(async () => {
      const result = await api.post<{ assessment_id: number; created: boolean }>("/imports/demo");
      setMessage(
        result.created
          ? `SYNTHETIC demo dataset imported (assessment id ${result.assessment_id}).`
          : `SYNTHETIC demo dataset was already imported (assessment id ${result.assessment_id}); nothing changed.`,
      );
      onImported(result.assessment_id);
    });

  return (
    <section>
      <h2>Import preview</h2>
      <p className="muted">
        Matches files to the gradebook by the exact student ID at the start of the file name. Text after the ID (such
        as <code>_logo</code> or <code>_mystery</code>) is ignored for identity and comparison; original paths are kept
        privately. Nothing is executed. Preview first; commit is idempotent.
      </p>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          doPreview();
        }}
      >
        <label>
          Gradebook CSV
          <input value={form.csv_path} onChange={update("csv_path")} />
        </label>
        <label>
          Submission folder
          <input value={form.source_root} onChange={update("source_root")} />
        </label>
        <label>
          File name ID regex
          <input value={form.filename_regex} onChange={update("filename_regex")} className="mono" />
        </label>
        <label>
          Dataset key
          <input value={form.dataset_key} onChange={update("dataset_key")} />
        </label>
        <label>
          Assessment column
          <input value={form.assessment_name} onChange={update("assessment_name")} />
        </label>
        <div className="row gap">
          <button type="submit" disabled={busy}>
            Preview
          </button>
          <button type="button" disabled={busy || !preview} onClick={doCommit} className="primary">
            Commit import
          </button>
        </div>
      </form>
      <div className="demo-box">
        <button type="button" disabled={busy} onClick={doDemo}>
          Load SYNTHETIC demo dataset
        </button>
        <span className="muted"> Invented students D001–D009 and scripted mock responses, kept separate from real data.</span>
      </div>
      <ErrorBox error={error} />
      {message ? <Notice tone="ok">{message}</Notice> : null}
      {preview ? <PreviewReport preview={preview} /> : null}
      <h2>P1: multi-assignment import, evidence and leads</h2>
      <ImportP1 assessment={assessment} onImported={onImported} />
    </section>
  );
}

function PreviewReport({ preview }: { preview: Preview }) {
  const { report, issues, students } = preview;
  const q = report.quality;
  return (
    <div className="report">
      <h3>Data-quality report</h3>
      <div className="cards">
        <div className="card">
          <h4>Gradebook</h4>
          <KeyValues
            rows={[
              ["SHA-256", <code key="s">{str(report.csv.sha256)}</code>],
              ["Columns", report.csv.columns.join(", ")],
              ["Rows / unique IDs", `${str(report.csv.rows)} / ${str(report.csv.unique_student_ids)}`],
              ["Duplicate IDs", report.csv.duplicate_ids.length ? report.csv.duplicate_ids.join(", ") : "none"],
              ["Grader labels (non-empty)", str(report.csv.nonempty_grader_labels)],
              ["Rows with blank grader", str(report.csv.rows_with_blank_grader)],
              ["Scores populated / blank (all columns)", `${str(report.csv.scores_populated_all_assessments)} / ${str(report.csv.scores_blank_all_assessments)}`],
              ["Unparseable score cells", str(report.csv.scores_unparseable_all_assessments)],
            ]}
          />
        </div>
        <div className="card">
          <h4>Assessment {str(report.assessment.name)}</h4>
          <KeyValues
            rows={[
              ["Scores populated / blank", `${str(report.assessment.scores_populated)} / ${str(report.assessment.scores_blank)}`],
              [
                "Value counts",
                Object.entries(report.assessment.score_value_counts)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(" · ") || "—",
              ],
              ["Observed max", str(report.assessment.observed_max)],
            ]}
          />
          <p className="note">{report.assessment.observed_max_note}</p>
        </div>
        <div className="card">
          <h4>Files</h4>
          <KeyValues
            rows={[
              ["Matched files", str(report.files.matched_files)],
              ["Total bytes", str(report.files.total_bytes)],
              ["Size range", `${str(report.files.size_min)}–${str(report.files.size_max)} bytes`],
              ["Rejected / unknown ID / ambiguous", `${str(report.files.rejected_files)} / ${str(report.files.unknown_id_files)} / ${str(report.files.ambiguous_id_files)}`],
              ["Regex", <code key="r">{str(report.files.filename_regex)}</code>],
            ]}
          />
        </div>
        <div className="card">
          <h4>Matching</h4>
          <KeyValues
            rows={[
              ["Students with files", str(report.matching.students_with_files)],
              [
                "Files per student",
                Object.entries(report.matching.files_per_student)
                  .map(([k, v]) => `${v} student(s) with ${k}`)
                  .join(" · "),
              ],
              ["Multi-file students", report.matching.multi_file_students.join(", ") || "none"],
              ["Matched but no score", report.matching.matched_without_score.join(", ") || "none"],
            ]}
          />
        </div>
      </div>

      <h4>Students without files</h4>
      <p className="note">Unavailable in this upload — not non-submission or zero credit.</p>
      <p>{report.matching.students_without_files.join(", ") || "none"}</p>

      <h4>Identical files</h4>
      <p className="note">{report.matching.cross_student_note} Not evidence of misconduct.</p>
      <List
        items={[
          ...report.matching.within_bundle_identical_files.map(
            (d) => `Within ${d.student_id}'s bundle: ${d.copies} identical copies (${d.paths.join(", ")}) — analyzed once, aliases kept`,
          ),
          ...report.matching.cross_student_identical_files.map(
            (g) => `Across students ${g.student_ids.join(", ")} (content ${g.content_sha256}…)`,
          ),
        ]}
      />

      <div className="cards">
        <div className="card">
          <h4>Parse failures</h4>
          <List items={q.parse_failures.map((f) => `${f.student_id} · ${f.path}${f.line ? ` · line ${f.line}` : ""}`)} />
        </div>
        <div className="card">
          <h4>Possible anonymization artifacts</h4>
          <p className="note">Placeholders such as [NAME] in code; not demonstrated student mistakes.</p>
          <List items={q.possible_anonymization_artifacts.map((f) => `${f.student_id} · ${f.path}${f.line ? ` · line ${f.line}` : ""}`)} />
        </div>
        <div className="card">
          <h4>Possible identifying text (heuristic)</h4>
          <p className="note">Author-style comments without a placeholder. Check before sharing or cloud use.</p>
          <List items={q.possible_identifying_text.map((f) => `${f.student_id} · ${f.path}${f.line ? ` · line ${f.line}` : ""}`)} />
        </div>
      </div>

      <h4>Assumptions recorded with this import</h4>
      <List items={report.assumptions} />

      <h4>Issues ({issues.length})</h4>
      {issues.length === 0 ? (
        <p className="muted">none</p>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>Severity</th>
              <th>Kind</th>
              <th>Where</th>
              <th>Message</th>
            </tr>
          </thead>
          <tbody>
            {issues.map((issue, i) => (
              <tr key={i}>
                <td>
                  <Badge tone={issue.severity === "error" ? "bad" : "warn"}>{issue.severity}</Badge>
                </td>
                <td>{issue.kind}</td>
                <td>{issue.path ?? issue.student_id ?? (issue.row ? `row ${issue.row}` : "")}</td>
                <td>{issue.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h4>Per-student mapping ({students.length})</h4>
      <div className="table-wrap">
        <table className="table compact">
          <thead>
            <tr>
              <th>Student</th>
              <th>Grader</th>
              <th>Score (raw)</th>
              <th>Files</th>
              <th>Distinct contents</th>
            </tr>
          </thead>
          <tbody>
            {students.map((s) => (
              <tr key={s.student_id} className={s.files.length ? "" : "dim"}>
                <td>{s.student_id}</td>
                <td>{s.grader ?? <span className="muted">(blank)</span>}</td>
                <td>{s.score_status === "scored" ? s.score_raw : <span className="muted">{s.score_status}</span>}</td>
                <td>{s.files.length ? s.files.join(", ") : <span className="muted">unavailable in this upload</span>}</td>
                <td>{s.distinct_contents}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
