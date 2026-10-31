import { useState } from "react";
import type { Nav } from "../App";
import { api, type Assessment, type SubmissionDetail, type SubmissionSummary } from "../api";
import { ArtifactPanel, Badge, ErrorBox, KeyValues, Loading, Tabs, JsonBlock, fmtScore, useLoad } from "../components/common";

export default function SubmissionsScreen({ assessment, nav }: { assessment: Assessment; nav: Nav }) {
  const list = useLoad(() => api.get<SubmissionSummary[]>(`/assessments/${assessment.id}/submissions`), [assessment.id]);
  const quality = useLoad(() => api.get<Record<string, unknown>>(`/assessments/${assessment.id}/data-quality`), [assessment.id]);
  const selected = Number(nav.params.get("id")) || null;
  const [filter, setFilter] = useState("");
  const rows = (list.data ?? []).filter(
    (s) => !filter || s.student_id.toLowerCase().includes(filter.toLowerCase()) || s.issue_kinds.join(" ").includes(filter),
  );
  return (
    <section>
      <h2>
        Submissions & source <Loading show={list.loading} />
      </h2>
      <ErrorBox error={list.error} />
      <details>
        <summary>Saved import and data-quality report</summary>
        <ErrorBox error={quality.error} />
        {quality.data ? <JsonBlock value={quality.data} /> : <Loading show={quality.loading} />}
      </details>
      <div className="split wide-left">
        <div>
          <label className="inline">
            Filter
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="student id or issue kind" />
          </label>
          <div className="table-wrap tall">
            <table className="table compact clickable">
              <thead>
                <tr>
                  <th>Student</th>
                  <th>Recorded score · grader</th>
                  <th>Files</th>
                  <th>Parse</th>
                  <th>Issues</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr
                    key={s.id}
                    className={s.id === selected ? "selected" : ""}
                    onClick={() => nav.go("submissions", { id: s.id })}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && nav.go("submissions", { id: s.id })}
                  >
                    <td>{s.student_id}</td>
                    <td>{fmtScore(s.score)}</td>
                    <td title={s.files.join("\n")}>
                      {s.artifact_count}
                      {s.alias_count > s.artifact_count ? ` (${s.alias_count} files)` : ""}
                      {s.component_scope_status === "component_scope_unconfirmed" ? (
                        <Badge tone="info" title="Multiple distinct files; component scope unconfirmed">multi</Badge>
                      ) : null}
                    </td>
                    <td>{s.parse_failures ? <Badge tone="bad">{s.parse_failures} failed</Badge> : "ok"}</td>
                    <td className="small">{s.issue_kinds.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            Students with no file in this upload are not listed here (see the import report). Missing files are not
            treated as non-submission or zero credit.
          </p>
        </div>
        <div>{selected ? <SubmissionDetailView id={selected} /> : <p className="muted">Select a submission to view its source.</p>}</div>
      </div>
    </section>
  );
}

function SubmissionDetailView({ id }: { id: number }) {
  const detail = useLoad(() => api.get<SubmissionDetail>(`/submissions/${id}`), [id]);
  const [tab, setTab] = useState(0);
  const d = detail.data;
  return (
    <div>
      <ErrorBox error={detail.error} />
      <Loading show={detail.loading} />
      {d ? (
        <>
          <h3>{d.student_id}</h3>
          <KeyValues
            rows={[
              ["Recorded score · grader", fmtScore(d.score)],
              ["Distinct files", `${d.artifact_count} (${d.alias_count} file paths)`],
              ["Component scope", d.component_scope_status],
            ]}
          />
          {d.artifact_count > 1 ? (
            <p className="note">
              Multiple distinct files are kept and analyzed separately. They are not assumed to be required components
              or competing versions; nothing is selected by file name, size or date.
            </p>
          ) : null}
          <Tabs
            tabs={d.artifacts.map((a, i) => ({ id: i, label: `File ${i + 1}: ${a.aliases.map((x) => x.relative_path).join(" = ")}` }))}
            active={Math.min(tab, d.artifacts.length - 1)}
            onChange={setTab}
          />
          {d.artifacts[Math.min(tab, d.artifacts.length - 1)] ? (
            <ArtifactPanel key={d.artifacts[Math.min(tab, d.artifacts.length - 1)].id} artifact={d.artifacts[Math.min(tab, d.artifacts.length - 1)]} />
          ) : null}
        </>
      ) : null}
    </div>
  );
}
