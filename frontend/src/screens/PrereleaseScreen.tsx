import { useState } from "react";
import { api, type Assessment, type Json, type Run } from "../api";
import type { Nav } from "../App";
import { Badge, ErrorBox, KeyValues, Loading, Notice, errorText, fmtTime, useLoad } from "../components/common";
import { downloadText, type Prerelease, type Snapshot } from "../p1api";

export default function PrereleaseScreen({ assessment, nav }: { assessment: Assessment; nav: Nav }) {
  const summary = useLoad(() => api.get<Prerelease>(`/assessments/${assessment.id}/prerelease`), [assessment.id]);
  const snapshots = useLoad(() => api.get<Snapshot[]>(`/assessments/${assessment.id}/moderation-snapshots`), [assessment.id]);
  const metrics = useLoad(() => api.get<Json>(`/assessments/${assessment.id}/grading-metrics`), [assessment.id]);
  const [budget, setBudget] = useState("10");
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      summary.reload();
      snapshots.reload();
      metrics.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const s = summary.data;
  return (
    <section>
      <h2>Pre-release review & moderation snapshot</h2>
      <p className="muted">
        Unresolved policy, unavailable evidence, failed checks and unanswered feedback are kept separate; nothing is
        forced into a false "resolved". A snapshot pins the exact draft revisions and guidance versions reviewed. Nothing
        is written back to the gradebook or an LMS.
      </p>
      {assessment.is_synthetic ? <Notice tone="warn">SYNTHETIC demo data.</Notice> : null}
      <ErrorBox error={error || summary.error} />
      <Loading show={summary.loading} />
      {s ? (
        <div className="cards prerelease" data-testid="prerelease">
          <div className="card" data-testid="pr-unresolved-policy">
            <h4>Unresolved policy ({s.unresolved_policy.length})</h4>
            <ul className="plain-list small">
              {s.unresolved_policy.map((i) => (
                <li key={i.id}>
                  <button type="button" className="link" onClick={() => nav.go("issues", { issue: i.id })}>
                    #{i.id}
                  </button>{" "}
                  <Badge tone="warn">{i.state}</Badge> {i.question} ({i.affected} affected)
                </li>
              ))}
            </ul>
            {s.unresolved_policy.length === 0 ? <p className="muted">none</p> : null}
            <h5>Rubric issues blocking totals</h5>
            <ul className="plain-list small">
              {s.rubric_blocking.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          </div>
          <div className="card" data-testid="pr-unavailable-evidence">
            <h4>Unavailable evidence</h4>
            <KeyValues
              rows={[
                ["Criteria needing evidence outside this export", s.unavailable_evidence.external_criteria.join(", ") || "none"],
                ["Manifest", s.unavailable_evidence.manifest_summary ? JSON.stringify(s.unavailable_evidence.manifest_summary) : "—"],
              ]}
            />
          </div>
          <div className="card" data-testid="pr-failed-checks">
            <h4>Failed / partial / stale checks</h4>
            <KeyValues
              rows={[
                ["Feedback", Object.entries(s.failed_checks.feedback).map(([k, v]) => `${k}: ${v}`).join(" · ") || "none"],
                [
                  "Latest pipeline",
                  s.failed_checks.latest_pipeline
                    ? `#${s.failed_checks.latest_pipeline.id} ${s.failed_checks.latest_pipeline.status}`
                    : "none run yet",
                ],
              ]}
            />
          </div>
          <div className="card" data-testid="pr-unanswered">
            <h4>Unanswered feedback ({s.unanswered_feedback.length})</h4>
            <ul className="plain-list small">
              {s.unanswered_feedback.slice(0, 30).map((u) => (
                <li key={u.card_id}>
                  <button type="button" className="link" onClick={() => nav.go("grading", { submission: u.submission_id })}>
                    submission {u.submission_id}
                  </button>{" "}
                  · {u.type}: {u.title}
                </li>
              ))}
            </ul>
          </div>
          <div className="card" data-testid="pr-coverage">
            <h4>Coverage</h4>
            <KeyValues
              rows={[
                ["Submissions / drafted", `${s.submissions} / ${s.drafted} (${s.drafts_from_historical} from imported totals)`],
                ["All pairs", String(s.coverage.all_pairs)],
                ["Retrieved candidates", String(s.coverage.retrieved_candidates)],
                ["Model-reviewed pairs", String(s.coverage.model_reviewed_pairs)],
                ["Unchecked pairs", <strong key="u" data-testid="unchecked-pairs">{s.coverage.unchecked_pairs}</strong>],
                ["Feedback states", Object.entries(s.feedback_states).map(([k, v]) => `${k}: ${v}`).join(" · ")],
              ]}
            />
            <p className="note">{s.coverage.statement}</p>
          </div>
          <div className="card">
            <h4>Approved guidance</h4>
            <ul className="plain-list small">
              {s.approved_guidance.map((g) => (
                <li key={g.note_version_id}>
                  note version {g.note_version_id} (v{g.version}, {g.status}) · <code>{g.criterion_key}</code> ·{" "}
                  {Object.entries(g.applicability).map(([k, v]) => `${k}: ${v}`).join(" · ")}
                </li>
              ))}
            </ul>
            {s.approved_guidance.length === 0 ? <p className="muted">none</p> : null}
          </div>
        </div>
      ) : null}

      <div className="card">
        <h4>Actions</h4>
        <div className="row gap wrap">
          <label className="inline">
            Review budget{" "}
            <input className="narrow" value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="numeric" aria-label="Review budget" />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => act(async () => setRun(await api.post<Run>(`/assessments/${assessment.id}/prerelease-review`, { review_budget: Number(budget) || 0 })))}
            data-testid="run-prerelease"
          >
            Run pre-release review
          </button>
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => act(async () => setSnap(await api.post<Snapshot>(`/assessments/${assessment.id}/moderation-snapshots`)))}
            data-testid="create-snapshot"
          >
            Create moderation snapshot
          </button>
        </div>
        {run ? (
          <Notice tone="info">
            Pre-release review queued as run #{run.id} ({run.status}). Track it on Runs & coverage; refresh this page when it finishes.
          </Notice>
        ) : null}
        {snap ? (
          <div className="snapshot-result" data-testid="snapshot-result">
            <Notice tone="ok">
              <span data-testid="snapshot-version">Snapshot version {snap.version}</span> · {snap.revision_ids?.length ?? 0} draft revisions pinned
              · {snap.unresolved?.length ?? 0} unresolved rows
            </Notice>
            <KeyValues
              rows={[
                ["CSV (private)", <code key="c">{snap.csv_path}</code>],
                ["JSON (private)", <code key="j">{snap.json_path}</code>],
              ]}
            />
            <button
              type="button"
              onClick={() => downloadText(snap.csv_text ?? "", snap.csv_path.split(/[\\/]/).pop() ?? "moderation-snapshot.csv")}
            >
              Download CSV
            </button>
            <h5>Unresolved rows</h5>
            <ul className="plain-list small" data-testid="snapshot-unresolved">
              {(snap.unresolved ?? []).map((u) => (
                <li key={u.student_id}>
                  <strong>{u.student_id}</strong>: {u.reasons.join(" | ")}
                </li>
              ))}
            </ul>
            <details open>
              <summary>Snapshot CSV preview</summary>
              <pre className="json" data-testid="snapshot-csv">{snap.csv_text}</pre>
            </details>
          </div>
        ) : null}
      </div>

      <div className="card">
        <h4>Snapshots</h4>
        <table className="table compact">
          <thead>
            <tr>
              <th>Version</th>
              <th>Created</th>
              <th>Summary</th>
              <th>CSV</th>
            </tr>
          </thead>
          <tbody>
            {(snapshots.data ?? []).map((x) => (
              <tr key={x.id}>
                <td>{x.version}</td>
                <td className="small">{fmtTime(x.created_at)}</td>
                <td className="small">{JSON.stringify(x.summary)}</td>
                <td className="small mono-wrap">{x.csv_path}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" data-testid="metrics">
        <h4>Workload measures (not grading accuracy)</h4>
        {metrics.data ? <pre className="json">{JSON.stringify(metrics.data, null, 2)}</pre> : <Loading show={metrics.loading} />}
      </div>
    </section>
  );
}
