import { useState } from "react";
import type { Nav } from "../App";
import { CASE_ACTIONS, api, qs, type ArtifactView, type Assessment, type CaseDetail, type CaseSummary, type Comparison, type EvidenceRef, type Run } from "../api";
import {
  Badge,
  ClaimList,
  ErrorBox,
  EvidencePanel,
  IssueList,
  JsonBlock,
  KeyValues,
  List,
  Loading,
  MockBadge,
  Notice,
  RejectedClaims,
  SourceView,
  StatusBadge,
  errorText,
  fmtTime,
  issueHighlights,
  useLoad,
  type Highlight,
} from "../components/common";
import { ChannelTable } from "./RunsScreen";

// Same flag as App.tsx: ?demo in the URL shows a simplified screen for recording.
const DEMO = new URLSearchParams(window.location.search).has("demo");
const human = (s: string | null | undefined) => (s ?? "").replace(/_/g, " ");

const ACTION_LABELS: Record<string, string> = {
  difference_justified: "Difference justified",
  needs_clarification: "Needs clarification",
  context_incomplete: "Context incomplete",
  possible_recording_error: "Possible recording error",
  dismiss_model_analysis: "Dismiss model analysis",
};

export default function ReviewScreen({ assessment, nav }: { assessment: Assessment; nav: Nav }) {
  const [includeStale, setIncludeStale] = useState(false);
  const [runId, setRunId] = useState("");
  const runs = useLoad(() => api.get<Run[]>(`/analysis-runs${qs({ assessment_id: assessment.id })}`), [assessment.id]);
  const cases = useLoad(
    () => api.get<CaseSummary[]>(`/review-cases${qs({ assessment_id: assessment.id, include_stale: includeStale, run_id: runId || undefined })}`),
    [assessment.id, includeStale, runId],
  );
  const selected = Number(nav.params.get("case")) || null;
  if (selected) {
    return <CaseView id={selected} nav={nav} onBack={() => nav.go("review")} onDecided={cases.reload} />;
  }
  return (
    <section>
      <h2>
        Review queue <Loading show={cases.loading} />
      </h2>
      {DEMO ? (
        <p className="muted" style={{ maxWidth: "48rem" }}>
          Pairs of comparable submissions whose recorded scores differ. Each one is a question for a person, not a
          verdict. Biggest score gaps first.
        </p>
      ) : (
        <>
          <p className="muted">
            Each case pairs retrieved, potentially comparable work with its original recorded scores (joined after the
            semantic review). Cases are review prompts, not verdicts. Ordered by recorded-score gap, then reviewer relation.
          </p>
          <label className="inline">
            Analysis run
            <select value={runId} onChange={(e) => setRunId(e.target.value)}>
              <option value="">All runs</option>
              {(runs.data ?? []).filter((r) => r.kind === "pipeline").map((r) => (
                <option key={r.id} value={r.id}>Run {r.id} · {r.status}</option>
              ))}
            </select>
            <ErrorBox error={runs.error} />
          </label>
          <label className="inline">
            <input type="checkbox" checked={includeStale} onChange={(e) => setIncludeStale(e.target.checked)} /> Include historical
            (stale) cases
          </label>
        </>
      )}
      <ErrorBox error={cases.error} />
      {cases.data && cases.data.length === 0 ? (
        <p className="muted">No {includeStale ? "" : "current "}cases. Run an analysis, or include historical cases.</p>
      ) : null}
      <div className="case-list">
        {(cases.data ?? []).map((c) => (
          <button key={c.id} type="button" className={`case-item ${c.is_current ? "" : "stale"}`} onClick={() => nav.go("review", { case: c.id })}>
            <div className="row gap wrap">
              <strong>
                {DEMO ? `${c.student_a} ↔ ${c.student_b}` : `Case ${c.id}`}
              </strong>
              <Badge tone="info">{DEMO ? human(c.category) : c.category}</Badge>
              {c.score_gap !== null && DEMO ? <Badge tone="bad">{c.score_gap} pt gap</Badge> : null}
              <MockBadge show={c.is_mock} />
              {c.origin === "deterministic_exact" && !DEMO ? <Badge tone="neutral">deterministic</Badge> : null}
              {!c.is_current ? (
                <Badge tone="warn" title={c.stale_reason ?? ""}>
                  historical
                </Badge>
              ) : null}
              {c.decision_count ? <Badge tone="ok">{c.decision_count} decision(s)</Badge> : null}
            </div>
            <div className="case-headline">{c.headline}</div>
            <div className="small muted">
              {c.student_a} ({c.score_a ?? "blank"} · {c.grader_a ?? "no grader"}) ↔ {c.student_b} ({c.score_b ?? "blank"} ·{" "}
              {c.grader_b ?? "no grader"}) · recorded gap {c.score_gap ?? "n/a"}
              {!c.is_current && c.stale_reason ? ` · ${c.stale_reason}` : ""}
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function CaseView({ id, nav, onBack, onDecided }: { id: number; nav: Nav; onBack: () => void; onDecided: () => void }) {
  const detail = useLoad(() => api.get<CaseDetail>(`/review-cases/${id}`), [id]);
  const [focus, setFocus] = useState<{ artifactId: number; start: number; end: number } | null>(null);
  const c = detail.data;
  const onRef = (r: EvidenceRef) => setFocus({ artifactId: r.artifact_id, start: r.line_start, end: r.line_end });
  const result = c?.comparison?.result ?? null;

  return (
    <section>
      <button type="button" className="link" onClick={onBack}>
        ← back to queue
      </button>
      <ErrorBox error={detail.error} />
      <Loading show={detail.loading} />
      {c ? (
        <>
          <h2>
            {DEMO ? `${c.student_a} ↔ ${c.student_b}` : `Case ${c.id}`}{" "}
            <Badge tone="info">{DEMO ? human(c.category) : c.category}</Badge> <MockBadge show={c.is_mock} />
          </h2>
          {!c.is_current ? <Notice tone="warn">Historical case (not current): {c.stale_reason}</Notice> : null}

          {DEMO ? (
            <div className="cards" style={{ marginBottom: "0.8rem" }}>
              {[
                [c.student_a, c.score_a, c.grader_a],
                [c.student_b, c.score_b, c.grader_b],
              ].map(([student, score, grader], i) => (
                <div key={i} className="card" style={{ padding: "1rem 1.3rem" }}>
                  <div className="muted small">Recorded score · unchanged</div>
                  <div style={{ fontSize: "2rem", fontWeight: 600, letterSpacing: "-0.03em" }}>{score ?? "blank"}</div>
                  <div>
                    <strong>{student}</strong> <span className="muted">· graded by {grader ?? "no grader label"}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : null}

          <p className="case-headline">{c.headline}</p>
          <div className="question">
            <span className="label">{DEMO ? "AI asks the instructor" : "Question for the head TA / instructor"}</span>
            <p>{c.question}</p>
          </div>

          {DEMO ? null : (
            <>
              <KeyValues
                rows={[
                  ["Origin", c.origin],
                  ["Context mode", c.context_mode],
                  ["Recorded scores (original, unchanged)", `${c.student_a}: ${c.score_a ?? "blank"} (${c.grader_a ?? "no grader label"}) · ${c.student_b}: ${c.score_b ?? "blank"} (${c.grader_b ?? "no grader label"})`],
                  ["Guidance versions", c.guidance_versions.length ? JSON.stringify(c.guidance_versions) : "none"],
                ]}
              />
              <p className="muted small">
                A whole-assignment score gap does not imply a question-level deduction. The grader assumption (row grader
                graded all assessments) is a recorded working assumption.
              </p>
            </>
          )}

          <div className="sides">
            {c.sides.map((side, i) => (
              <div key={side.submission_id} className="side">
                <h3>
                  {DEMO ? side.student_id : `Submission ${i + 1}: ${side.student_id}`}
                </h3>
                {DEMO ? null : <p className="muted small">component scope: {side.component_scope_status}</p>}
                {side.artifacts.map((artifact) => (
                  <SideArtifact
                    key={artifact.id}
                    artifact={artifact}
                    focus={focus && focus.artifactId === artifact.id ? focus : null}
                    claims={result}
                    onRef={onRef}
                  />
                ))}
              </div>
            ))}
          </div>

          <h3>{DEMO ? "How they differ" : "Reviewer comparison"}</h3>
          {c.comparison ? (
            <div className="card">
              <div className="row gap wrap">
                {DEMO ? <Badge tone="info">AI draft · verify before acting</Badge> : <StatusBadge status={c.comparison.status} />}
                <MockBadge show={c.comparison.is_mock} />
                {DEMO ? null : (
                  <span className="muted small">
                    {c.comparison.provider} · {c.comparison.model} · {c.comparison.prompt_version}
                  </span>
                )}
                {!c.comparison.is_current ? <Badge tone="warn">not current</Badge> : null}
              </div>
              {c.comparison.status === "failed" ? (
                <Notice tone="bad">
                  Reviewer failed ({c.comparison.error_kind}): {c.comparison.error_message}
                </Notice>
              ) : null}
              {result ? (
                <>
                  {DEMO ? null : (
                    <KeyValues
                      rows={[
                        ["Relation", result.relation],
                        ["Scope", result.scope],
                        ["Requirement basis", result.requirement_basis],
                        ["Equivalence level", result.equivalence_level ?? "—"],
                        ["Suggested category", result.suggested_category],
                      ]}
                    />
                  )}
                  {result.requirement_basis === "unavailable" && !DEMO ? (
                    <p className="note">No requirement basis: this is a content-level comparison; academic equivalence is unknown.</p>
                  ) : null}
                  <ClaimList title={DEMO ? "What both do" : "Shared observations"} claims={result.shared_observations} onRef={onRef} />
                  <ClaimList title={DEMO ? "Where they differ (click a quote to see it in the code)" : "Material differences"} claims={result.material_differences} onRef={onRef} />
                  {DEMO && !(result.contradicted_analyst_claims ?? []).length ? null : (
                    <ClaimList title="Contradicted analyst claims" claims={result.contradicted_analyst_claims} onRef={onRef} />
                  )}
                  <h4>{DEMO ? "Other possible explanations" : "Alternative explanations"}</h4>
                  <List items={result.alternative_explanations} />
                  {DEMO ? null : (
                    <>
                      <h4>Missing context</h4>
                      <List items={result.missing_context} />
                    </>
                  )}
                </>
              ) : null}
              {DEMO ? null : (
                <>
                  <RejectedClaims items={c.comparison.rejected_claims} />
                  {result?.deterministic_differences ? (
                    <details>
                      <summary>Deterministic differences (computed from source text)</summary>
                      <JsonBlock value={result.deterministic_differences} />
                    </details>
                  ) : null}
                  <details>
                    <summary>What was sent to the reviewer</summary>
                    <KeyValues
                      rows={[
                        ["Provider / model", `${String(c.comparison.sent_content.provider)} / ${String(c.comparison.sent_content.model)}`],
                        ["Included", (c.comparison.sent_content.included ?? []).join(", ")],
                        ["Excluded", (c.comparison.sent_content.excluded ?? []).join(", ")],
                        ["Prompt characters", String(c.comparison.sent_content.prompt_characters ?? "—")],
                      ]}
                    />
                  </details>
                </>
              )}
            </div>
          ) : (
            <p className="muted">
              No model comparison for this case (deterministic check only). Byte-identical files do not prove equivalent
              submissions or misconduct.
            </p>
          )}

          {DEMO ? null : (
            <details className="card">
              <summary>Candidate retrieval diagnostics</summary>
              <ChannelTable pair={c.candidate} />
            </details>
          )}

          <h3>{DEMO ? "Your decision" : "Decisions"}</h3>
          {c.decisions.length === 0 ? <p className="muted">No decisions yet.</p> : null}
          <ul className="plain-list">
            {c.decisions.map((d) => (
              <li key={d.id}>
                <Badge tone="ok">{ACTION_LABELS[d.action] ?? d.action}</Badge> {d.comment}{" "}
                {d.proposed_score_a !== null || d.proposed_score_b !== null ? (
                  <span className="small">
                    proposed (human): A={d.proposed_score_a ?? "—"} B={d.proposed_score_b ?? "—"}
                  </span>
                ) : null}{" "}
                <span className="muted small">
                  {d.actor} · {fmtTime(d.created_at)}
                </span>
              </li>
            ))}
          </ul>
          <DecisionForm
            caseId={c.id}
            studentA={c.student_a}
            studentB={c.student_b}
            onSaved={() => {
              detail.reload();
              onDecided();
            }}
          />
          <button
            type="button"
            className={DEMO ? "primary" : ""}
            onClick={() => nav.go("notes", { from_case: c.id })}
          >
            {DEMO ? "Clarify the guidance from this case →" : "Create instructor note from this case"}
          </button>
        </>
      ) : null}
    </section>
  );
}

function SideArtifact({
  artifact, focus, claims, onRef,
}: {
  artifact: ArtifactView;
  focus: { start: number; end: number } | null;
  claims: Comparison["result"];
  onRef: (r: EvidenceRef) => void;
}) {
  const refs: EvidenceRef[] = [
    ...(claims?.shared_observations ?? []),
    ...(claims?.material_differences ?? []),
    ...(claims?.contradicted_analyst_claims ?? []),
  ]
    .flatMap((x) => x.evidence_refs)
    .filter((r) => r.artifact_id === artifact.id);
  const highlights: Highlight[] = [
    ...issueHighlights(artifact.issues),
    ...refs.map((r) => ({ start: r.line_start, end: r.line_end, tone: "evidence" as const, note: r.quote })),
    ...(focus ? [{ ...focus, tone: "focus" as const }] : []),
  ];
  return (
    <div className="side-artifact">
      {DEMO ? (
        artifact.label ? <Badge tone="info">{artifact.label}</Badge> : null
      ) : (
        <div className="row gap wrap">
          {artifact.label ? <Badge tone="info">{artifact.label}</Badge> : null}
          <span className="small">{artifact.aliases.map((a) => a.relative_path).join(" = ")}</span>
          <code className="small">{artifact.content_sha256.slice(0, 12)}</code>
          {artifact.parse_status !== "ok" ? <Badge tone="bad">parse failed</Badge> : null}
        </div>
      )}
      <SourceView text={artifact.source_text} highlights={highlights} />
      {artifact.issues.length && !DEMO ? (
        <details>
          <summary>{artifact.issues.length} extraction issue(s)</summary>
          <IssueList issues={artifact.issues} />
        </details>
      ) : null}
      {DEMO ? null : (
        <details>
          <summary>Analyst summary (fallible)</summary>
          <EvidencePanel record={artifact.evidence} onRef={onRef} />
        </details>
      )}
    </div>
  );
}

function DecisionForm({ caseId, studentA, studentB, onSaved }: { caseId: number; studentA: string | null; studentB: string | null; onSaved: () => void }) {
  const [action, setAction] = useState<string>("needs_clarification");
  const [comment, setComment] = useState("");
  const [scoreA, setScoreA] = useState("");
  const [scoreB, setScoreB] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const needsReason = DEMO && !comment.trim();

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/review-cases/${caseId}/decisions`, {
        action,
        comment,
        proposed_score_a: scoreA.trim() ? Number(scoreA) : null,
        proposed_score_b: scoreB.trim() ? Number(scoreB) : null,
      });
      setComment("");
      setScoreA("");
      setScoreB("");
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="card form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        if (!needsReason) submit();
      }}
    >
      <h4>Record a decision</h4>
      <fieldset className="radio-row">
        <legend>Action</legend>
        {CASE_ACTIONS.map((a) => (
          <label key={a} className="inline">
            <input type="radio" name={`action-${caseId}`} value={a} checked={action === a} onChange={() => setAction(a)} /> {ACTION_LABELS[a]}
          </label>
        ))}
      </fieldset>
      <label>
        {DEMO ? (
          <span>
            Reason <span className="bad">*</span> <span className="muted small">(required, saved to the audit log)</span>
          </span>
        ) : (
          "Comment"
        )}
        <textarea rows={2} value={comment} onChange={(e) => setComment(e.target.value)} />
      </label>
      <div className="row gap wrap">
        <label>
          Proposed score for {studentA} (optional)
          <input value={scoreA} onChange={(e) => setScoreA(e.target.value)} inputMode="decimal" className="narrow" />
        </label>
        <label>
          Proposed score for {studentB} (optional)
          <input value={scoreB} onChange={(e) => setScoreB(e.target.value)} inputMode="decimal" className="narrow" />
        </label>
      </div>
      <p className="note">Proposal only — original scores are never changed.</p>
      <div>
        <button type="submit" className="primary" disabled={busy || needsReason}>
          Save decision
        </button>
      </div>
      <ErrorBox error={error} />
    </form>
  );
}