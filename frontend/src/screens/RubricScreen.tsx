import { useEffect, useMemo, useState } from "react";
import { api, type Assessment } from "../api";
import { Badge, ErrorBox, KeyValues, Loading, Notice, errorText, useLoad } from "../components/common";
import { classificationTone, type AssessmentRubric, type Criterion, type RubricDetail, type RubricSummary } from "../p1api";

export default function RubricScreen({ assessment }: { assessment: Assessment }) {
  const list = useLoad(() => api.get<RubricSummary[]>("/rubrics"), []);
  const linked = useLoad(() => api.get<AssessmentRubric>(`/assessments/${assessment.id}/rubric`), [assessment.id]);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const selectedRubric = list.data?.find(r => r.id === selected);
  const wrongDataType = !!selectedRubric && selectedRubric.is_synthetic !== assessment.is_synthetic;

  useEffect(() => {
    if (selected === null && list.data?.length) {
      setSelected(linked.data?.rubric_version_id ?? list.data[list.data.length - 1].id);
    }
  }, [list.data, linked.data, selected]);

  async function act(fn: () => Promise<string>) {
    setError(null);
    setMessage(null);
    try {
      setMessage(await fn());
      list.reload();
      linked.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <section>
      <h2>Rubric</h2>
      <p className="muted">
        Draft rubric extracted from the grading guide with page/quote provenance. Conflicts and ambiguities are recorded
        as issues, never silently fixed. Unresolved criteria stay unresolved (not zero); blocking issues stop total
        proposals while criterion evidence review continues.
      </p>
      <div className="row gap wrap">
        <button
          type="button"
          onClick={() =>
            act(async () => {
              const r = await api.post<{ rubric_version_id: number; created: boolean }>("/rubrics/import", {});
              setSelected(r.rubric_version_id);
              return r.created ? `Imported guide as rubric version ${r.rubric_version_id}.` : `Guide already imported (version ${r.rubric_version_id}).`;
            })
          }
          data-testid="import-guide"
        >
          Import real guide PDF (private path)
        </button>
        <label className="inline">
          Rubric{" "}
          <select value={selected ?? ""} onChange={(e) => setSelected(Number(e.target.value))} aria-label="Rubric version">
            {(list.data ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.is_synthetic ? "[SYNTHETIC] " : ""}
                {r.document} · v{r.version} · {r.status}
              </option>
            ))}
          </select>
        </label>
        {selected ? (
          <>
            <button type="button" data-testid="reparse-rubric" onClick={() => act(async () => {
              const r = await api.post<{ rubric_version_id: number; created: boolean }>(`/rubrics/${selected}/reparse`);
              setSelected(r.rubric_version_id);
              return r.created ? "Created a corrected draft parse. Review it, then link it to the appropriate assessment. Older versions are preserved."
                : "This guide already uses the current parser.";
            })}>Reparse with current parser</button>
            <button type="button" onClick={() => act(async () => { await api.post(`/rubrics/${selected}/activate`); return "Activated (open issues remain open and visible)."; })}>
              Activate
            </button>
            <button
              type="button"
              disabled={wrongDataType || !!assessment.import_warning}
              onClick={() => act(async () => { await api.post(`/assessments/${assessment.id}/rubric-link`, { rubric_version_id: selected }); return `Linked to ${assessment.name}.`; })}
            >
              Link to {assessment.name}
            </button>
          </>
        ) : null}
      </div>
      <ErrorBox error={error || list.error || linked.error} />
      {wrongDataType ? <Notice tone="warn">This is a {selectedRubric?.is_synthetic ? "SYNTHETIC" : "real"} guide,
        while the selected assessment is {assessment.is_synthetic ? "synthetic" : "real"}. They cannot be linked.
        The walkthrough uses [SYNTHETIC] synthetic-p1 · SA3 and student P001.</Notice> : null}
      {message ? <Notice tone="ok">{message}</Notice> : null}
      {linked.data ? (
        <div className="card">
          <h4>{assessment.name}: linked rubric</h4>
          {linked.data.linked ? (
            <KeyValues
              rows={[
                ["Rubric version", String(linked.data.rubric_version_id)],
                [
                  "Total for an empty draft (information bound)",
                  linked.data.empty_draft_bound?.status === "blocked" ? (
                    <span key="b" data-testid="bound-blocked">
                      <Badge tone="bad">blocked</Badge> {(linked.data.empty_draft_bound.reasons ?? []).join(" · ")}
                    </span>
                  ) : (
                    `${linked.data.empty_draft_bound?.lower ?? "—"} to ${linked.data.empty_draft_bound?.upper ?? "—"} (not a confidence interval)`
                  ),
                ],
              ]}
            />
          ) : (
            <p className="muted">No rubric linked: content-only mode.</p>
          )}
        </div>
      ) : null}
      {selected ? <RubricView key={selected} versionId={selected} /> : <Loading show={list.loading} />}
    </section>
  );
}

function RubricView({ versionId }: { versionId: number }) {
  const detail = useLoad(() => api.get<RubricDetail>(`/rubrics/${versionId}`), [versionId]);
  const [focus, setFocus] = useState<Criterion | null>(null);
  const d = detail.data;
  const depth = useMemo(() => {
    const byKey = new Map((d?.criteria ?? []).map((c) => [c.criterion_key, c]));
    const out = new Map<string, number>();
    for (const c of d?.criteria ?? []) {
      let n = 0;
      let p = c.parent_key;
      while (p && byKey.has(p) && n < 6) {
        n += 1;
        p = byKey.get(p)!.parent_key;
      }
      out.set(c.criterion_key, n);
    }
    return out;
  }, [d]);
  if (!d) return <Loading show />;
  const page = focus ? d.document.pages.find((p) => p.page === focus.page) : null;
  return (
    <div>
      {d.is_synthetic ? <Notice tone="warn">SYNTHETIC guide (invented for tests).</Notice> : null}
      <h3>Rubric issues ({d.issues.length})</h3>
      <div className="table-wrap">
        <table className="table compact" data-testid="rubric-issues">
          <thead>
            <tr>
              <th>Assignment</th>
              <th>Kind</th>
              <th>Message</th>
              <th>Page</th>
            </tr>
          </thead>
          <tbody>
            {d.issues.map((i) => (
              <tr key={i.id} data-testid={`rubric-issue-${i.assignment}-${i.kind}`}>
                <td>{i.assignment}</td>
                <td>
                  <Badge tone={i.blocks_total_proposals ? "bad" : "warn"}>{i.kind}</Badge>
                  {i.blocks_total_proposals ? (
                    <div>
                      <Badge tone="bad">blocks total proposals</Badge>
                    </div>
                  ) : null}
                </td>
                <td className="small">{i.message}</td>
                <td>{i.page ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3>Criteria ({d.criteria.length})</h3>
      <div className="split wide-left">
        <div className="table-wrap tall">
          <table className="table compact clickable" data-testid="rubric-criteria">
            <thead>
              <tr>
                <th>Criterion</th>
                <th>Pts</th>
                <th>Kind</th>
                <th>Class</th>
                <th>Pg</th>
              </tr>
            </thead>
            <tbody>
              {d.criteria.map((c) => (
                <tr key={c.criterion_key} onClick={() => setFocus(c)} className={focus?.criterion_key === c.criterion_key ? "selected" : ""}>
                  <td className="small" style={{ paddingLeft: `${0.4 + (depth.get(c.criterion_key) ?? 0) * 1.1}rem` }}>
                    {c.is_container ? <strong>{c.title}</strong> : c.title}
                    <div className="muted">
                      <code>{c.criterion_key}</code>
                      {c.applies_to.length ? ` · applies to ${c.applies_to.join(", ")}` : ""}
                    </div>
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
        </div>
        <div className="card">
          <h4>Source excerpt</h4>
          {focus && page ? (
            <>
              <p className="small muted">
                Page {page.page} · <code>{focus.criterion_key}</code>
              </p>
              <Excerpt text={page.text} quote={focus.source_quote} />
            </>
          ) : (
            <p className="muted">Select a criterion to see its page text with the source quote highlighted.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function Excerpt({ text, quote }: { text: string; quote: string }) {
  const q = quote.replace(/\s+/g, " ").trim();
  const index = q ? text.indexOf(q) : -1;
  if (index < 0) {
    return (
      <div className="excerpt">
        <p className="muted small">Quote not located verbatim on this page.</p>
        <p>{text.slice(0, 1200)}</p>
      </div>
    );
  }
  const start = Math.max(0, index - 300);
  const end = Math.min(text.length, index + q.length + 300);
  return (
    <p className="excerpt">
      {start > 0 ? "… " : ""}
      {text.slice(start, index)}
      <mark>{text.slice(index, index + q.length)}</mark>
      {text.slice(index + q.length, end)}
      {end < text.length ? " …" : ""}
    </p>
  );
}
