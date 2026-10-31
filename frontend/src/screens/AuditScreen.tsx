import { useState } from "react";
import { api, qs, type Assessment, type AuditEvent } from "../api";
import { ErrorBox, KeyValues, Loading, Notice, errorText, fmtTime, useLoad } from "../components/common";

interface ExportResult {
  csv_path: string;
  json_path: string;
  csv_text: string;
  rows: number;
}

export default function AuditScreen({ assessment }: { assessment: Assessment }) {
  const events = useLoad(() => api.get<AuditEvent[]>(`/audit${qs({ limit: 300 })}`), []);
  const [result, setResult] = useState<ExportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function exportReport() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.post<ExportResult>("/exports/moderation-report", { assessment_id: assessment.id }));
      events.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function download() {
    if (!result) return;
    const blob = new Blob([result.csv_text], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = result.csv_path.split(/[\\/]/).pop() ?? "moderation-report.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  return (
    <section>
      <h2>Audit & export</h2>
      <div className="card">
        <h3>Moderation report</h3>
        <p className="muted">
          Copies original scores from the immutable import records. Proposed scores appear only where a human entered
          them in a decision. The original CSV and submission files are never modified. Formula-leading cells are
          escaped.
        </p>
        {assessment.is_synthetic ? <Notice tone="warn">This report describes SYNTHETIC demo data.</Notice> : null}
        <button type="button" className="primary" onClick={exportReport} disabled={busy}>
          Export moderation report
        </button>
        <ErrorBox error={error} />
        {result ? (
          <>
            <KeyValues
              rows={[
                ["Rows", String(result.rows)],
                ["CSV saved (private folder)", <code key="c">{result.csv_path}</code>],
                ["JSON detail saved", <code key="j">{result.json_path}</code>],
              ]}
            />
            <button type="button" onClick={download}>
              Download CSV
            </button>
          </>
        ) : null}
      </div>
      <h3>
        Audit history <Loading show={events.loading} />
      </h3>
      <ErrorBox error={events.error} />
      <div className="table-wrap tall">
        <table className="table compact">
          <thead>
            <tr>
              <th>#</th>
              <th>When</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Entity</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            {(events.data ?? []).map((e) => (
              <tr key={e.id}>
                <td>{e.id}</td>
                <td className="small">{fmtTime(e.at)}</td>
                <td>{e.actor}</td>
                <td>{e.action}</td>
                <td className="small">
                  {e.entity_type} {e.entity_id ?? ""}
                </td>
                <td className="small mono-wrap">{JSON.stringify(e.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">The local prototype actor is not institutional authentication.</p>
    </section>
  );
}
