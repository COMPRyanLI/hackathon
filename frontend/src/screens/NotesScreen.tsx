import { useEffect, useState } from "react";
import type { Nav } from "../App";
import {
  APPLICABILITY_ACTIONS,
  ApiError,
  CONDITION_FEATURES,
  api,
  qs,
  type Assessment,
  type Impact,
  type ImpactItem,
  type NoteVersion,
} from "../api";
import { Badge, ErrorBox, JsonBlock, KeyValues, Loading, MockBadge, Notice, StatusBadge, errorText, fmtScore, fmtTime, useLoad, usePoll } from "../components/common";

const KINDS: [string, string, string][] = [
  ["clarification", "Clarification of existing expectations", "States what was already expected. Past grading can be checked against it."],
  ["prospective_policy", "Prospective policy change", "Applies going forward. Must not silently become a past grading standard; the scan is informational."],
  ["retrospective_review", "Requested retrospective review", "Asks for previously graded work to be re-examined under this note."],
];

const CONDITION_EXAMPLE = '{"feature": "loop_count", "op": ">", "value": 0}';

interface Conflict {
  note_version_id: number;
  note_id: number;
  version: number;
  interpretation: string;
}

export default function NotesScreen({ assessment, nav, onGuidanceChanged }: { assessment: Assessment; nav: Nav; onGuidanceChanged: () => void }) {
  const notes = useLoad(() => api.get<NoteVersion[]>(`/instructor-notes${qs({ assessment_id: assessment.id })}`), [assessment.id]);
  const impactId = Number(nav.params.get("impact")) || null;
  const fromCase = Number(nav.params.get("from_case")) || null;
  const [editing, setEditing] = useState<number | null>(null);

  const groups = new Map<number, NoteVersion[]>();
  for (const v of notes.data ?? []) groups.set(v.note_id, [...(groups.get(v.note_id) ?? []), v]);

  const refresh = () => {
    notes.reload();
    onGuidanceChanged();
  };

  if (impactId) {
    return <ImpactView versionId={impactId} onBack={() => nav.go("notes")} />;
  }

  return (
    <section>
      <h2>
        Instructor notes <Loading show={notes.loading} />
      </h2>
      <p className="muted">
        comment → draft interpretation → explicit approval → guidance version → scope-wide applicability scan → human
        decisions. Comments and drafts have no effect. Approval never changes a grade.
      </p>
      <ErrorBox error={notes.error} />
      <CreateNote assessmentId={assessment.id} sourceCaseId={fromCase} onCreated={(id) => { refresh(); setEditing(id); }} />
      {groups.size === 0 ? <p className="muted">No notes yet.</p> : null}
      {[...groups.entries()].map(([noteId, versions]) => (
        <div key={noteId} className="card">
          <h3>Note {noteId}</h3>
          {versions.map((v) => (
            <div key={v.id} className="note-version">
              <div className="row gap wrap">
                <strong>v{v.version}</strong>
                <StatusBadge status={v.status} />
                {v.note_kind ? <Badge tone="info">{v.note_kind}</Badge> : null}
                {v.source_case_id ? <span className="muted small">from case {v.source_case_id}</span> : null}
                {v.supersedes_version_id ? <span className="muted small">supersedes version id {v.supersedes_version_id}</span> : null}
                <span className="muted small">
                  {v.actor} · {fmtTime(v.created_at)}
                  {v.approved_at ? ` · approved by ${v.approved_by} ${fmtTime(v.approved_at)}` : ""}
                  {v.superseded_at ? ` · superseded ${fmtTime(v.superseded_at)}` : ""}
                </span>
              </div>
              <KeyValues
                rows={[
                  ["Original text", v.original_text],
                  ["Interpretation", v.interpretation || <span className="muted">(none yet)</span>],
                  ["Scope", [v.scope_component, v.scope_requirement].filter(Boolean).join(" / ") || "whole assessment"],
                  ["Applicability conditions", v.applicability_conditions ? <code>{JSON.stringify(v.applicability_conditions)}</code> : "none (manual or model scan)"],
                  ["Exceptions", v.exceptions || "—"],
                  ["Scoring implication", v.scoring_implication],
                  ["Coexists with", v.coexists_with.length ? v.coexists_with.join(", ") : "—"],
                ]}
              />
              <div className="row gap">
                {(v.status === "comment" || v.status === "draft") && (
                  <button type="button" onClick={() => setEditing(editing === v.id ? null : v.id)}>
                    {editing === v.id ? "Close" : "Edit / approve"}
                  </button>
                )}
                {(v.status === "approved" || v.status === "superseded") && (
                  <button type="button" onClick={() => nav.go("notes", { impact: v.id })}>
                    Impact review
                  </button>
                )}
                {v.status === "approved" && <SupersedeButton version={v} onDone={(id) => { refresh(); setEditing(id); }} />}
              </div>
              {editing === v.id ? (
                <ApproveForm
                  version={v}
                  isSynthetic={assessment.is_synthetic}
                  onSaved={refresh}
                  onApproved={(id) => {
                    refresh();
                    setEditing(null);
                    nav.go("notes", { impact: id });
                  }}
                  onSupersedeInstead={(conflictId) => {
                    setEditing(null);
                    void api
                      .post<NoteVersion>(`/instructor-notes/${conflictId}/supersede`, {
                        interpretation: v.interpretation,
                        original_text: v.original_text,
                      })
                      .then((successor) => {
                        refresh();
                        setEditing(successor.id);
                      })
                      .catch((err) => alert(errorText(err)));
                  }}
                />
              ) : null}
            </div>
          ))}
        </div>
      ))}
    </section>
  );
}

function CreateNote({ assessmentId, sourceCaseId, onCreated }: { assessmentId: number; sourceCaseId: number | null; onCreated: (id: number) => void }) {
  const [text, setText] = useState("");
  const [caseId, setCaseId] = useState<string>(sourceCaseId ? String(sourceCaseId) : "");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setCaseId(sourceCaseId ? String(sourceCaseId) : ""), [sourceCaseId]);
  async function create() {
    setError(null);
    try {
      const v = await api.post<NoteVersion>("/instructor-notes", {
        assessment_id: assessmentId,
        original_text: text,
        source_case_id: caseId.trim() ? Number(caseId) : null,
      });
      setText("");
      onCreated(v.id);
    } catch (err) {
      setError(errorText(err));
    }
  }
  return (
    <form
      className="card form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        create();
      }}
    >
      <h3>New comment</h3>
      <label>
        Comment from the instructor (original wording is preserved)
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <label>
        Source case id (optional)
        <input value={caseId} onChange={(e) => setCaseId(e.target.value)} className="narrow" />
      </label>
      <div>
        <button type="submit" disabled={!text.trim()}>
          Save comment
        </button>
      </div>
      <ErrorBox error={error} />
    </form>
  );
}

function SupersedeButton({ version, onDone }: { version: NoteVersion; onDone: (id: number) => void }) {
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <button
        type="button"
        onClick={async () => {
          setError(null);
          try {
            const successor = await api.post<NoteVersion>(`/instructor-notes/${version.id}/supersede`, {});
            onDone(successor.id);
          } catch (err) {
            setError(errorText(err));
          }
        }}
      >
        Draft a superseding version
      </button>
      <ErrorBox error={error} />
    </>
  );
}

function ApproveForm({
  version, isSynthetic, onSaved, onApproved, onSupersedeInstead,
}: {
  version: NoteVersion;
  isSynthetic: boolean;
  onSaved: () => void;
  onApproved: (id: number) => void;
  onSupersedeInstead: (conflictVersionId: number) => void;
}) {
  const [form, setForm] = useState({
    original_text: version.original_text,
    interpretation: version.interpretation,
    note_kind: version.note_kind ?? "",
    scope_component: version.scope_component,
    scope_requirement: version.scope_requirement,
    exceptions: version.exceptions,
    rationale: version.rationale,
    scoring_implication: version.scoring_implication || "no_numeric_rule",
    conditions: version.applicability_conditions ? JSON.stringify(version.applicability_conditions) : "",
  });
  const [useModel, setUseModel] = useState(false);
  const [providerMode, setProviderMode] = useState<"configured" | "mock">("configured");
  const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function fields() {
    let conditions: unknown = null;
    if (form.conditions.trim()) {
      try {
        conditions = JSON.parse(form.conditions);
      } catch {
        throw new Error("Applicability conditions must be valid JSON (or left empty).");
      }
    }
    return {
      original_text: form.original_text,
      interpretation: form.interpretation,
      note_kind: form.note_kind || null,
      scope_component: form.scope_component,
      scope_requirement: form.scope_requirement,
      exceptions: form.exceptions,
      rationale: form.rationale,
      scoring_implication: form.scoring_implication,
      applicability_conditions: conditions,
    };
  }

  async function saveDraft() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.patch(`/instructor-notes/${version.id}`, fields());
      setMessage("Draft saved. Drafts have no effect until approved.");
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function approve(coexists: number[] = []) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await api.post(`/instructor-notes/${version.id}/approve`, {
        ...fields(),
        coexists_with: coexists.length ? coexists : null,
        use_model_scan: useModel,
        provider_mode: providerMode,
      });
      setConflicts(null);
      onApproved(version.id);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && err.detail && typeof err.detail === "object") {
        setConflicts((err.detail as { conflicts?: Conflict[] }).conflicts ?? []);
        setError((err.detail as { message?: string }).message ?? err.message);
      } else {
        setError(errorText(err));
      }
    } finally {
      setBusy(false);
    }
  }

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm({ ...form, [key]: e.target.value });

  return (
    <form
      className="form-stack approve-form"
      onSubmit={(e) => {
        e.preventDefault();
        approve();
      }}
    >
      <label>
        Original comment
        <textarea rows={2} value={form.original_text} onChange={set("original_text")} />
      </label>
      <label>
        Approved interpretation (required)
        <textarea rows={3} value={form.interpretation} onChange={set("interpretation")} />
      </label>
      <fieldset>
        <legend>What kind of note is this? (required)</legend>
        {KINDS.map(([value, label, help]) => (
          <label key={value} className="radio-block">
            <input type="radio" name={`kind-${version.id}`} checked={form.note_kind === value} onChange={() => setForm({ ...form, note_kind: value })} />
            <span>
              <strong>{label}</strong>
              <span className="muted small"> — {help}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="row gap wrap">
        <label>
          Scope: component (optional)
          <input value={form.scope_component} onChange={set("scope_component")} />
        </label>
        <label>
          Scope: requirement (optional)
          <input value={form.scope_requirement} onChange={set("scope_requirement")} />
        </label>
      </div>
      <label>
        Exceptions
        <textarea rows={2} value={form.exceptions} onChange={set("exceptions")} />
      </label>
      <label>
        Rationale
        <textarea rows={2} value={form.rationale} onChange={set("rationale")} />
      </label>
      <label>
        Scoring implication (no_numeric_rule unless the instructor specifies a rule)
        <input value={form.scoring_implication} onChange={set("scoring_implication")} />
      </label>
      <label>
        Applicability conditions (optional JSON, deterministic). Example: <code>{CONDITION_EXAMPLE}</code>
        <textarea rows={3} className="mono" value={form.conditions} onChange={set("conditions")} placeholder={CONDITION_EXAMPLE} />
      </label>
      <p className="muted small">
        Supported: <code>{"{feature, op, value}"}</code> with op ==, !=, &lt;, &lt;=, &gt;, &gt;=;{" "}
        <code>{'{"source_regex": "...", "present": true}'}</code>; combine with <code>all</code>, <code>any</code>, <code>not</code>.
        Features: {CONDITION_FEATURES.join(", ")}. An unavailable feature (for example after a parse failure) yields
        "uncertain".
      </p>
      <div className="row gap wrap">
        <label className="inline">
          <input type="checkbox" checked={useModel} onChange={(e) => setUseModel(e.target.checked)} /> Run a model check when no
          condition is given, or scope and exceptions need interpretation
        </label>
        <label>
          Providers for the scan
          <select value={providerMode} onChange={(e) => setProviderMode(e.target.value as "configured" | "mock")}>
            <option value="configured">Configured providers</option>
            <option value="mock" disabled={!isSynthetic}>
              MOCK — labeled outputs, not real findings{isSynthetic ? "" : " (synthetic data only)"}
            </option>
          </select>
        </label>
      </div>
      <div className="row gap">
        <button type="button" onClick={saveDraft} disabled={busy}>
          Save draft
        </button>
        <button type="submit" className="primary" disabled={busy}>
          Approve and scan every in-scope submission
        </button>
      </div>
      <ErrorBox error={error} />
      {message ? <Notice tone="ok">{message}</Notice> : null}
      {conflicts ? (
        <div className="notice notice-warn">
          <p>Overlapping approved guidance exists. Resolve it explicitly:</p>
          <ul className="plain-list">
            {conflicts.map((c) => (
              <li key={c.note_version_id}>
                Note {c.note_id} v{c.version}: {c.interpretation}{" "}
                <button type="button" className="link" onClick={() => onSupersedeInstead(c.note_version_id)}>
                  Supersede note {c.note_id} instead
                </button>
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => approve(conflicts.map((c) => c.note_version_id))} disabled={busy}>
            Confirm both apply and approve
          </button>
        </div>
      ) : null}
    </form>
  );
}

export function ImpactView({ versionId, onBack, backLabel = "← back to notes" }: { versionId: number; onBack: () => void; backLabel?: string }) {
  const impact = useLoad(() => api.get<Impact>(`/instructor-notes/${versionId}/impact`), [versionId]);
  const scanActive = !!impact.data?.scan_run && ["queued", "running"].includes(impact.data.scan_run.status);
  usePoll(scanActive, impact.reload);
  const [onlyApplies, setOnlyApplies] = useState(false);
  const d = impact.data;
  const coverage = (d?.scan_run?.coverage ?? {}) as Record<string, unknown>;
  return (
    <section>
      <button type="button" className="link" onClick={onBack}>
        {backLabel}
      </button>
      <ErrorBox error={impact.error} />
      <Loading show={impact.loading} />
      {d ? (
        <>
          <h2>
            Impact review · note {d.note.note_id} v{d.note.version} <StatusBadge status={d.note.status} />
          </h2>
          <Notice tone="info">{d.statement || "Potential impact only. No grades were changed."}</Notice>
          {d.note.note_kind === "prospective_policy" ? (
            <Notice tone="warn">Prospective policy: these results are informational and do not establish a past grading standard.</Notice>
          ) : null}
          <KeyValues
            rows={[
              ["Interpretation", d.note.interpretation],
              ["Kind", d.note.note_kind],
              ["Conditions", d.note.applicability_conditions ? <code>{JSON.stringify(d.note.applicability_conditions)}</code> : "none"],
              ["Scan run", d.scan_run ? <span>#{d.scan_run.id} <StatusBadge status={d.scan_run.status} /></span> : "—"],
              ["Status counts", Object.entries(d.status_counts).map(([k, v]) => `${k}: ${v}`).join(" · ")],
            ]}
          />
          {Array.isArray(coverage.notes) ? (
            <ul className="plain-list small">
              {(coverage.notes as string[]).map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          ) : null}
          <label className="inline">
            <input type="checkbox" checked={onlyApplies} onChange={(e) => setOnlyApplies(e.target.checked)} /> Show only "applies" and
            "uncertain"
          </label>
          <div className="table-wrap">
            <table className="table compact">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Student</th>
                  <th>Recorded score · grader</th>
                  <th>Status</th>
                  <th>Method</th>
                  <th>Previously flagged</th>
                  <th>Rationale & evidence</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {d.items
                  .filter((i) => !onlyApplies || i.status !== "does_not_apply")
                  .map((item) => (
                    <ImpactRow key={item.id} item={item} onSaved={impact.reload} />
                  ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">Highlighted rows: the note applies to work that was not previously flagged by any review case.</p>
        </>
      ) : null}
    </section>
  );
}

function ImpactRow({ item, onSaved }: { item: ImpactItem; onSaved: () => void }) {
  const [action, setAction] = useState<string>("confirm_applies");
  const [comment, setComment] = useState("");
  const [score, setScore] = useState("");
  const [error, setError] = useState<string | null>(null);
  const highlight = item.status === "applies" && !item.previously_flagged;
  async function save() {
    setError(null);
    try {
      await api.post(`/note-applicability/${item.id}/decisions`, {
        action,
        comment,
        proposed_score: score.trim() ? Number(score) : null,
      });
      setComment("");
      setScore("");
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  }
  return (
    <tr className={`${highlight ? "highlight" : ""} ${item.is_current ? "" : "dim"}`} data-testid={`impact-row-${item.student_id}`}>
      <td>{item.priority_rank}</td>
      <td>{item.student_id}</td>
      <td>{fmtScore(item.score)}</td>
      <td>
        <StatusBadge status={item.status} />
        <MockBadge show={item.is_mock} />
        {!item.is_current ? <Badge tone="warn" title={item.stale_reason ?? ""}>historical</Badge> : null}
      </td>
      <td className="small">{item.method}</td>
      <td>{item.previously_flagged ? "yes" : <strong>no</strong>}</td>
      <td className="small">
        <div>{item.rationale}</div>
        {item.evidence.length ? (
          <details>
            <summary>evidence ({item.evidence.length})</summary>
            {item.evidence.map((e, i) => (
              <EvidenceLine key={i} e={e} />
            ))}
          </details>
        ) : null}
      </td>
      <td className="small">
        {item.decisions.map((dec) => (
          <div key={dec.id}>
            <Badge tone="ok">{dec.action}</Badge> {dec.comment}
            {dec.proposed_score !== null ? ` · proposed (human): ${dec.proposed_score}` : ""}
          </div>
        ))}
        <div className="decision-inline">
          <select value={action} onChange={(e) => setAction(e.target.value)} aria-label={`Decision for ${item.student_id}`}>
            {APPLICABILITY_ACTIONS.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
          <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="comment" aria-label="Comment" />
          <input
            value={score}
            onChange={(e) => setScore(e.target.value)}
            placeholder="proposal only"
            aria-label="Proposed score (proposal only)"
            className="narrow"
            inputMode="decimal"
          />
          <button type="button" onClick={save}>
            Save
          </button>
        </div>
        <ErrorBox error={error} />
      </td>
    </tr>
  );
}

function EvidenceLine({ e }: { e: Record<string, unknown> }) {
  if (Array.isArray(e.matches)) {
    const matches = e.matches as { line: number; quote: string }[];
    return (
      <div className="evidence-line">
        regex <code>{String(e.source_regex)}</code> present={String(e.present)} (expected {String(e.expected_present)})
        {matches.map((m, i) => (
          <div key={i}>
            line {m.line}: <code className="pre">{m.quote}</code>
          </div>
        ))}
      </div>
    );
  }
  if ("feature" in e) {
    return (
      <div className="evidence-line">
        {String(e.feature)}: {e.status === "unavailable" ? `unavailable (${String(e.reason)})` : `${String(e.actual)} ${String(e.op)} ${String(e.expected)} → ${String(e.result)}`}
        <span className="muted"> · artifact {String(e.artifact_id)}</span>
      </div>
    );
  }
  if (Array.isArray(e.evidence_refs)) {
    return (
      <div className="evidence-line">
        {String(e.text ?? "")}
        {(e.evidence_refs as { line_start: number; quote: string }[]).map((r, i) => (
          <div key={i}>
            line {r.line_start}: <code className="pre">{r.quote}</code>
          </div>
        ))}
      </div>
    );
  }
  return <JsonBlock value={e} />;
}
