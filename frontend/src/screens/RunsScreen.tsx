import { Fragment, useState } from "react";
import type { Nav } from "../App";
import {
  api,
  qs,
  type Assessment,
  type CandidatePair,
  type ProviderStatus,
  type PublicSettings,
  type Run,
  type SubmissionSummary,
} from "../api";
import { Badge, ErrorBox, JsonBlock, Loading, MockBadge, Notice, StatusBadge, errorText, fmtNum, fmtTime, useLoad, usePoll } from "../components/common";

const STAGES = ["analyst", "embed_source", "embed_summary", "retrieval", "review", "applicability"];
const CHANNELS = ["exact", "lexical", "structural", "source_vector", "summary_vector"];

export default function RunsScreen({ assessment, settings, nav }: { assessment: Assessment; settings: PublicSettings | null; nav: Nav }) {
  const runs = useLoad(() => api.get<Run[]>(`/analysis-runs${qs({ assessment_id: assessment.id })}`), [assessment.id]);
  const active = (runs.data ?? []).some((r) => r.status === "queued" || r.status === "running");
  usePoll(active, runs.reload);
  const selectedRun = Number(nav.params.get("run")) || null;

  return (
    <section>
      <h2>Runs & coverage</h2>
      <ProvidersPanel />
      <StartRunForm assessment={assessment} settings={settings} onStarted={runs.reload} />
      <h3>
        Runs <Loading show={runs.loading} />
      </h3>
      <ErrorBox error={runs.error} />
      {(runs.data ?? []).length === 0 ? <p className="muted">No runs yet.</p> : null}
      {(runs.data ?? []).map((run) => (
        <RunCard key={run.id} run={run} onChanged={runs.reload} onCandidates={() => nav.go("runs", { run: run.id })} />
      ))}
      {selectedRun ? <CandidatesView runId={selectedRun} /> : null}
    </section>
  );
}

function ProvidersPanel() {
  const status = useLoad(() => api.get<ProviderStatus>("/providers/status"), []);
  const s = status.data;
  return (
    <div className="card">
      <div className="row space">
        <h3>Model providers</h3>
        <button type="button" onClick={status.reload} disabled={status.loading}>
          Recheck
        </button>
      </div>
      <ErrorBox error={status.error} />
      {s ? (
        <>
          <p>
            {s.local_only ? <Badge tone="ok">local-only: cloud inference disabled</Badge> : <Badge tone="warn">cloud inference ENABLED</Badge>}{" "}
            Ollama:{" "}
            {s.ollama.reachable ? (
              <Badge tone="ok">reachable · v{String(s.ollama.version)}</Badge>
            ) : (
              <Badge tone="bad">not reachable</Badge>
            )}{" "}
            <span className="muted small">{s.ollama.base_url}</span>
          </p>
          {s.ollama.error ? <Notice tone="bad">{s.ollama.error}</Notice> : null}
          <table className="table compact">
            <thead>
              <tr>
                <th>Role</th>
                <th>Provider</th>
                <th>Model</th>
                <th>Ready</th>
                <th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(s.roles).map(([role, r]) => (
                <tr key={role}>
                  <td>{role}</td>
                  <td>{r.provider}</td>
                  <td>
                    <code>{r.model}</code>
                  </td>
                  <td>{r.ready ? <Badge tone="ok">ready</Badge> : <Badge tone="bad">not ready</Badge>}</td>
                  <td>
                    {r.pull_command ? (
                      <span>
                        Model not installed. Run: <code className="copyable">{r.pull_command}</code>{" "}
                        <button type="button" className="link" onClick={() => navigator.clipboard?.writeText(r.pull_command!)}>
                          copy
                        </button>
                      </span>
                    ) : null}
                    {r.is_mock ? <MockBadge show /> : null}
                    {r.warning ? <span className="muted"> {r.warning}</span> : null}
                    {r.provider === "anthropic" ? (
                      <span className="small">
                        cloud allowed: {String(r.cloud_allowed)} · API key configured: {String(r.api_key_configured)}
                        {r.sends ? <div className="muted">Would send: {r.sends}</div> : null}
                      </span>
                    ) : null}
                    {r.capabilities?.length ? <span className="muted small"> capabilities: {r.capabilities.join(", ")}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            Missing models are never replaced automatically. Without them, runs continue with the deterministic channels
            (exact, lexical, structural) and show the model stages as unavailable.
          </p>
        </>
      ) : null}
    </div>
  );
}

function StartRunForm({ assessment, settings, onStarted }: { assessment: Assessment; settings: PublicSettings | null; onStarted: () => void }) {
  const subs = useLoad(() => api.get<SubmissionSummary[]>(`/assessments/${assessment.id}/submissions`), [assessment.id]);
  const [mode, setMode] = useState<"pilot" | "explicit">("pilot");
  const [pilotSize, setPilotSize] = useState(12);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [budget, setBudget] = useState<string>("");
  const [topK, setTopK] = useState<string>("");
  const [providerMode, setProviderMode] = useState<"configured" | "mock">("configured");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/assessments/${assessment.id}/analysis-runs`, {
        submission_ids: mode === "explicit" ? [...chosen] : null,
        pilot_size: pilotSize,
        review_budget: budget.trim() ? Number(budget) : null,
        top_k: topK.trim() ? Number(topK) : null,
        provider_mode: providerMode,
      });
      onStarted();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const toggle = (id: number) => {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChosen(next);
  };

  return (
    <div className="card">
      <h3>Start an analysis run</h3>
      <p className="muted small">
        Analyst summaries → source and summary embeddings → hybrid retrieval → budgeted reviewer pairs → review cases.
        Start with a 10–15 student pilot and a 20–40 pair budget, then measure throughput before extending.
      </p>
      <div className="form-grid">
        <fieldset>
          <legend>Submissions</legend>
          <label className="inline">
            <input type="radio" checked={mode === "pilot"} onChange={() => setMode("pilot")} /> Seeded pilot sample of
            <input type="number" min={2} max={500} value={pilotSize} onChange={(e) => setPilotSize(Number(e.target.value))} className="narrow" aria-label="Pilot size" />
          </label>
          <label className="inline">
            <input type="radio" checked={mode === "explicit"} onChange={() => setMode("explicit")} /> Choose submissions ({chosen.size} selected)
          </label>
        </fieldset>
        <label>
          Review pair budget (default {settings?.initial_review_pair_budget ?? 30})
          <input type="number" min={0} value={budget} onChange={(e) => setBudget(e.target.value)} placeholder={String(settings?.initial_review_pair_budget ?? 30)} />
        </label>
        <label>
          Top-k per channel (default {settings?.retrieval_top_k_per_channel ?? 5})
          <input type="number" min={1} value={topK} onChange={(e) => setTopK(e.target.value)} placeholder={String(settings?.retrieval_top_k_per_channel ?? 5)} />
        </label>
        <label>
          Providers
          <select value={providerMode} onChange={(e) => setProviderMode(e.target.value as "configured" | "mock")}>
            <option value="configured">Configured providers (.env)</option>
            <option value="mock" disabled={!assessment.is_synthetic}>
              MOCK — labeled outputs, not real findings{assessment.is_synthetic ? "" : " (synthetic data only)"}
            </option>
          </select>
        </label>
      </div>
      {mode === "explicit" ? (
        <div className="checkbox-grid">
          {(subs.data ?? []).map((s) => (
            <label key={s.id} className="inline">
              <input type="checkbox" checked={chosen.has(s.id)} onChange={() => toggle(s.id)} /> {s.student_id}
            </label>
          ))}
        </div>
      ) : null}
      <button type="button" className="primary" onClick={start} disabled={busy || (mode === "explicit" && chosen.size < 2)}>
        Queue run
      </button>
      <ErrorBox error={error} />
    </div>
  );
}

function RunCard({ run, onChanged, onCandidates }: { run: Run; onChanged: () => void; onCandidates: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const act = async (path: string) => {
    setError(null);
    try {
      await api.post(path);
      onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  };
  const coverage = run.coverage as Record<string, unknown>;
  const notes = (coverage.notes as string[] | undefined) ?? [];
  const isMock = (run.provider_snapshot as Record<string, unknown>)?.provider_mode === "mock";
  return (
    <div className="card run">
      <div className="row space">
        <div className="row gap">
          <strong>
            Run {run.id} · {run.kind}
          </strong>
          <StatusBadge status={run.status} />
          {run.stage ? <span className="muted">stage: {run.stage}</span> : null}
          <MockBadge show={isMock} />
          {run.cancel_requested ? <Badge tone="warn">cancel requested</Badge> : null}
        </div>
        <div className="row gap">
          {(run.status === "queued" || run.status === "running") && (
            <button type="button" onClick={() => act(`/analysis-runs/${run.id}/cancel`)}>
              Cancel
            </button>
          )}
          {["interrupted", "failed", "cancelled", "partial"].includes(run.status) && (
            <button type="button" onClick={() => act(`/analysis-runs/${run.id}/resume`)}>
              Resume
            </button>
          )}
          {run.kind === "pipeline" && (
            <button type="button" onClick={onCandidates}>
              Candidate pairs
            </button>
          )}
        </div>
      </div>
      <p className="muted small">
        created {fmtTime(run.created_at)} · started {fmtTime(run.started_at)} · finished {fmtTime(run.finished_at)} · attempts {run.attempts}
      </p>
      <ErrorBox error={error} />
      {run.error ? (
        <details>
          <summary className="bad">Run error</summary>
          <pre className="json">{run.error}</pre>
        </details>
      ) : null}
      <table className="table compact">
        <thead>
          <tr>
            <th>Stage</th>
            <th>Progress</th>
            <th>Errors / notes</th>
          </tr>
        </thead>
        <tbody>
          {STAGES.filter((s) => run.progress?.[s]).map((stage) => {
            const p = run.progress[stage] as Record<string, unknown>;
            const err = p.error as { kind?: string; message?: string } | undefined;
            const fields = Object.entries(p).filter(([k]) => !["error", "skipped_reason", "note", "channel_availability"].includes(k));
            return (
              <tr key={stage}>
                <td>{stage}</td>
                <td className="small">{fields.map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}</td>
                <td className="small">
                  {err ? (
                    <span className="bad">
                      {err.kind}: {err.message}
                    </span>
                  ) : null}
                  {p.skipped_reason ? <div className="warn-text">{String(p.skipped_reason)}</div> : null}
                  {p.note ? <div className="muted">{String(p.note)}</div> : null}
                  {p.channel_availability ? (
                    <details>
                      <summary>channel availability</summary>
                      <JsonBlock value={p.channel_availability} />
                    </details>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {Object.keys(coverage).length ? (
        <div className="coverage">
          {coverage.exhaustive === false ? (
            <Notice tone="warn">Coverage is NOT exhaustive. This run does not establish that no other inconsistencies exist.</Notice>
          ) : null}
          <ul className="plain-list small">
            {notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
          <details>
            <summary>Coverage detail</summary>
            <JsonBlock value={coverage} />
          </details>
        </div>
      ) : null}
    </div>
  );
}

function CandidatesView({ runId }: { runId: number }) {
  const pairs = useLoad(() => api.get<CandidatePair[]>(`/analysis-runs/${runId}/candidates`), [runId]);
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="card">
      <h3>
        Candidate pairs for run {runId} <Loading show={pairs.loading} />
      </h3>
      <Notice tone="info">
        Retrieval diagnostics, not fairness or correctness. Similarity only decides what gets a closer look; ranks from
        different channels are fused by reciprocal rank, never averaged as scores.
      </Notice>
      <ErrorBox error={pairs.error} />
      <div className="table-wrap tall">
        <table className="table compact">
          <thead>
            <tr>
              <th>Pair</th>
              <th>Fused (RRF)</th>
              <th>Channels ranked</th>
              <th>Selected for review</th>
              <th>Scope</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(pairs.data ?? []).map((p) => (
              <Fragment key={p.id}>
                <tr className={p.selected ? "" : "dim"}>
                  <td>
                    {p.student_a} ↔ {p.student_b}
                  </td>
                  <td>{fmtNum(p.fused_score, 5)}</td>
                  <td className="small">
                    {CHANNELS.filter((c) => p.channels[c]?.status === "ranked")
                      .map((c) => `${c} #${p.channels[c].rank}`)
                      .join(", ") || (p.is_random_control ? "random control" : "—")}
                  </td>
                  <td>{p.selected ? <Badge tone="info">{p.selection_reason}</Badge> : "no"}</td>
                  <td className="small">{p.scope}</td>
                  <td>
                    <button type="button" className="link" onClick={() => setOpen(open === p.id ? null : p.id)}>
                      {open === p.id ? "hide" : "diagnostics"}
                    </button>
                  </td>
                </tr>
                {open === p.id ? (
                  <tr>
                    <td colSpan={6}>
                      <ChannelTable pair={p} />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function ChannelTable({ pair }: { pair: CandidatePair }) {
  return (
    <div>
      <p className="muted small">Retrieval diagnostics, not fairness or correctness.</p>
      <table className="table compact">
        <thead>
          <tr>
            <th>Channel</th>
            <th>Status</th>
            <th>Rank</th>
            <th>Similarity (diagnostic)</th>
            <th>Reason / partial availability</th>
          </tr>
        </thead>
        <tbody>
          {CHANNELS.map((c) => {
            const d = pair.channels[c];
            if (!d) return null;
            return (
              <tr key={c}>
                <td>{c}</td>
                <td>
                  <StatusBadge status={d.status} />
                </td>
                <td>{d.rank ?? "—"}</td>
                <td>{fmtNum(d.score)}</td>
                <td className="small">{[d.reason, d.partial].filter(Boolean).join(" · ")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {pair.reasons.length ? (
        <ul className="plain-list small">
          {pair.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : null}
      <details>
        <summary>Contributing artifact pairs</summary>
        <JsonBlock value={pair.contributing} />
      </details>
    </div>
  );
}
