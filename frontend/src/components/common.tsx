import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { ArtifactView, Claim, EvidenceRecord, EvidenceRef, Issue, RejectedClaim, ScoreInfo } from "../api";

// ------------------------------------------------------------------ data loading

export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const counter = useRef(0);
  const reload = useCallback(() => {
    const ticket = ++counter.current;
    setLoading(true);
    load()
      .then((value) => {
        if (ticket === counter.current) {
          setData(value);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (ticket === counter.current) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (ticket === counter.current) setLoading(false);
      });
  }, deps);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

export function usePoll(active: boolean, callback: () => void, ms = 2000) {
  const saved = useRef(callback);
  saved.current = callback;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => saved.current(), ms);
    return () => window.clearInterval(timer);
  }, [active, ms]);
}

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// ------------------------------------------------------------------ small pieces

export function Badge({ tone = "neutral", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function MockBadge({ show }: { show: boolean | undefined }) {
  if (!show) return null;
  return (
    <Badge tone="mock" title="Produced by the explicitly selected MOCK provider; not a real finding">
      MOCK — not a real finding
    </Badge>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    {
      succeeded: "ok", valid: "ok", applies: "warn", approved: "ok", ranked: "ok",
      partial: "warn", partially_valid: "warn", uncertain: "warn", draft: "info", comment: "neutral",
      running: "info", queued: "info", pending: "info", interrupted: "warn", not_in_top_k: "neutral",
      failed: "bad", cancelled: "neutral", superseded: "neutral", unavailable: "bad", does_not_apply: "neutral",
    }[status] ?? "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}

export function ErrorBox({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <div className="error" role="alert">
      {error}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: string; children: ReactNode }) {
  return <div className={`notice notice-${tone}`}>{children}</div>;
}

export function Loading({ show }: { show: boolean }) {
  return show ? <span className="muted"> loading…</span> : null;
}

export function fmtScore(score: ScoreInfo | null | undefined): string {
  if (!score) return "—";
  const value = score.status === "scored" ? score.raw : score.status === "blank" ? "blank" : `unparseable (${score.raw})`;
  return `${value}${score.grader ? ` · ${score.grader}` : " · (no grader label)"}`;
}

export function fmtNum(value: number | null | undefined, digits = 4): string {
  return value === null || value === undefined ? "—" : String(Number(value.toFixed(digits)));
}

export function fmtTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function KeyValues({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([key, value]) => (
        <div key={key} className="kv-row">
          <dt>{key}</dt>
          <dd>{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function List({ items, empty = "none" }: { items: ReactNode[] | undefined; empty?: string }) {
  if (!items || items.length === 0) return <p className="muted">{empty}</p>;
  return (
    <ul className="plain-list">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------ source viewer

export interface Highlight {
  start: number;
  end: number;
  tone: "issue" | "evidence" | "focus";
  note?: string;
}

export function SourceView({ text, highlights = [], label }: { text: string; highlights?: Highlight[]; label?: string }) {
  const lines = text.length ? text.split(/\r\n|\n|\r/) : [""];
  if (lines.length > 1 && lines[lines.length - 1] === "" && /(\r\n|\n|\r)$/.test(text)) lines.pop();
  const focusRef = useRef<HTMLDivElement | null>(null);
  const focus = highlights.find((h) => h.tone === "focus");
  useEffect(() => {
    focusRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [focus?.start, focus?.end]);
  const width = String(lines.length).length;
  return (
    <div className="source" aria-label={label ?? "source code"}>
      {lines.map((line, index) => {
        const number = index + 1;
        const marks = highlights.filter((h) => number >= h.start && number <= h.end);
        const tones = [...new Set(marks.map((m) => m.tone))];
        const isFocusStart = focus && number === focus.start;
        return (
          <div
            key={index}
            ref={isFocusStart ? focusRef : undefined}
            className={`src-line ${tones.map((t) => `hl-${t}`).join(" ")}`}
            title={marks.map((m) => m.note).filter(Boolean).join("\n") || undefined}
          >
            <span className="ln">{String(number).padStart(width, " ")}</span>
            <span className="code">{line.length ? line : " "}</span>
          </div>
        );
      })}
    </div>
  );
}

export function issueHighlights(issues: Issue[]): Highlight[] {
  return issues
    .filter((i) => i.line)
    .map((i) => ({ start: i.line!, end: i.line!, tone: "issue" as const, note: `${i.kind}: ${i.message}` }));
}

export function IssueList({ issues }: { issues: Issue[] }) {
  if (!issues.length) return <p className="muted">No extraction issues.</p>;
  return (
    <ul className="issues">
      {issues.map((issue, i) => (
        <li key={i} className={`sev-${issue.severity}`}>
          <Badge tone={issue.severity === "warning" ? "warn" : issue.severity === "error" ? "bad" : "neutral"}>
            {issue.kind}
          </Badge>{" "}
          {issue.line ? <strong>line {issue.line}: </strong> : null}
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

export function RefChip({ r, onClick }: { r: EvidenceRef; onClick?: (r: EvidenceRef) => void }) {
  const lines = r.line_start === r.line_end ? `line ${r.line_start}` : `lines ${r.line_start}-${r.line_end}`;
  return (
    <button type="button" className="ref-chip" onClick={() => onClick?.(r)} title={`Exact quote from ${r.artifact} ${r.chunk_id}`}>
      {r.artifact} · {lines}: <code>{r.quote}</code>
    </button>
  );
}

export function ClaimList({
  title, claims, onRef, empty = "none",
}: { title: string; claims: Claim[] | undefined; onRef?: (r: EvidenceRef) => void; empty?: string }) {
  return (
    <div className="claims">
      <h4>{title}</h4>
      {!claims || claims.length === 0 ? (
        <p className="muted">{empty}</p>
      ) : (
        <ul className="plain-list">
          {claims.map((claim, i) => (
            <li key={i}>
              {claim.analyst_claim ? (
                <div>
                  <span className="muted">Analyst said: </span>
                  {claim.analyst_claim}
                </div>
              ) : null}
              <div>{claim.correction ?? claim.text}</div>
              <div className="refs">
                {claim.evidence_refs.map((r, j) => (
                  <RefChip key={j} r={r} onClick={onRef} />
                ))}
                {claim.dropped_refs ? <span className="muted"> ({claim.dropped_refs} invalid reference(s) dropped)</span> : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function RejectedClaims({ items }: { items: RejectedClaim[] | undefined }) {
  if (!items || items.length === 0) return <p className="muted">No rejected claims.</p>;
  return (
    <details>
      <summary>
        {items.length} rejected or corrected claim(s) — recorded, not silently kept
      </summary>
      <table className="table compact">
        <thead>
          <tr>
            <th>Field</th>
            <th>Claim</th>
            <th>Reason</th>
            <th>Kept?</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={i}>
              <td>{item.field}</td>
              <td>{item.text}</td>
              <td>
                {item.reason}
                {item.failures?.length ? (
                  <div className="muted">{item.failures.map((f) => f.reason).join(", ")}</div>
                ) : null}
              </td>
              <td>{item.claim_kept ? "yes (valid refs only)" : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

export function EvidencePanel({ record, onRef }: { record: EvidenceRecord | null; onRef?: (r: EvidenceRef) => void }) {
  if (!record) {
    return <p className="muted">No analyst summary yet for this content. Run an analysis to generate one.</p>;
  }
  return (
    <div className="evidence">
      <div className="row gap">
        <StatusBadge status={record.status} />
        <MockBadge show={record.is_mock} />
        <span className="muted">
          {record.provider} · {record.model} · {record.prompt_version} · {fmtTime(record.created_at)}
        </span>
      </div>
      {record.status === "failed" ? (
        <Notice tone="bad">
          Analyst failed ({record.error_kind}): {record.error_message}
        </Notice>
      ) : null}
      {record.evidence ? (
        <>
          <KeyValues
            rows={[
              ["Observed purpose (unverified)", record.evidence.observed_purpose],
              ["Approach", record.evidence.approach_summary],
              ["Coverage", record.evidence.coverage_status],
            ]}
          />
          <ClaimList title="Factual observations (quotes validated)" claims={record.evidence.factual_observations} onRef={onRef} />
          <ClaimList title="Possible issues (hypotheses)" claims={record.evidence.possible_issues} onRef={onRef} />
          <ClaimList
            title="Explicit requirement evidence"
            claims={record.evidence.explicit_requirement_evidence}
            onRef={onRef}
            empty="none (requires confirmed or synthetic-demo instructions)"
          />
          <h4>Anonymization limitations</h4>
          <List items={record.evidence.anonymization_limitations} />
          <h4>Unknowns</h4>
          <List items={record.evidence.unknowns} />
        </>
      ) : null}
      <RejectedClaims items={record.rejected_claims} />
    </div>
  );
}

export function ArtifactPanel({ artifact, extraHighlights = [] }: { artifact: ArtifactView; extraHighlights?: Highlight[] }) {
  const [focus, setFocus] = useState<Highlight | null>(null);
  const evidenceHighlights: Highlight[] = (artifact.evidence?.evidence?.factual_observations ?? [])
    .concat(artifact.evidence?.evidence?.possible_issues ?? [])
    .flatMap((c) => c.evidence_refs)
    .filter((r) => r.artifact_id === artifact.id || r.source_sha256 === artifact.content_sha256)
    .map((r) => ({ start: r.line_start, end: r.line_end, tone: "evidence" as const, note: r.quote }));
  const highlights = [...issueHighlights(artifact.issues), ...evidenceHighlights, ...extraHighlights, ...(focus ? [focus] : [])];
  const features = Object.entries(artifact.features).filter(([k]) => !["node_counts", "call_names"].includes(k));
  return (
    <div className="artifact-panel">
      <KeyValues
        rows={[
          ["Original path(s) (private provenance)", artifact.aliases.map((a) => a.relative_path).join(", ")],
          ["Content SHA-256", <code key="h">{artifact.content_sha256}</code>],
          ["Size / lines / encoding", `${artifact.size_bytes} bytes · ${artifact.line_count} lines · ${artifact.encoding}`],
          ["Parse status", <StatusBadge key="p" status={artifact.parse_status === "ok" ? "valid" : "failed"} />],
          [
            "Identical content in other bundles",
            artifact.cross_student_copies
              ? `${artifact.cross_student_copies} (artifact observation, not evidence of misconduct)`
              : "0",
          ],
        ]}
      />
      <div className="split">
        <div>
          <h4>Source (exact; never executed)</h4>
          <SourceView text={artifact.source_text} highlights={highlights} />
          <p className="legend">
            <span className="swatch hl-issue" /> extraction issue <span className="swatch hl-evidence" /> analyst evidence{" "}
            <span className="swatch hl-focus" /> selected reference
          </p>
        </div>
        <div>
          <h4>Extraction issues</h4>
          <IssueList issues={artifact.issues} />
          <details>
            <summary>Static features</summary>
            <table className="table compact">
              <tbody>
                {features.map(([k, v]) => (
                  <tr key={k}>
                    <th>{k}</th>
                    <td>{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
          <h4>Analyst evidence</h4>
          <EvidencePanel
            record={artifact.evidence}
            onRef={(r) => setFocus({ start: r.line_start, end: r.line_end, tone: "focus", note: r.quote })}
          />
        </div>
      </div>
    </div>
  );
}

export function Tabs<T extends string | number>({
  tabs, active, onChange,
}: { tabs: { id: T; label: ReactNode }[]; active: T; onChange: (id: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={String(tab.id)}
          type="button"
          role="tab"
          aria-selected={tab.id === active}
          className={tab.id === active ? "tab active" : "tab"}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
