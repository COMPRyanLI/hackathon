import { useEffect, useState } from "react";
import { ApiError, api, qs, type Assessment } from "../api";
import type { Nav } from "../App";
import { Badge, ErrorBox, KeyValues, Loading, Notice, errorText, fmtScore, fmtTime, useLoad } from "../components/common";
import { SUPPORTED_FACTS, settle, type CalibrationIssue } from "../p1api";
import { ImpactView } from "./NotesScreen";

const STATES = ["", "open", "awaiting_head_ta", "deferred", "resolved", "superseded"];

function stateTone(state: string): string {
  return ({ open: "info", awaiting_head_ta: "warn", deferred: "neutral", resolved: "ok", superseded: "neutral" } as Record<string, string>)[state] ?? "neutral";
}

export default function IssuesScreen({ assessment, nav }: { assessment: Assessment; nav: Nav }) {
  const [state, setState] = useState("");
  const list = useLoad(
    () => api.get<CalibrationIssue[]>(`/calibration-issues${qs({ assessment_id: assessment.id, state })}`),
    [assessment.id, state],
  );
  const selected = Number(nav.params.get("issue")) || null;
  const impact = Number(nav.params.get("impact")) || null;

  if (impact) {
    return (
      <ImpactView
        versionId={impact}
        backLabel="← back to shared questions"
        onBack={() => nav.go("issues", selected ? { issue: selected } : undefined)}
      />
    );
  }

  return (
    <section>
      <h2>Shared questions (head-TA queue)</h2>
      <p className="muted">
        One row per shared policy question, not one per student. Graders handle case-specific explanations; the head TA
        resolves a repeated question once and the answer is reused on matching work. Resolving never changes a grade.
      </p>
      <label className="inline">
        State{" "}
        <select value={state} onChange={(e) => setState(e.target.value)} aria-label="Filter by state">
          {STATES.map((s) => (
            <option key={s} value={s}>
              {s || "(all)"}
            </option>
          ))}
        </select>
      </label>
      <ErrorBox error={list.error} />
      <Loading show={list.loading} />
      <div className="table-wrap">
        <table className="table compact clickable" data-testid="issues-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Question</th>
              <th>Criterion · component</th>
              <th>State</th>
              <th>Affected</th>
              <th>Impact review</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {(list.data ?? []).map((i) => (
              <tr
                key={i.id}
                className={i.id === selected ? "selected" : ""}
                onClick={() => nav.go("issues", { issue: i.id })}
                data-testid={`issue-row-${i.id}`}
              >
                <td>{i.id}</td>
                <td>{i.question}</td>
                <td className="small">
                  <code>{i.criterion_key}</code> · {i.component_role ?? "—"}
                </td>
                <td>
                  <Badge tone={stateTone(i.state)}>{i.state}</Badge>
                </td>
                <td data-testid="issue-affected">{i.affected}</td>
                <td>
                  {i.resolution_note_version_id ? <button type="button" data-testid={`impact-shortcut-${i.id}`}
                    onClick={e => { e.stopPropagation(); nav.go("issues", { issue: i.id, impact: i.resolution_note_version_id! }); }}>
                    Open impact review
                  </button> : <span className="muted small">Available after guidance approval</span>}
                </td>
                <td className="small">{fmtTime(i.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {list.data && list.data.length === 0 ? <p className="muted">No shared questions yet.</p> : null}
      {selected ? (
        <IssueDetail
          key={selected}
          issueId={selected}
          others={(list.data ?? []).filter((i) => i.id !== selected)}
          nav={nav}
          onChanged={list.reload}
        />
      ) : null}
    </section>
  );
}

function IssueDetail({
  issueId, others, nav, onChanged,
}: { issueId: number; others: CalibrationIssue[]; nav: Nav; onChanged: () => void }) {
  const detail = useLoad(() => api.get<CalibrationIssue>(`/calibration-issues/${issueId}`), [issueId]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const d = detail.data;
  const open = d && ["open", "awaiting_head_ta", "deferred"].includes(d.state);

  async function act(path: string, body: Record<string, unknown>, done: string) {
    setError(null);
    setMessage(null);
    try {
      await api.post(path, { expected_version: d?.version, ...body });
      setMessage(done);
      settle(detail.reload, onChanged);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(`Conflict: ${errorText(err)} — reload the issue before acting again.`);
      } else {
        setError(errorText(err));
      }
    }
  }

  return (
    <div className="card issue-detail" data-testid="issue-detail">
      <ErrorBox error={detail.error} />
      {d ? (
        <>
          <h3>
            Issue #{d.id} <Badge tone={stateTone(d.state)}>{d.state}</Badge>
          </h3>
          {d.is_synthetic ? <Notice tone="warn">SYNTHETIC demo data.</Notice> : null}
          <p className="card-question">
            <strong>Question:</strong> {d.question}
          </p>
          <KeyValues
            rows={[
              ["Criterion", <code key="c">{d.criterion_key}</code>],
              ["Component", d.component_role ?? "—"],
              ["Issue type", d.issue_type],
              ["Affected submissions (distinct)", String(d.affected)],
              ["Context", d.context_note || "—"],
              ["Authority needed", d.authority_needed],
              ["Version (optimistic concurrency)", String(d.version)],
              ["Resolution", d.resolution_type ? `${d.resolution_type}: ${d.resolution_text} (by ${d.resolved_by})` : "—"],
            ]}
          />
          {d.resolution_note_version_id ? (
            <p>
              <button
                type="button"
                className="primary"
                data-testid="open-impact"
                onClick={() => nav.go("issues", { issue: d.id, impact: d.resolution_note_version_id! })}
              >
                Open impact review (guidance version {d.resolution_note_version_id})
              </button>{" "}
              <span className="muted small">Potential impact only; no grades were changed.</span>
            </p>
          ) : <Notice tone="info">{open
            ? "No impact scan exists yet. Complete the resolve form below and approve guidance to scan matching submissions. Requesting calibration only adds the question to the head-TA queue."
            : "This resolution did not approve reusable guidance, so it has no impact scan."}</Notice>}
          {d.guidance ? (
            <details>
              <summary>Approved typed rule</summary>
              <pre className="json">{JSON.stringify(d.guidance, null, 2)}</pre>
            </details>
          ) : null}
          <h4>Representative examples</h4>
          <div className="table-wrap">
            <table className="table compact" data-testid="issue-examples">
              <thead>
                <tr>
                  <th>Student</th>
                  <th>Imported historical total</th>
                  <th>Current draft</th>
                  <th>Evidence</th>
                  <th>Link</th>
                  <th>Local disposition</th>
                </tr>
              </thead>
              <tbody>
                {(d.examples ?? []).map((e) => (
                  <tr key={e.submission_id}>
                    <td>
                      <button type="button" className="link" onClick={() => nav.go("grading", { submission: e.submission_id })}>
                        {e.student_id}
                      </button>
                    </td>
                    <td className="small">{fmtScore(e.recorded)}</td>
                    <td>{e.draft ? `${e.draft.total} (${e.draft.grader_label})` : "—"}</td>
                    <td className="small">
                      {e.evidence.map((ev, i) => (
                        <div key={i}>
                          {ev.line ? `line ${ev.line}: ` : ""}
                          <code>{ev.quote ?? (ev.fact ? `${ev.fact}=${String(ev.actual)}` : "")}</code>
                        </div>
                      ))}
                    </td>
                    <td className="small">{e.method}</td>
                    <td className="small">{e.local_disposition ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details>
            <summary>Membership history ({d.links?.length ?? 0} links) and events</summary>
            <table className="table compact">
              <thead>
                <tr>
                  <th>Submission</th>
                  <th>Method</th>
                  <th>Active</th>
                  <th>Removed reason</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {(d.links ?? []).map((l) => (
                  <tr key={l.id} className={l.active ? "" : "dim"}>
                    <td>{l.submission_id}</td>
                    <td>{l.method}</td>
                    <td>{l.active ? "yes" : "no"}</td>
                    <td>{l.removed_reason ?? "—"}</td>
                    <td className="small">{fmtTime(l.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <ul className="plain-list small">
              {(d.events ?? []).map((ev, i) => (
                <li key={i}>
                  {fmtTime(ev.at)} · {ev.actor} · {ev.action} <code>{JSON.stringify(ev.detail)}</code>
                </li>
              ))}
            </ul>
          </details>
          <ErrorBox error={error} />
          {message ? <Notice tone="ok">{message}</Notice> : null}
          {open ? (
            <>
              <ResolveForm issue={d} onResolve={(body) => act(`/calibration-issues/${d.id}/resolve`, body, "Resolved. The impact scan and refresh of linked feedback are queued; no grade changed.")} />
              <div className="row gap wrap issue-controls">
                <MergeControl issue={d} others={others.filter((o) => ["open", "awaiting_head_ta", "deferred"].includes(o.state))}
                  onMerge={(into, intoVersion) => act(`/calibration-issues/${d.id}/merge`, { into_issue_id: into, expected_into_version: intoVersion }, `Merged into #${into}.`)} />
                <SplitControl issue={d}
                  onSplit={(ids, question) => act(`/calibration-issues/${d.id}/split`, { submission_ids: ids, question }, "Split into a new issue.")} />
                <button type="button" onClick={() => act(`/calibration-issues/${d.id}/defer`, { reason: "deferred by head TA" }, "Deferred.")}>
                  Defer
                </button>
              </div>
            </>
          ) : null}
        </>
      ) : (
        <Loading show />
      )}
    </div>
  );
}

function ResolveForm({ issue, onResolve }: { issue: CalibrationIssue; onResolve: (body: Record<string, unknown>) => void }) {
  const [type, setType] = useState("interpretation");
  const [text, setText] = useState("");
  const [kind, setKind] = useState("clarification");
  const [condition, setCondition] = useState("");
  const [exceptions, setExceptions] = useState("");
  const [treatment, setTreatment] = useState('{"action": "award", "points": "0"}');
  const [criterion, setCriterion] = useState(issue.criterion_key);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setCondition(JSON.stringify({ component: issue.component_role ?? "any", fact: "loop_inclusive_bound", op: "==", value: true }));
    setExceptions(
      JSON.stringify([
        { description: "counter starts at 1", condition: { component: issue.component_role ?? "program_1", fact: "loop_counter_start", op: "==", value: 1 } },
      ]),
    );
  }, [issue.id, issue.component_role]);

  function submit() {
    setError(null);
    try {
      const body: Record<string, unknown> = { resolution_type: type, text };
      if (type === "interpretation") {
        body.note_kind = kind;
        body.condition = JSON.parse(condition);
        body.exception_conditions = exceptions.trim() ? JSON.parse(exceptions) : [];
        body.point_treatment = treatment.trim() ? JSON.parse(treatment) : { action: "no_numeric_rule" };
        body.criterion_key = criterion;
      }
      onResolve(body);
    } catch (err) {
      setError(`Invalid JSON: ${errorText(err)}`);
    }
  }

  return (
    <div className="approve-form" data-testid="resolve-form">
      <h4>Resolve once</h4>
      <div className="form-stack">
        <label>
          Resolution type
          <select value={type} onChange={(e) => setType(e.target.value)} data-testid="resolve-type" aria-label="Resolution type">
            <option value="interpretation">interpretation (creates versioned guidance + scope-wide scan)</option>
            <option value="record_correction">record correction (no new policy)</option>
            <option value="case_specific">case-specific explanation (no new policy)</option>
          </select>
        </label>
        <label>
          Answer / interpretation
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} data-testid="resolve-text" aria-label="Resolution text" />
        </label>
        {type === "interpretation" ? (
          <>
            <label>
              Kind
              <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Note kind">
                <option value="clarification">clarification of existing expectations</option>
                <option value="prospective_policy">prospective policy change</option>
                <option value="retrospective_review">requested retrospective review</option>
              </select>
            </label>
            <label>
              Criterion key
              <input value={criterion} onChange={(e) => setCriterion(e.target.value)} aria-label="Criterion key" />
            </label>
            <label>
              Typed condition (JSON; evaluated by application code)
              <textarea value={condition} onChange={(e) => setCondition(e.target.value)} rows={2} className="mono" data-testid="resolve-condition" aria-label="Typed condition" />
              <span className="note">Supported facts: {SUPPORTED_FACTS.join(", ")}. Component: a role such as program_1, or "any".</span>
            </label>
            <label>
              Exceptions (JSON list of {"{description, condition}"}; matches stay reviewable)
              <textarea value={exceptions} onChange={(e) => setExceptions(e.target.value)} rows={3} className="mono" data-testid="resolve-exceptions" aria-label="Exception conditions" />
            </label>
            <label>
              Point treatment (JSON: award / deduct / range / no_numeric_rule)
              <input value={treatment} onChange={(e) => setTreatment(e.target.value)} className="mono" data-testid="resolve-treatment" aria-label="Point treatment" />
              <span className="note">Proposal only — original and draft grades never change automatically.</span>
            </label>
          </>
        ) : null}
        <button type="button" className="primary" onClick={submit} disabled={!text.trim()} data-testid="resolve-submit">
          Resolve (version {issue.version})
        </button>
        <ErrorBox error={error} />
      </div>
    </div>
  );
}

function MergeControl({
  issue, others, onMerge,
}: { issue: CalibrationIssue; others: CalibrationIssue[]; onMerge: (into: number, intoVersion: number) => void }) {
  const [target, setTarget] = useState("");
  if (!others.length) return null;
  const chosen = others.find((o) => o.id === Number(target));
  return (
    <span className="row gap">
      <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label={`Merge issue ${issue.id} into`}>
        <option value="">merge into…</option>
        {others.map((o) => (
          <option key={o.id} value={o.id}>
            #{o.id} {o.question.slice(0, 60)}
          </option>
        ))}
      </select>
      <button type="button" disabled={!chosen} onClick={() => chosen && onMerge(chosen.id, chosen.version)}>
        Merge
      </button>
    </span>
  );
}

function SplitControl({ issue, onSplit }: { issue: CalibrationIssue; onSplit: (ids: number[], question: string) => void }) {
  const [open, setOpen] = useState(false);
  const [ids, setIds] = useState<number[]>([]);
  const [question, setQuestion] = useState("");
  const active = (issue.links ?? []).filter((l) => l.active);
  if (active.length < 2) return null;
  return (
    <span>
      <button type="button" onClick={() => setOpen(!open)}>
        Split…
      </button>
      {open ? (
        <span className="inline-form">
          {active.map((l) => (
            <label key={l.id} className="inline">
              <input
                type="checkbox"
                checked={ids.includes(l.submission_id)}
                onChange={(e) => setIds(e.target.checked ? [...ids, l.submission_id] : ids.filter((x) => x !== l.submission_id))}
              />{" "}
              submission {l.submission_id}
            </label>
          ))}
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="question for the new issue" aria-label="Split question" />
          <button type="button" disabled={!ids.length || !question.trim()} onClick={() => onSplit(ids, question)}>
            Split selected
          </button>
        </span>
      ) : null}
    </span>
  );
}
