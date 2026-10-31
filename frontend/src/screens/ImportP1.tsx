import { useState } from "react";
import { api, type Assessment, type Json } from "../api";
import { Badge, ErrorBox, Notice, errorText, useLoad } from "../components/common";
import { EVIDENCE_STATES, setFlash, takeFlash, type BatchPreviewRow, type FlagView } from "../p1api";

interface DemoResult {
  rubric_version_id: number;
  assessments: Record<string, { assessment_id: number; created: boolean }>;
  flags: Json;
}

// P1 additions to the Import screen: synthetic P1 demo, six-assignment batch
// import, evidence manifest and unverified external flags.
export default function ImportP1({ assessment, onImported }: { assessment: Assessment | null; onImported: (id: number) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(() => takeFlash());
  const [batch, setBatch] = useState<{ assignments: BatchPreviewRow[]; errors: Record<string, string>; note: string } | null>(null);

  const announce = (text: string) => {
    setMessage(text);
    setFlash(text);
  };

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await fn();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="p1-import">
      <div className="demo-box">
        <button
          type="button"
          disabled={busy}
          data-testid="load-p1-demo"
          onClick={() =>
            run(async () => {
              const r = await api.post<DemoResult>("/demo/p1");
              announce(
                `SYNTHETIC P1 demo ready: SA3 (assessment ${r.assessments.SA3.assessment_id}), SA6 (assessment ${r.assessments.SA6.assessment_id}), synthetic guide (rubric ${r.rubric_version_id}).`,
              );
              onImported(r.assessments.SA3.assessment_id);
            })
          }
        >
          Load SYNTHETIC P1 demo (SA3/SA6)
        </button>
        <span className="muted"> Invented students P001–P012, a synthetic grading guide and synthetic flags.</span>
      </div>

      <div className="card">
        <h3>Six-assignment import (SA1–SA6)</h3>
        <p className="muted small">
          Previews every assignment folder together; each assignment is committed separately with its own manifest,
          score column and context. Only code files are in this export — screenshots and outputs are not, which is not
          evidence that students failed to submit them.
        </p>
        <div className="row gap">
          <button type="button" disabled={busy} onClick={() => run(async () => setBatch(await api.post("/imports/batch-preview", {})))} data-testid="batch-preview">
            Preview all assignments
          </button>
          <button
            type="button"
            className="primary"
            disabled={busy || !batch}
            onClick={() =>
              run(async () => {
                const r = await api.post<{ results: Record<string, { assessment_id?: number; created?: boolean; error?: string }> }>("/imports/batch-commit", {});
                const parts = Object.entries(r.results).map(([k, v]) => `${k}: ${v.error ? `error ${v.error}` : v.created ? "imported" : "unchanged"}`);
                announce(parts.join(" · "));
                const first = Object.values(r.results).find((v) => v.assessment_id);
                if (first?.assessment_id) onImported(first.assessment_id);
              })
            }
          >
            Commit all
          </button>
        </div>
        {batch ? (
          <div className="table-wrap">
            <table className="table compact" data-testid="batch-table">
              <thead>
                <tr>
                  <th>Assignment</th>
                  <th>Files</th>
                  <th>Bundles</th>
                  <th>Multi-file</th>
                  <th>Bundles without score</th>
                  <th>Parse failures</th>
                  <th>Students without files</th>
                  <th>Errors</th>
                </tr>
              </thead>
              <tbody>
                {batch.assignments.map((a) => (
                  <tr key={a.assignment}>
                    <td>{a.assignment}</td>
                    <td>{a.files}</td>
                    <td>{a.student_bundles}</td>
                    <td>{a.multi_file_bundles}</td>
                    <td>{a.bundles_without_score}</td>
                    <td>{a.parse_failures}</td>
                    <td>{a.students_without_files}</td>
                    <td>{a.issues.length || "—"}</td>
                  </tr>
                ))}
                {Object.entries(batch.errors).map(([k, v]) => (
                  <tr key={k} className="dim">
                    <td>{k}</td>
                    <td colSpan={7} className="bad">
                      {v}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="note">{batch.note}</p>
          </div>
        ) : null}
      </div>

      <ErrorBox error={error} />
      {message ? <Notice tone="ok">{message}</Notice> : null}
      {assessment ? <ManifestView key={assessment.id} assessment={assessment} /> : null}
      <FlagsView />
    </div>
  );
}

function ManifestView({ assessment }: { assessment: Assessment }) {
  const manifest = useLoad(
    () => api.get<{ version: number; summary: Json; manifest: Json & { submissions: Record<string, Json> } }>(`/assessments/${assessment.id}/manifest`),
    [assessment.id],
  );
  return (
    <details className="card" data-testid="manifest">
      <summary>
        <strong>
          Evidence manifest · {assessment.name} {manifest.data ? `v${manifest.data.version}` : ""}
        </strong>
      </summary>
      <ul className="plain-list small">
        {EVIDENCE_STATES.map(([state, text]) => (
          <li key={state}>
            <Badge>{state}</Badge> {text}
          </li>
        ))}
      </ul>
      {manifest.error ? <p className="muted">{manifest.error}</p> : null}
      {manifest.data ? (
        <div className="table-wrap tall">
          <table className="table compact" data-testid="manifest-table">
            <thead>
              <tr>
                <th>Student</th>
                <th>Code</th>
                <th>Files</th>
                <th>Parse</th>
                <th>Screenshot</th>
                <th>Output</th>
                <th>Recorded score</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(manifest.data.manifest.submissions).map(([code, row]) => (
                <tr key={code} data-testid={`manifest-row-${code}`}>
                  <td>{code}</td>
                  <td>
                    <Badge tone={row.code === "available" ? "ok" : "warn"}>{String(row.code)}</Badge>
                  </td>
                  <td>{String(row.files ?? 0)}</td>
                  <td>{String(row.parse ?? "—")}</td>
                  <td className="small">{String(row.screenshot ?? "—")}</td>
                  <td className="small">{String(row.output ?? "—")}</td>
                  <td className="small">{String(row.score_status ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </details>
  );
}

function FlagsView() {
  const flags = useLoad(() => api.get<FlagView[]>("/flags"), []);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  return (
    <div className="card" data-testid="flags">
      <h3>
        External flags <Badge tone="warn">UNVERIFIED imported leads</Badge>
      </h3>
      <p className="muted small">
        Imported rows are leads, never guidance, labels or ground truth. Sides link to submissions only by exact source
        text; score/grader agreement is a metadata check, not identity. Assignment hints from notes stay unconfirmed.
      </p>
      <button
        type="button"
        onClick={async () => {
          setError(null);
          try {
            const r = await api.post<Json>("/flags/import", {});
            setMessage(`Imported ${String(r.rows)} rows: ${JSON.stringify(r.link_summary)}`);
            flags.reload();
          } catch (err) {
            setError(errorText(err));
          }
        }}
      >
        Import flags (private default path)
      </button>
      <ErrorBox error={error || flags.error} />
      {message ? <Notice tone="ok">{message}</Notice> : null}
      <div className="table-wrap">
        <table className="table compact" data-testid="flags-table">
          <thead>
            <tr>
              <th>Row</th>
              <th>Trust</th>
              <th>Similarity</th>
              <th>Hint</th>
              <th>Side A</th>
              <th>Side B</th>
              <th>Checks</th>
              <th>Note (imported)</th>
            </tr>
          </thead>
          <tbody>
            {(flags.data ?? []).map((f) => (
              <tr key={f.id}>
                <td>
                  {f.row_number} <span className="muted">(id {f.original_flag_id})</span>
                </td>
                <td>
                  <Badge tone="warn">{f.trust}</Badge>
                </td>
                <td>{f.similarity === null ? "—" : f.similarity}</td>
                <td className="small">{f.assignment_hint ? `${f.assignment_hint} (unconfirmed)` : "—"}</td>
                {["a", "b"].map((side) => {
                  const link = f.links.find((l) => l.side === side);
                  return (
                    <td key={side} className="small">
                      {link ? (
                        <>
                          <Badge tone={link.status.startsWith("exact") ? "ok" : link.status === "ambiguous" ? "warn" : "neutral"}>{link.status}</Badge>
                          {link.candidates.map((c, i) => (
                            <div key={i}>
                              {String(c.assignment)} {String(c.student_id)}
                            </div>
                          ))}
                          {link.metadata_check && "status" in link.metadata_check ? (
                            <div className="muted">metadata: {String(link.metadata_check.status)}</div>
                          ) : null}
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  );
                })}
                <td className="small">
                  {f.checks.map((c, i) => (
                    <div key={i}>
                      <Badge tone="warn">{c.kind}</Badge> {c.detail}
                    </div>
                  ))}
                </td>
                <td className="small">{f.rubric_note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {flags.data && flags.data.length === 0 ? <p className="muted">No flags imported.</p> : null}
    </div>
  );
}
