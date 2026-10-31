import { useEffect, useMemo, useState } from "react";
import { ApiError, api, qs, type Assessment } from "../api";
import type { Nav } from "../App";
import {
  Badge,
  ErrorBox,
  IssueList,
  KeyValues,
  Loading,
  MockBadge,
  Notice,
  SourceView,
  Tabs,
  errorText,
  fmtScore,
  fmtTime,
  issueHighlights,
  useLoad,
  usePoll,
  type Highlight,
} from "../components/common";
import {
  classificationTone,
  feedbackTone,
  newIdempotencyKey,
  readLocal,
  settle,
  writeLocal,
  type CalibrationIssue,
  type Criterion,
  type DraftRevision,
  type Feedback,
  type FeedbackCard,
  type QueueRow,
  type SaveDraftResult,
  type Workspace,
  type WorkspaceArtifact,
} from "../p1api";

const GRADER_KEY = "gradelens.graderLabel";

export default function GradingScreen({ assessment, nav }: { assessment: Assessment; nav: Nav }) {
  const queue = useLoad(() => api.get<QueueRow[]>(`/assessments/${assessment.id}/grading-queue`), [assessment.id]);
  const selected = Number(nav.params.get("submission")) || null;
  const pending = !!queue.data?.some((r) => ["queued", "running", "stale"].includes(r.feedback_status ?? ""));
  usePoll(pending, queue.reload, 1500);

  const rows = queue.data ?? [];
  const select = (id: number) => nav.go("grading", { submission: id });
  const next = () => {
    if (!rows.length) return;
    const index = rows.findIndex((r) => r.submission_id === selected);
    const target = rows[(index + 1) % rows.length];
    select(target.submission_id);
  };

  return (
    <section>
      <div className="row space">
        <h2>Grading workspace</h2>
        <span className="muted small">
          Save a draft and keep grading; feedback is prepared in the background. Feedback is advisory and never changes a
          grade.
        </span>
      </div>
      <ErrorBox error={queue.error} />
      <div className="grading-layout">
        <aside className="grading-queue">
          <h3>
            Queue <Loading show={queue.loading} />
          </h3>
          <div className="table-wrap tall">
            <table className="table compact clickable" data-testid="grading-queue">
              <thead>
                <tr>
                  <th>Student</th>
                  <th title="Imported historical total (a prior observation, not ground truth)">Imported total</th>
                  <th>Draft</th>
                  <th>Feedback</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={r.submission_id}
                    className={r.submission_id === selected ? "selected" : ""}
                    onClick={() => select(r.submission_id)}
                    data-testid={`queue-row-${r.student_id}`}
                  >
                    <td>
                      {r.student_id}
                      <div className="muted small">{r.files} file(s)</div>
                    </td>
                    <td className="small">{fmtScore(r.recorded)}</td>
                    <td>{r.draft ? <strong>{r.draft.total}</strong> : <span className="muted">—</span>}</td>
                    <td>
                      {r.feedback_status ? <Badge tone={feedbackTone(r.feedback_status)}>{r.feedback_status}</Badge> : null}
                      {r.open_cards ? <span className="small"> {r.open_cards} open</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </aside>
        <div className="grading-main">
          {selected ? (
            <WorkspaceView
              key={selected}
              submissionId={selected}
              assessment={assessment}
              onNext={next}
              onChanged={queue.reload}
            />
          ) : (
            <p className="muted">Select a submission from the queue.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function WorkspaceView({
  submissionId, assessment, onNext, onChanged,
}: { submissionId: number; assessment: Assessment; onNext: () => void; onChanged: () => void }) {
  const ws = useLoad(() => api.get<Workspace>(`/submissions/${submissionId}/workspace`), [submissionId]);
  const issues = useLoad(
    () => api.get<CalibrationIssue[]>(`/calibration-issues${qs({ assessment_id: assessment.id })}`),
    [assessment.id],
  );
  const fbStatus = ws.data?.feedback?.status;
  const [expectRevision, setExpectRevision] = useState<number | null>(null);
  const behind = expectRevision !== null && (ws.data?.current_revision?.id ?? -1) < expectRevision;
  usePoll(fbStatus === "queued" || fbStatus === "running" || fbStatus === "stale", ws.reload, 1500);
  usePoll(behind, ws.reload, 400);
  const [focus, setFocus] = useState<{ artifactId?: number; lines: number[] } | null>(null);
  const [saveInfo, setSaveInfo] = useState<string | null>(null);

  const refresh = () => settle(ws.reload, issues.reload, onChanged);

  const d = ws.data;
  return (
    <div className="workspace" data-testid="workspace">
      <ErrorBox error={ws.error} />
      {!d ? <Loading show /> : null}
      {d ? (
        <>
          <div className="row space">
            <h3 data-testid="workspace-student">
              {d.student_id} · {d.assignment}
            </h3>
            <button type="button" onClick={onNext} data-testid="next-submission">
              Next submission →
            </button>
          </div>
          <KeyValues
            rows={[
              ["Imported historical total", <span key="r">{fmtScore(d.recorded)}</span>],
              ["About that total", d.recorded_note],
              [
                "Current draft",
                d.current_revision ? (
                  <span key="c">
                    revision {d.current_revision.revision_number}: <strong>{d.current_revision.total}</strong> ·{" "}
                    {d.current_revision.grader_label} · <Badge>{d.current_revision.origin}</Badge>
                  </span>
                ) : (
                  "no draft yet"
                ),
              ],
            ]}
          />
          {saveInfo ? (
            <Notice tone="ok">
              <span data-testid="save-status">{saveInfo}</span>
            </Notice>
          ) : null}
          <DraftForm
            ws={d}
            onSaved={(msg, revisionId) => {
              setSaveInfo(msg);
              if (revisionId) setExpectRevision(revisionId);
              refresh();
            }}
          />
          <FeedbackPanel
            feedback={d.feedback}
            hasDraft={!!d.current_revision}
            criteria={d.criteria}
            issues={issues.data ?? []}
            onFocus={setFocus}
            onChanged={(msg) => {
              if (msg) setSaveInfo(msg);
              refresh();
            }}
          />
          <ArtifactsView artifacts={d.artifacts} focus={focus} feedback={d.feedback}
            assignment={d.assignment} onChanged={refresh} />
          <RubricBlock criteria={d.criteria} issues={d.rubric_issues} />
          <HistoryBlock history={d.history} />
        </>
      ) : null}
    </div>
  );
}

function awardCriteria(criteria: Criterion[]): Criterion[] {
  return criteria.filter((c) => !c.is_container && c.rule_kind === "award");
}

function DraftForm({ ws, onSaved }: { ws: Workspace; onSaved: (message: string, revisionId?: number) => void }) {
  const current = ws.current_revision;
  const [total, setTotal] = useState(current?.total ?? "");
  const [grader, setGrader] = useState(() => readLocal(GRADER_KEY, current?.grader_label ?? ""));
  const [points, setPoints] = useState<Record<string, string>>(current?.criterion_points ?? {});
  const [reasons, setReasons] = useState((current?.deduction_reasons ?? []).join("\n"));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<DraftRevision | null>(null);
  const [showCriteria, setShowCriteria] = useState(false);
  const criteria = awardCriteria(ws.criteria);

  async function save() {
    setBusy(true);
    setError(null);
    setConflict(null);
    writeLocal(GRADER_KEY, grader);
    const criterion_points = Object.fromEntries(Object.entries(points).filter(([, v]) => v.trim() !== ""));
    try {
      const result = await api.post<SaveDraftResult>(`/submissions/${ws.submission_id}/draft-grades`, {
        total,
        expected_revision_id: current?.id ?? null,
        grader_label: grader,
        criterion_points: Object.keys(criterion_points).length ? criterion_points : null,
        deduction_reasons: reasons.split("\n").map((r) => r.trim()).filter(Boolean),
        note,
        idempotency_key: newIdempotencyKey(),
      });
      onSaved(
        `Saved revision ${result.revision.revision_number}` +
          (result.feedback ? ` · feedback ${result.feedback.status}` : "") +
          (result.created ? "" : " (duplicate request: existing revision returned)") +
          ". You can continue to the next submission.",
        result.revision.id,
      );
      setNote("");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.detail as { current_revision?: DraftRevision } | null;
        setConflict(detail?.current_revision ?? null);
        setError("This draft changed since you opened it. Review the current revision, then save again.");
      } else {
        setError(errorText(err));
      }
    } finally {
      setBusy(false);
    }
  }

  async function fromHistorical() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<SaveDraftResult>(`/submissions/${ws.submission_id}/draft-from-historical`, {
        expected_revision_id: current?.id ?? null,
        grader_label: grader,
      });
      onSaved(`Saved revision ${result.revision.revision_number} from the imported total (original kept unchanged).`, result.revision.id);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="card draft-form"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <h4>Draft grade</h4>
      <div className="form-grid">
        <label>
          Total (required)
          <input
            value={total}
            onChange={(e) => setTotal(e.target.value)}
            inputMode="decimal"
            required
            data-testid="draft-total"
            aria-label="Draft total"
          />
        </label>
        <label>
          Grader label
          <input
            value={grader}
            onChange={(e) => setGrader(e.target.value)}
            placeholder="e.g. Grader A"
            data-testid="grader-label"
            aria-label="Grader label (provenance, not authentication)"
          />
          <span className="note">Provenance label only; not authentication.</span>
        </label>
        <label>
          Note (optional)
          <input value={note} onChange={(e) => setNote(e.target.value)} aria-label="Draft note" />
        </label>
      </div>
      <details open={showCriteria} onToggle={(e) => setShowCriteria((e.target as HTMLDetailsElement).open)}>
        <summary>Optional: criterion points and deduction reasons (never required)</summary>
        {criteria.length ? (
          <table className="table compact">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Max</th>
                <th>Evidence class</th>
                <th>Points</th>
              </tr>
            </thead>
            <tbody>
              {criteria.map((c) => (
                <tr key={c.criterion_key}>
                  <td className="small">
                    <code>{c.criterion_key}</code>
                    <div>{c.title}</div>
                  </td>
                  <td>{c.max_points ?? "—"}</td>
                  <td>
                    <Badge tone={classificationTone(c.classification)}>{c.classification}</Badge>
                  </td>
                  <td>
                    <input
                      className="narrow"
                      value={points[c.criterion_key] ?? ""}
                      onChange={(e) => setPoints({ ...points, [c.criterion_key]: e.target.value })}
                      inputMode="decimal"
                      aria-label={`Points for ${c.criterion_key}`}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No rubric linked: total-only grading (content-only mode).</p>
        )}
        <label className="form-stack">
          Deduction reasons (one per line)
          <textarea value={reasons} onChange={(e) => setReasons(e.target.value)} rows={2} aria-label="Deduction reasons" />
        </label>
      </details>
      <div className="row gap wrap">
        <button type="submit" className="primary" disabled={busy || !total.trim()} data-testid="save-draft">
          Save draft
        </button>
        <button type="button" onClick={fromHistorical} disabled={busy || !ws.recorded || ws.recorded.score === null}>
          Start draft from imported total
        </button>
        <span className="muted small">
          Starting from the imported total is an explicit, recorded action; the original record is kept.
        </span>
      </div>
      <ErrorBox error={error} />
      {conflict ? (
        <Notice tone="warn">
          Current revision {conflict.revision_number}: total {conflict.total} by {conflict.grader_label} (
          {fmtTime(conflict.created_at)}).{" "}
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
        </Notice>
      ) : null}
    </form>
  );
}

function FeedbackPanel({
  feedback, hasDraft, criteria, issues, onFocus, onChanged,
}: {
  feedback: Feedback | null;
  hasDraft: boolean;
  criteria: Criterion[];
  issues: CalibrationIssue[];
  onFocus: (f: { artifactId?: number; lines: number[] } | null) => void;
  onChanged: (message?: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  if (!hasDraft) {
    return (
      <div className="card" data-testid="feedback-panel">
        <h4>Feedback</h4>
        <p className="muted">Save a draft to queue feedback.</p>
      </div>
    );
  }
  if (!feedback) {
    return (
      <div className="card" data-testid="feedback-panel">
        <h4>Feedback</h4>
        <p className="muted">No feedback has been queued for the current revision.</p>
      </div>
    );
  }
  const cov = feedback.coverage ?? {};
  const pending = feedback.status === "queued" || feedback.status === "running";
  const deterministicReady = (feedback.stages?.deterministic as { status?: string } | undefined)?.status === "completed";
  const cards = showAll ? feedback.all_cards : feedback.initial_cards;
  return (
    <div className="card" data-testid="feedback-panel">
      <div className="row space">
        <h4>
          Feedback for revision {feedback.revision_number ?? feedback.revision_id}{" "}
          <Badge tone={feedbackTone(feedback.status)}>
            <span data-testid="feedback-status">{feedback.status}</span>
          </Badge>
          {!feedback.is_current ? <Badge tone="warn">not current</Badge> : null}
        </h4>
        <span className="muted small">trigger: {feedback.trigger}</span>
      </div>
      {pending ? <Notice tone="info">Checks are running in the background. You can move on to the next submission.</Notice> : null}
      {feedback.error ? <Notice tone="warn">{feedback.error}</Notice> : null}
      {cov.mode === "content_only" ? <Notice tone="info">
        No rubric is linked to this assessment. Open <a href="#/rubric">Rubric</a>, import/select the matching guide,
        and link it to this assessment. “Request head-TA calibration” appears on a feedback card with a policy question,
        not on every draft. For walkthrough step 4, select [SYNTHETIC] synthetic-p1 · SA3 and open P001.
      </Notice> : null}
      {deterministicReady && pending ? <Notice tone="info">Source checks are ready below. Model analysis is still pending.</Notice> : null}
      {!pending || deterministicReady ? (
        <>
          <KeyValues
            rows={[
              ["Mode", String(cov.mode ?? "—")],
              ["Unresolved criteria", String(cov.unresolved_criteria?.length ?? 0)],
              [
                "Blocking rubric issues",
                cov.blocking_rubric_issues?.length ? cov.blocking_rubric_issues.join(" · ") : "none",
              ],
              [
                "Peers",
                cov.peers?.note
                  ? cov.peers.note
                  : `${cov.peers?.drafted_peers ?? 0} drafted peer(s); ${cov.peers?.compared?.length ?? 0} compared`,
              ],
            ]}
          />
          <p className="note" data-testid="coverage-statement">
            {cov.statement ??
              "No card means no issue was found within checked coverage. It does not mean the grade was verified correct."}
          </p>
          {Object.entries(feedback.stages ?? {}).some(([, v]) => (v as { status?: string })?.status === "failed") ? (
            <Notice tone="warn">A model stage failed; deterministic checks are still shown (partial feedback).</Notice>
          ) : null}
          {Object.entries(feedback.stages ?? {}).some(([, v]) => (v as { status?: string })?.status === "partial") ? (
            <Notice tone="warn">Model evidence is partial. Source windows or rejected claims leave gaps; whole-program behavior is not verified. See stage details below.</Notice>
          ) : null}
          {cards.length === 0 ? <p className="muted">No actionable cards within checked coverage.</p> : null}
          <div className="feedback-cards">
            {cards.map((card) => (
              <CardView
                key={card.id}
                card={card}
                criteria={criteria}
                issues={issues}
                onFocus={onFocus}
                onChanged={onChanged}
              />
            ))}
          </div>
          {feedback.counts.total > feedback.initial_cards.length || showAll ? (
            <button type="button" className="link" onClick={() => setShowAll(!showAll)} data-testid="toggle-all-cards">
              {showAll ? "Show initial cards only" : `Show all ${feedback.counts.total} cards`}
              {feedback.counts.suppressed ? ` (${feedback.counts.suppressed} suppressed)` : ""}
            </button>
          ) : null}
          <details className="small" data-testid="feedback-stage-details">
            <summary>Stage details</summary>
            <p className="note">Source checks and model analysis finish independently. Partial model evidence does not verify correctness.</p>
            <KeyValues rows={Object.entries(feedback.stages ?? {}).filter(([, value]) =>
              value !== null && typeof value === "object" && "status" in value
            ).map(([key, value]) => [key.replace(/_/g, " "), String((value as { status: string }).status)])} />
            <details><summary>Coverage and error metadata</summary><pre style={{ whiteSpace: "pre-wrap", maxHeight: 300, overflow: "auto" }}>
              {JSON.stringify(feedback.stages, null, 2)}
            </pre></details>
          </details>
        </>
      ) : null}
    </div>
  );
}

function evidenceLines(card: FeedbackCard): { artifactId?: number; lines: number[] } {
  const lines: number[] = [];
  let artifactId: number | undefined;
  for (const e of card.evidence) {
    if (e.line) lines.push(e.line);
    if (e.lines) lines.push(...e.lines);
    if (e.artifact_id) artifactId = artifactId ?? e.artifact_id;
  }
  return { artifactId, lines };
}

function CardView({
  card, criteria, issues, onFocus, onChanged,
}: {
  card: FeedbackCard;
  criteria: Criterion[];
  issues: CalibrationIssue[];
  onFocus: (f: { artifactId?: number; lines: number[] } | null) => void;
  onChanged: (message?: string) => void;
}) {
  const [mode, setMode] = useState<"" | "keep" | "revise" | "link">("");
  const [text, setText] = useState("");
  const [newTotal, setNewTotal] = useState("");
  const [critKey, setCritKey] = useState(card.criterion_key ?? "");
  const [critPoints, setCritPoints] = useState("");
  const [linkId, setLinkId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const linkable = issues.filter((i) => i.state === "open" || i.state === "awaiting_head_ta");
  const suppressed = card.status === "suppressed";

  async function act(action: string, extra: Record<string, unknown> = {}) {
    setBusy(true);
    setError(null);
    try {
      const result = await api.post<Record<string, unknown>>(`/feedback-cards/${card.id}/dispositions`, {
        action,
        revision_id: card.revision_id,
        ...extra,
      });
      const message =
        action === "revise"
          ? `Saved revision (id ${String(result.new_revision_id)}) from your explicit revision; feedback queued.`
          : action === "request_calibration"
            ? `Head-TA question ${result.issue_created ? "created" : "reused"} (#${String(result.issue_id)}).`
            : `Recorded: ${action}.`;
      setMode("");
      onChanged(message);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const body = (
    <>
      <p className="card-body" data-testid="card-body">
        {card.body}
      </p>
      {card.question ? (
        <p className="card-question" data-testid="card-question">
          <strong>Question:</strong> {card.question}
        </p>
      ) : null}
      {card.evidence.length ? (
        <div className="refs">
          {card.evidence.map((e, i) => (
            <button
              type="button"
              key={i}
              className="ref-chip"
              onClick={() => onFocus({ artifactId: e.artifact_id, lines: e.line ? [e.line] : e.lines ?? [] })}
              data-testid="card-evidence"
            >
              {e.line ? `line ${e.line}: ` : e.fact ? `${e.fact} = ${String(e.actual)} ` : ""}
              {e.quote ? <code>{e.quote}</code> : e.lines?.length ? `(lines ${e.lines.join(", ")})` : null}
            </button>
          ))}
        </div>
      ) : null}
      {card.rubric_refs.length ? (
        <details>
          <summary>Rubric reference{card.rubric_refs.length > 1 ? "s" : ""}</summary>
          {card.rubric_refs.map((r) => (
            <div key={r.criterion_key} className="small">
              <code>{r.criterion_key}</code> (p. {r.page}, max {r.max_points ?? "—"}, {r.classification}):{" "}
              <q>{r.quote}</q>
            </div>
          ))}
        </details>
      ) : null}
      {Object.keys(card.comparison ?? {}).length ? (
        <details>
          <summary>Comparison / proposal details</summary>
          <pre className="json">{JSON.stringify(card.comparison, null, 2)}</pre>
          <p className="note">Proposal only — original and draft grades never change automatically.</p>
        </details>
      ) : null}
    </>
  );

  return (
    <div className={`feedback-card ${suppressed ? "dim" : ""}`} data-testid={`card-${card.type}`}>
      <div className="row gap wrap">
        <Badge tone="info">{card.type}</Badge>
        <strong className="card-title">{card.title}</strong>
        <Badge>{card.status}</Badge>
        <MockBadge show={card.is_mock} />
        {card.issue ? (
          <Badge tone={card.issue.state === "resolved" ? "ok" : "warn"} title={card.issue.question}>
            <span data-testid="card-issue-chip">
              shared issue #{card.issue.id}: {card.issue.state === "awaiting_head_ta" ? "awaiting head-TA review" : card.issue.state} ·{" "}
              {card.issue.affected} affected
            </span>
          </Badge>
        ) : null}
      </div>
      {suppressed ? (
        <details>
          <summary className="muted small">Suppressed: {card.status_reason}</summary>
          {body}
        </details>
      ) : (
        body
      )}
      {card.dispositions.length ? (
        <div className="small muted">
          {card.dispositions.map((d, i) => (
            <div key={i}>
              {d.action} by {d.actor} · {fmtTime(d.at)} {d.explanation ? `— ${d.explanation}` : ""}
            </div>
          ))}
        </div>
      ) : null}
      {card.status === "open" ? (
        <div className="card-actions row gap wrap">
          <button type="button" onClick={() => setMode(mode === "keep" ? "" : "keep")} data-testid="action-keep">
            Keep with explanation
          </button>
          <button type="button" onClick={() => setMode(mode === "revise" ? "" : "revise")} data-testid="action-revise">
            Revise draft
          </button>
          <button type="button" onClick={() => act("defer")} disabled={busy} data-testid="action-defer">
            Defer
          </button>
          {card.can_request_calibration && !card.issue ? (
            <button type="button" onClick={() => act("request_calibration")} disabled={busy} data-testid="action-request-calibration">
              Request head-TA calibration
            </button>
          ) : null}
          {!card.issue && linkable.length ? (
            <button type="button" onClick={() => setMode(mode === "link" ? "" : "link")} data-testid="action-link">
              Link to existing issue
            </button>
          ) : null}
        </div>
      ) : null}
      {mode === "keep" ? (
        <div className="inline-form">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Why the draft stays as it is (resolves this case only)"
            rows={2}
            aria-label="Explanation"
            data-testid="keep-explanation"
          />
          <button type="button" disabled={busy || !text.trim()} onClick={() => act("keep_with_explanation", { explanation: text })}>
            Save explanation
          </button>
        </div>
      ) : null}
      {mode === "revise" ? (
        <div className="inline-form">
          <label>
            New total
            <input
              value={newTotal}
              onChange={(e) => setNewTotal(e.target.value)}
              inputMode="decimal"
              className="narrow"
              data-testid="revise-total"
              aria-label="New total"
            />
          </label>
          <label>
            Criterion (optional)
            <select value={critKey} onChange={(e) => setCritKey(e.target.value)} aria-label="Criterion to set">
              <option value="">(none)</option>
              {awardCriteria(criteria).map((c) => (
                <option key={c.criterion_key} value={c.criterion_key}>
                  {c.criterion_key}
                </option>
              ))}
            </select>
          </label>
          <label>
            Criterion points
            <input
              value={critPoints}
              onChange={(e) => setCritPoints(e.target.value)}
              inputMode="decimal"
              className="narrow"
              aria-label="Criterion points"
              data-testid="revise-criterion-points"
            />
          </label>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="reason (optional)" aria-label="Revision reason" />
          <button
            type="button"
            disabled={busy || !newTotal.trim()}
            data-testid="revise-save"
            onClick={() =>
              act("revise", {
                new_total: newTotal,
                criterion_points: critKey && critPoints.trim() ? { [critKey]: critPoints } : null,
                explanation: text,
                idempotency_key: newIdempotencyKey(),
              })
            }
          >
            Save revision
          </button>
          <span className="note">This explicit revision is the only way a draft changes.</span>
        </div>
      ) : null}
      {mode === "link" ? (
        <div className="inline-form">
          <select value={linkId} onChange={(e) => setLinkId(e.target.value)} aria-label="Existing issue">
            <option value="">choose an issue…</option>
            {linkable.map((i) => (
              <option key={i.id} value={i.id}>
                #{i.id} · {i.state} · {i.question.slice(0, 80)}
              </option>
            ))}
          </select>
          <button type="button" disabled={busy || !linkId} onClick={() => act("link_issue", { issue_id: Number(linkId) })}>
            Link
          </button>
        </div>
      ) : null}
      <ErrorBox error={error} />
    </div>
  );
}

function ArtifactsView({
  artifacts, focus, feedback, assignment, onChanged,
}: { artifacts: WorkspaceArtifact[]; focus: { artifactId?: number; lines: number[] } | null; feedback: Feedback | null;
     assignment: string; onChanged: () => void }) {
  const [active, setActive] = useState<number>(artifacts[0]?.id ?? 0);
  useEffect(() => {
    if (focus?.artifactId && artifacts.some((a) => a.id === focus.artifactId)) setActive(focus.artifactId);
  }, [focus, artifacts]);
  const cardHighlights = useMemo(() => {
    const out: Record<number, Highlight[]> = {};
    for (const card of feedback?.all_cards ?? []) {
      const { artifactId, lines } = evidenceLines(card);
      if (!artifactId) continue;
      for (const line of lines) {
        (out[artifactId] ??= []).push({ start: line, end: line, tone: "evidence", note: card.title });
      }
    }
    return out;
  }, [feedback]);
  const artifact = artifacts.find((a) => a.id === active) ?? artifacts[0];
  if (!artifact) return null;
  const focusHl: Highlight[] =
    focus && (!focus.artifactId || focus.artifactId === artifact.id)
      ? focus.lines.map((l) => ({ start: l, end: l, tone: "focus" as const }))
      : [];
  return (
    <div className="card">
      <h4>Source (exact; never executed)</h4>
      <Tabs
        tabs={artifacts.map((a) => ({
          id: a.id,
          label: `${a.aliases[0]?.relative_path ?? `artifact ${a.id}`} · ${a.role ? a.role.role : "role unknown"}`,
        }))}
        active={artifact.id}
        onChange={setActive}
      />
      <div className="split wide-left">
        <div>
          <SourceView
            text={artifact.source_text}
            highlights={[...issueHighlights(artifact.issues), ...(cardHighlights[artifact.id] ?? []), ...focusHl]}
          />
          <p className="legend">
            <span className="swatch hl-issue" /> extraction issue <span className="swatch hl-evidence" /> feedback evidence{" "}
            <span className="swatch hl-focus" /> selected
          </p>
        </div>
        <div>
          <KeyValues
            rows={[
              [
                "Inferred component",
                artifact.role ? (
                  <span key="r">
                    {artifact.role.role}{" "}
                    <Badge tone={artifact.role.status === "ambiguous" ? "warn" : artifact.role.status === "confirmed" ? "ok" : "info"}>
                      {artifact.role.status}
                    </Badge>
                  </span>
                ) : (
                  "not mapped"
                ),
              ],
              ["Role evidence (code only, never file names)", artifact.role?.evidence.join("; ") ?? "—"],
              ["Original path(s)", artifact.aliases.map((a) => a.relative_path).join(", ")],
            ]}
          />
          <IssueList issues={artifact.issues} />
          <ComponentMappingEditor key={`${artifact.id}-${artifact.role?.role}-${artifact.role?.status}`}
            artifact={artifact} assignment={assignment} onChanged={onChanged} />
          <details>
            <summary>Typed facts (application code, not a model)</summary>
            <table className="table compact">
              <tbody>
                {Object.entries(artifact.typed_facts).map(([k, v]) => (
                  <tr key={k}>
                    <th>{k}</th>
                    <td>{v === null || v === undefined ? <span className="muted">unknown</span> : String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </div>
      </div>
    </div>
  );
}

function ComponentMappingEditor({ artifact, assignment, onChanged }: {
  artifact: WorkspaceArtifact; assignment: string; onChanged: () => void;
}) {
  const [role, setRole] = useState(artifact.role?.role ?? "ambiguous");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function confirm() {
    setBusy(true); setError(null);
    try {
      await api.post(`/artifacts/${artifact.id}/component-mapping`, { role });
      onChanged();
    } catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  }
  return <div data-testid="component-mapping-editor">
    <label>Component role
      <select value={role} onChange={e => setRole(e.target.value)} data-testid="component-role">
        <option value="ambiguous">Unresolved / mixed components</option>
        {assignment.toUpperCase() === "SA3" ? <>
          <option value="program_1">Program 1</option><option value="program_2">Program 2</option>
        </> : <option value="whole_assignment">Assignment code (coverage not confirmed)</option>}
      </select>
    </label>
    <button type="button" disabled={busy} onClick={confirm} data-testid="confirm-component-role">Save component mapping</button>
    <p className="note">Confirm from the source, not the filename. Existing feedback will be refreshed; no grade changes.</p>
    <ErrorBox error={error} />
  </div>;
}

function RubricBlock({ criteria, issues }: { criteria: Criterion[]; issues: Workspace["rubric_issues"] }) {
  if (!criteria.length) {
    return (
      <div className="card">
        <h4>Rubric</h4>
        <p className="muted">No rubric linked to this assessment (content-only mode).</p>
      </div>
    );
  }
  return (
    <details className="card">
      <summary>
        <strong>Rubric criteria ({criteria.filter((c) => !c.is_container).length}) and issues ({issues.length})</strong>
      </summary>
      <table className="table compact">
        <thead>
          <tr>
            <th>Criterion</th>
            <th>Points</th>
            <th>Kind</th>
            <th>Evidence class</th>
            <th>Page</th>
          </tr>
        </thead>
        <tbody>
          {criteria.map((c) => (
            <tr key={c.criterion_key}>
              <td className="small">
                <code>{c.criterion_key}</code>
                <div>{c.title}</div>
              </td>
              <td>{c.max_points ?? "—"}</td>
              <td>{c.rule_kind}</td>
              <td>
                <Badge tone={classificationTone(c.classification)}>{c.classification}</Badge>
              </td>
              <td>{c.page}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h4>Rubric issues</h4>
      <ul className="plain-list small">
        {issues.map((i) => (
          <li key={i.id}>
            <Badge tone={i.blocks_total_proposals ? "bad" : "warn"}>{i.kind}</Badge>{" "}
            {i.blocks_total_proposals ? <Badge tone="bad">blocks total proposals</Badge> : null} {i.message}
          </li>
        ))}
      </ul>
    </details>
  );
}

function HistoryBlock({ history }: { history: DraftRevision[] }) {
  if (!history.length) return null;
  return (
    <details className="card">
      <summary>
        <strong>Revision history ({history.length})</strong>
      </summary>
      <table className="table compact">
        <thead>
          <tr>
            <th>#</th>
            <th>Total</th>
            <th>Criterion points</th>
            <th>Origin</th>
            <th>Grader label</th>
            <th>Saved</th>
          </tr>
        </thead>
        <tbody>
          {history.map((r) => (
            <tr key={r.id}>
              <td>{r.revision_number}</td>
              <td>{r.total}</td>
              <td className="small">{Object.keys(r.criterion_points).length ? JSON.stringify(r.criterion_points) : "total-only"}</td>
              <td>
                <Badge>{r.origin}</Badge>
              </td>
              <td>{r.grader_label}</td>
              <td className="small">{fmtTime(r.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
