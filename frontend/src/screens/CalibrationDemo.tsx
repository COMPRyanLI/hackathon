import { useState } from "react";
import type { Nav, Screen } from "../App";
import { api, type Assessment, type Impact, type ImpactItem } from "../api";
import { Badge, ErrorBox, Notice, SourceView, errorText, useLoad, usePoll } from "../components/common";
import { newIdempotencyKey, type CalibrationIssue, type QueueRow, type SaveDraftResult, type Workspace } from "../p1api";

// Presentation of the existing P1 APIs, restricted to the known synthetic SA6 fixture.
// The server remains authoritative for conditions, applicability, grades and audit history.
const CONDITION = { component: "whole_assignment", fact: "diagonal_check_present", op: "==", value: false };
const EXCEPTIONS = [{ description: "Backtracking also missing: review interacting failures individually.",
  condition: { component: "whole_assignment", fact: "backtracking_present", op: "==", value: false } }];
const TYPE = "sa6_partial_credit_missing_diagonal";

export default function CalibrationDemo({ assessment, nav, step, onImported }: {
  assessment: Assessment | null; nav: Nav; step: Screen; onImported: (id: number) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const valid = assessment?.is_synthetic && assessment.dataset_key === "synthetic-p1" && assessment.name === "SA6";
  async function load() {
    setBusy(true); setError(null);
    try {
      const data = await api.post<{ assessments: { SA6: { assessment_id: number } } }>("/demo/p1");
      onImported(data.assessments.SA6.assessment_id); nav.go("grading");
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  if (!valid || !assessment) return <section className="calibration-demo">
    <h2>One question. A shared partial-credit standard.</h2>
    <div className="card"><p>Follow an invented N-Queens grading example: different correctness scores, one head-TA answer, and a human-reviewed revision.</p>
      <button className="primary" disabled={busy} onClick={load}>Load calibration example</button>
      <p className="muted">Uses the existing synthetic P1 fixture. This does not reset prior demo decisions.</p>
      <ErrorBox error={error} /></div>
  </section>;
  return <CalibrationFlow assessment={assessment} nav={nav} step={step} />;
}

function CalibrationFlow({ assessment, nav, step }: { assessment: Assessment; nav: Nav; step: Screen }) {
  const queue = useLoad(() => api.get<QueueRow[]>(`/assessments/${assessment.id}/grading-queue`), [assessment.id]);
  const issues = useLoad(() => api.get<CalibrationIssue[]>(`/calibration-issues?assessment_id=${assessment.id}`), [assessment.id]);
  const issue = issues.data?.find(i => i.issue_type === TYPE && i.state !== "superseded");
  const detail = useLoad(() => issue ? api.get<CalibrationIssue>(`/calibration-issues/${issue.id}`) : Promise.resolve(null), [issue?.id, issue?.version, issue?.affected]);
  const selected = Number(nav.params.get("submission")) || queue.data?.find(r => r.student_id === "P007")?.submission_id;
  const workspace = useLoad(() => selected ? api.get<Workspace>(`/submissions/${selected}/workspace`) : Promise.resolve(null), [selected]);
  const impact = useLoad(() => issue?.resolution_note_version_id
    ? api.get<Impact>(`/instructor-notes/${issue.resolution_note_version_id}/impact`) : Promise.resolve(null), [issue?.resolution_note_version_id]);
  const refresh = () => { queue.reload(); issues.reload(); workspace.reload(); detail.reload(); impact.reload(); };
  const [awaitRevision, setAwaitRevision] = useState<number | null>(null);
  const pending = !!queue.data?.some(r => ["queued", "running", "stale"].includes(r.feedback_status ?? ""))
    || (!!awaitRevision && workspace.data?.current_revision?.id !== awaitRevision)
    || ["queued", "running"].includes(impact.data?.scan_run?.status ?? "");
  usePoll(pending, refresh, 1000);
  const ws = workspace.data;
  const criterion = ws?.criteria.find(c => c.criterion_key.includes("correctness") && c.max_points === "6");
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function act(fn: () => Promise<void>) {
    setBusy(true); setError(null); setMessage("");
    try { await fn(); refresh(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  const cards = ws?.feedback?.is_current ? ws.feedback.all_cards : [];
  const question = cards?.find(c => c.type === "calibration_question" && c.title.includes("no diagonal"));
  const guidance = cards?.find(c => c.guidance_version_id === issue?.resolution_note_version_id && c.guidance_version_id != null);
  const d = detail.data;
  const resolved = issue?.state === "resolved";
  const targets = (queue.data ?? []).filter(r => ["P002", "P004", "P007"].includes(r.student_id));

  return <section className="calibration-demo">
    <p className="muted small">Synthetic walkthrough · scripted model analysis and real backend rules · real submissions still need individual applicability review.</p>
    <ErrorBox error={error || queue.error || issues.error || detail.error || workspace.error || impact.error} />
    {message && <Notice tone="ok">{message}</Notice>}
    {(step === "grading" || !["issues", "notes", "prerelease"].includes(step)) && <>
      <h2>Different graders. The same partial-credit question.</h2>
      <p>How much of the <strong>6-point correctness criterion</strong> should a solution receive when diagonal conflict checks are missing?</p>
      <div className="calibration-grid">
        {targets.map(row => <button key={row.submission_id} className={`card calibration-score ${selected === row.submission_id ? "selected" : ""}`}
          onClick={() => nav.go("grading", { submission: row.submission_id })}>
          <span>{row.student_id} · {row.draft?.grader_label || "Not graded yet"}</span>
          <strong>{criterion ? row.draft?.criterion_points[criterion.criterion_key] ?? "—" : "—"}<small> / 6</small></strong>
          <span>Draft correctness · total {row.draft?.total ?? "not saved"}</span>
        </button>)}
      </div>
      {ws && criterion && <div className="calibration-columns">
        <div className="card">
          <h3>{ws.student_id} · grader draft</h3>
          <GradeForm key={`${ws.submission_id}-${ws.current_revision?.id ?? 0}`} ws={ws} criterionKey={criterion.criterion_key} busy={busy}
            save={(total, points, grader) => act(async () => {
              const result = await api.post<SaveDraftResult>(`/submissions/${ws.submission_id}/draft-grades`, {
                total, grader_label: grader, expected_revision_id: ws.current_revision?.id ?? null,
                criterion_points: { ...ws.current_revision?.criterion_points, [criterion.criterion_key]: points },
                idempotency_key: newIdempotencyKey(), note: "Synthetic calibration walkthrough: grader-entered draft." });
              setAwaitRevision(result.revision.id); setMessage(`Draft saved · revision ${result.revision.revision_number}. Feedback runs in the background.`);
            })} />
          <details><summary>View submission and rubric</summary>
            <blockquote>{criterion.source_quote}</blockquote>
            {ws.artifacts.map(a => <SourceView key={a.id} text={a.source_text} />)}
          </details>
        </div>
        <div className="card" data-testid="calibration-feedback">
          <h3>{resolved ? "Approved guidance is reused" : "Feedback without an invented score"}</h3>
          {!ws.current_revision ? <p>Save a draft to check it against the rubric and existing questions.</p> :
            !ws.feedback?.is_current ? <p>Checking this revision…</p> : <>
              {question && <><p>{question.title}</p><p className="muted">A possible missing check is a reason for review, not proof of a defect. No point recommendation exists until the head TA approves a scale.</p></>}
              {guidance && <><p>{guidance.body}</p><p><strong>No new head-TA question is needed.</strong></p></>}
              {question?.issue && !resolved && <div className="callout"><strong>Already linked to shared question #{question.issue.id}</strong><p>One answer can cover these matching cases.</p></div>}
              {question && !question.issue && <button className="primary" disabled={busy} onClick={() => act(async () => {
                await api.post(`/feedback-cards/${question.id}/dispositions`, { action: "request_calibration", revision_id: question.revision_id });
                setMessage("Shared question requested. Matching existing cases are linked once.");
              })}>Ask head TA once</button>}
              {!question && !guidance && <p>No matching calibration card yet. Review the full workspace for coverage and other issues.</p>}
            </>}
          {issue && <button className="primary" onClick={() => nav.go("issues")}>View shared question · {issue.affected} submissions</button>}
          <p className="muted small">Other criteria remain unresolved when starter code or required-file evidence is missing.</p>
        </div>
      </div>}
    </>}
    {step === "issues" && <>
      <h2>Ask the head TA once.</h2>
      {!d ? <Notice>Request calibration from a feedback card first.</Notice> : <>
        <div className="callout"><strong>{d.affected} submissions · 1 shared question</strong><p>{d.question}</p></div>
        <div className="calibration-columns">
          <div className="card"><h3>Grader decisions to calibrate</h3>
            <table className="table"><thead><tr><th>Student</th><th>Grader</th><th>Correctness / 6</th></tr></thead><tbody>
              {(d.examples ?? []).map(e => <tr key={e.submission_id}><td>{e.student_id}</td><td>{e.draft?.grader_label ?? "—"}</td><td>{e.draft?.criterion_points[d.criterion_key] ?? "Not recorded"}</td></tr>)}
            </tbody></table>
            <p className="muted">Grouped by the same assignment, criterion and issue—not by score alone.</p>
            <details><summary>Review source evidence</summary><ExampleEvidence examples={d.examples ?? []} /><p>Absence of a check has no single source line to quote. This is a known synthetic fixture; real cases require applicability review.</p></details>
          </div>
          <div className="card">{resolved ? <><h3>Answer approved</h3><p>{d.resolution_text}</p><button className="primary" onClick={() => nav.go("notes")}>See where the answer applies →</button></> :
            <AnchorForm busy={busy} approve={(min, max, text) => act(async () => {
              await api.post(`/calibration-issues/${d.id}/resolve`, { expected_version: d.version,
                resolution_type: "interpretation", note_kind: "clarification", text,
                condition: CONDITION, exception_conditions: EXCEPTIONS,
                point_treatment: { action: "range", min, max }, criterion_key: d.criterion_key,
                is_anchor: true, anchor_examples: (d.examples ?? []).slice(0, 1).map(e => ({ submission_id: e.submission_id, kind: "positive" })),
                provider_mode: "mock" });
              nav.go("notes");
            })} />}
          </div>
        </div>
      </>}
    </>}
    {step === "notes" && <>
      <h2>One answer, applied consistently.</h2>
      {!resolved ? <Notice>Approve a shared answer first. Draft answers have no effect.</Notice> : !impact.data ? <p>Loading impact scan…</p> : <>
        <div className="callout"><strong>{d?.resolution_text ?? issue?.resolution_text}</strong><p>Criterion guidance only. Every original grade and grader draft remains unchanged.</p></div>
        <p>Scan: {impact.data.scan_run?.status ?? "not started"}. {impact.data.items.filter(i => i.is_current && i.status === "applies").length} matching submissions. Other patterns stay separate.</p>
        <div className="card table-wrap"><table className="table" data-testid="calibration-impact"><thead><tr><th>Student</th><th>Draft correctness / 6</th><th>Guidance proposal / 6</th><th>Result</th></tr></thead><tbody>
          {impact.data.items.map(item => <tr key={item.id}><td>{item.student_id}</td><td>{queue.data?.find(r => r.submission_id === item.submission_id)?.draft?.criterion_points[issue!.criterion_key] ?? "Not recorded"}</td><td>{proposalText(item)}</td><td>{!item.is_current ? "Outdated · review again" : item.status === "applies" ? "Matches approved condition" : item.status === "does_not_apply" ? "Outside this rule" : "Individual review"}</td></tr>)}
        </tbody></table></div>
        <p className="muted">An incomplete diagonal check is a different issue. If backtracking is also missing, the approved exception requires individual review. Missing external evidence still stays unresolved.</p>
        <button className="primary" onClick={() => nav.go("prerelease", { submission: selected! })}>Review a grade change →</button>
      </>}
    </>}
    {step === "prerelease" && <>
      <h2>The grader makes the final change.</h2>
      {!resolved || !ws || !criterion ? <Notice>Approve the shared guidance and select a submission first.</Notice> : <div className="calibration-columns">
        <div className="card"><h3>{ws.student_id} · current draft</h3>
          <p className="calibration-number">{ws.current_revision?.criterion_points[criterion.criterion_key] ?? "—"}<small> / 6 correctness</small></p>
          <p>Assignment total: {ws.current_revision?.total ?? "—"}. Imported total: {ws.recorded?.score ?? "blank"} (unchanged).</p>
          <p>{guidance?.body ?? "Feedback refresh is pending. No grade has been changed by approval."}</p>
          {ws.current_revision && <Badge tone="info">Draft revision {ws.current_revision.revision_number}</Badge>}
          <details><summary>Revision history</summary>{ws.history.map(r => <p key={r.id}>Revision {r.revision_number}: {r.criterion_points[criterion.criterion_key] ?? "—"}/6 correctness · total {r.total} · {r.grader_label}</p>)}</details>
        </div>
        <div className="card"><h3>Record your decision</h3>
          <p>Choose a criterion score and enter the revised total yourself. Guidance approval never edits either value.</p>
          <GradeForm key={`${ws.submission_id}-${ws.current_revision?.id ?? 0}`} ws={ws} criterionKey={criterion.criterion_key} busy={busy} revise
            save={(total, points, grader) => act(async () => {
              if (!guidance || !ws.feedback?.is_current) throw new Error("Wait for current guidance feedback before revising.");
              await api.post(`/feedback-cards/${guidance.id}/dispositions`, { action: "revise", revision_id: guidance.revision_id,
                new_total: total, criterion_points: { ...ws.current_revision?.criterion_points, [criterion.criterion_key]: points },
                grader_label: grader, explanation: "Grader reviewed the approved partial-credit anchor and chose this score.", idempotency_key: newIdempotencyKey() });
              setMessage("Your revision was recorded. The original grade is preserved; matching drafts reuse the approved answer.");
            })} />
          <a href={`/#/grading?submission=${ws.submission_id}`}>Open full workspace for exceptions or other criteria</a>
        </div>
      </div>}
    </>}
  </section>;
}

function GradeForm({ ws, criterionKey, busy, save, revise = false }: {
  ws: Workspace; criterionKey: string; busy: boolean; revise?: boolean;
  save: (total: string, points: string, grader: string) => void;
}) {
  const [total, setTotal] = useState(ws.current_revision?.total ?? "");
  const [points, setPoints] = useState(ws.current_revision?.criterion_points[criterionKey] ?? "");
  const [grader, setGrader] = useState(ws.current_revision?.grader_label ?? "Synthetic Grader C");
  return <form className="form-stack" onSubmit={e => { e.preventDefault(); save(total, points, grader); }}>
    <label>Correctness points (out of 6)<input aria-label="Correctness points" type="number" min="0" max="6" step="0.5" required value={points} onChange={e => setPoints(e.target.value)} /></label>
    <label>Assignment total (out of 10)<input aria-label="Assignment total" type="number" min="0" max="10" step="0.5" required value={total} onChange={e => setTotal(e.target.value)} /></label>
    <label>Grader label<input aria-label="Grader label" required value={grader} onChange={e => setGrader(e.target.value)} /></label>
    <button className="primary" disabled={busy}>{revise ? "Confirm my revision" : "Save draft & check"}</button>
  </form>;
}

function AnchorForm({ busy, approve }: { busy: boolean; approve: (min: string, max: string, text: string) => void }) {
  const [min, setMin] = useState(""); const [max, setMax] = useState("");
  const [reason, setReason] = useState(""); const [confirmed, setConfirmed] = useState(false);
  return <form className="form-stack" onSubmit={e => { e.preventDefault(); approve(min, max,
    `Missing diagonal checks with otherwise working search: ${min}–${max} of the 6 correctness points. ${reason}`); }}>
    <h3>Head TA: set the partial-credit range</h3>
    <p>For confirmed missing diagonal checks with otherwise working search. Review separately if backtracking is also missing.</p>
    <div className="row gap"><label>Minimum / 6<input aria-label="Minimum points" type="number" required min="0" max={max || "6"} step="0.5" value={min} onChange={e => setMin(e.target.value)} /></label>
      <label>Maximum / 6<input aria-label="Maximum points" type="number" required min={min || "0"} max="6" step="0.5" value={max} onChange={e => setMax(e.target.value)} /></label></div>
    <label>Reason for this range<textarea aria-label="Reason for range" required value={reason} onChange={e => setReason(e.target.value)} rows={2} /></label>
    <label className="inline"><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> I reviewed the examples and confirm this clarifies the expected grading standard.</label>
    <details><summary>Scope and technical rule</summary><p>SA6 · correctness only · synthetic fixture. This form records a clarification, not a prospective policy change.</p><pre className="json">{JSON.stringify({ condition: CONDITION, exceptions: EXCEPTIONS }, null, 2)}</pre></details>
    <button className="primary" disabled={busy || !confirmed}>Approve range & check matching work</button>
    <p className="muted small">One versioned answer. No automatic grade changes.</p>
  </form>;
}

function proposalText(item: ImpactItem): string {
  if (!item.is_current || item.status !== "applies") return "—";
  for (const ev of item.evidence) {
    const proposal = ev.proposal as { kind?: string; min?: string; max?: string } | undefined;
    if (proposal?.kind === "range") return `${proposal.min}–${proposal.max}`;
  }
  return "No numeric proposal";
}

function ExampleEvidence({ examples }: { examples: NonNullable<CalibrationIssue["examples"]> }) {
  const [chosen, setChosen] = useState<number | null>(null);
  const selected = examples.find(e => e.submission_id === chosen) ?? examples[0];
  const source = useLoad(() => selected ? api.get<Workspace>(`/submissions/${selected.submission_id}/workspace`) : Promise.resolve(null), [selected?.submission_id]);
  return <div><label>Student evidence <select aria-label="Student evidence" value={selected?.submission_id ?? ""} onChange={e => setChosen(Number(e.target.value))}>
    {examples.map(e => <option key={e.submission_id} value={e.submission_id}>{e.student_id}</option>)}
  </select></label><ErrorBox error={source.error} />
    {source.data?.criteria.filter(c => c.criterion_key.includes("correctness") && c.max_points === "6").map(c => <blockquote key={c.criterion_key}>{c.source_quote}</blockquote>)}
    {source.data?.artifacts.map(a => <SourceView key={a.id} text={a.source_text} label={`${source.data!.student_id} source`} />)}
  </div>;
}
