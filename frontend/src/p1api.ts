// Types for the P1 API (draft grading, feedback, shared calibration issues,
// rubric, pre-release). Subsets of the backend shapes in routes_p1.py.
import type { ArtifactView, Json, Run, ScoreInfo } from "./api";

export interface DraftRevision {
  id: number;
  submission_id: number;
  revision_number: number;
  parent_revision_id: number | null;
  total: string;
  criterion_points: Record<string, string>;
  deduction_reasons: string[];
  note: string;
  grader_label: string;
  actor: string;
  origin: "app_save" | "from_historical_record" | "simulated_replay" | string;
  historical_score_record_id: number | null;
  created_at: string | null;
}

export interface QueueRow {
  submission_id: number;
  student_id: string;
  files: number;
  recorded: ScoreInfo | null;
  draft: DraftRevision | null;
  feedback_status: string | null;
  open_cards: number;
}

export interface Criterion {
  criterion_key: string;
  parent_key: string | null;
  assignment: string;
  component_role: string | null;
  title: string;
  page: number;
  source_quote: string;
  max_points: string | null;
  rule_kind: string;
  classification: string;
  authority: string;
  applies_to: string[];
  exceptions: string[];
  fact_binding: string | null;
  is_container: boolean;
}

export interface RubricIssueView {
  id: number;
  assignment: string;
  criterion_key: string | null;
  kind: string;
  message: string;
  page: number | null;
  source_quote: string;
  blocks_total_proposals: boolean;
  status: string;
}

export interface IssueLink {
  id: number;
  submission_id: number;
  revision_id: number | null;
  card_id: number | null;
  method: string;
  active: boolean;
  local_disposition: string | null;
  removed_reason: string | null;
  created_at: string | null;
}

export interface IssueExample {
  submission_id: number;
  student_id: string;
  recorded: ScoreInfo | null;
  draft: DraftRevision | null;
  evidence: CardEvidence[];
  method: string;
  local_disposition: string | null;
}

export interface CalibrationIssue {
  id: number;
  assessment_id: number;
  assignment: string;
  criterion_key: string;
  component_role: string | null;
  rubric_version_id: number | null;
  issue_type: string;
  question: string;
  context_note: string;
  state: "open" | "awaiting_head_ta" | "resolved" | "deferred" | "superseded" | string;
  authority_needed: string;
  affected: number;
  covered_after_resolution: number;
  merged_into_id: number | null;
  resolution_type: string | null;
  resolution_text: string;
  resolution_note_version_id: number | null;
  resolved_by: string | null;
  version: number;
  created_at: string | null;
  links?: IssueLink[];
  events?: { at: string; actor: string; action: string; detail: Json }[];
  guidance?: Json;
  examples?: IssueExample[];
  is_synthetic?: boolean;
}

export interface CardEvidence {
  artifact_id?: number;
  line?: number;
  quote?: string;
  fact?: string;
  actual?: unknown;
  lines?: number[];
}

export interface FeedbackCard {
  id: number;
  feedback_id: number;
  revision_id: number;
  type: string;
  criterion_key: string | null;
  component_role: string | null;
  title: string;
  body: string;
  evidence: CardEvidence[];
  rubric_refs: { criterion_key: string; title: string; page: number; quote: string; max_points: string | null; classification: string; rule_kind: string }[];
  comparison: Json;
  question: string;
  can_request_calibration: boolean;
  issue: CalibrationIssue | null;
  guidance_version_id: number | null;
  priority: number;
  status: string;
  status_reason: string;
  is_mock: boolean;
  dispositions: { action: string; explanation: string; actor: string; new_revision_id: number | null; at: string }[];
}

export interface Feedback {
  revision_number?: number;
  id: number;
  revision_id: number;
  status: string;
  is_current: boolean;
  trigger: string;
  coverage: Json & {
    mode?: string;
    unresolved_criteria?: { criterion_key: string; title: string; classification: string; max_points: string | null }[];
    blocking_rubric_issues?: string[];
    statement?: string;
    peers?: Json & { drafted_peers?: number; note?: string; compared?: Json[] };
    ambiguous_components?: number[];
  };
  stages: Json;
  inputs: Json;
  error: string | null;
  initial_cards: FeedbackCard[];
  all_cards: FeedbackCard[];
  counts: { actionable: number; total: number; suppressed: number };
  created_at: string | null;
  finished_at: string | null;
}

export interface WorkspaceArtifact extends ArtifactView {
  role: { role: string; status: string; evidence: string[] } | null;
  typed_facts: Record<string, unknown>;
  loops: Json[];
}

export interface Workspace {
  submission_id: number;
  student_id: string;
  assessment_id: number;
  assignment: string;
  is_synthetic: boolean;
  recorded: ScoreInfo | null;
  recorded_note: string;
  artifacts: WorkspaceArtifact[];
  criteria: Criterion[];
  rubric_issues: RubricIssueView[];
  current_revision: DraftRevision | null;
  history: DraftRevision[];
  feedback: Feedback | null;
}

export interface SaveDraftResult {
  revision: DraftRevision;
  created: boolean;
  feedback: { id: number; status: string } | null;
  message?: string;
}

export interface RubricSummary {
  id: number;
  version: number;
  status: string;
  document: string;
  document_sha256: string;
  is_synthetic: boolean;
  pages: number;
}

export interface RubricDetail {
  id: number;
  status: string;
  parser_version: string;
  is_synthetic: boolean;
  document: { title: string; sha256: string; pages: { page: number; text: string }[] };
  criteria: Criterion[];
  issues: RubricIssueView[];
}

export interface AssessmentRubric {
  linked: boolean;
  mode?: string;
  rubric_version_id?: number;
  code?: string;
  criteria?: Criterion[];
  issues?: RubricIssueView[];
  blocking?: string[];
  empty_draft_bound?: Json & { status?: string; reasons?: string[]; lower?: string | null; upper?: string | null };
}

export interface Prerelease {
  assessment_id: number;
  submissions: number;
  drafted: number;
  drafts_from_historical: number;
  unresolved_policy: { id: number; state: string; question: string; criterion_key: string; affected: number }[];
  unavailable_evidence: { external_criteria: string[]; manifest_summary: Json | null };
  failed_checks: { feedback: Record<string, number>; latest_pipeline: (Json & { id: number; status: string; notes: string[] }) | null };
  unanswered_feedback: { submission_id: number; card_id: number; type: string; title: string }[];
  feedback_states: Record<string, number>;
  approved_guidance: { note_version_id: number; version: number; status: string; criterion_key: string; applicability: Record<string, number> }[];
  rubric_blocking: string[];
  coverage: { all_pairs: number; retrieved_candidates: number; model_reviewed_pairs: number; unchecked_pairs: number; statement: string };
}

export interface Snapshot {
  id: number;
  version: number;
  csv_path: string;
  json_path: string;
  summary: Json;
  unresolved?: { student_id: string; reasons: string[] }[];
  revision_ids?: number[];
  csv_text?: string;
  created_at?: string | null;
}

export interface FlagView {
  id: number;
  original_flag_id: string;
  row_number: number;
  trust: string;
  similarity: number | null;
  similarity_raw: string;
  assignment_hint: string | null;
  assignment_hint_status: string;
  rubric_note: string;
  explanation: string;
  checks: { kind: string; side?: string; detail: string }[];
  fields: Record<string, string>;
  links: { side: string; status: string; candidates: Json[]; metadata_check: Json }[];
}

export interface BatchPreviewRow {
  assignment: string;
  source_root: string;
  files: number;
  student_bundles: number;
  multi_file_bundles: number;
  bundles_without_score: number;
  students_without_files: number;
  parse_failures: number;
  anonymization_flags: number;
  rejected_files: number;
  manifest_sha256: string;
  issues: Json[];
}

export type { Run };

export const EVIDENCE_STATES = [
  ["available", "Code evidence present in this export."],
  ["not_in_current_export", "Not part of this export (e.g. screenshots). Not proof the student failed to submit; not zero."],
  ["confirmed_not_submitted", "Only from verified original submission records (never inferred here)."],
  ["unreadable", "Present but could not be parsed or decoded."],
  ["anonymization_affected", "Parsing damaged by anonymization placeholders; not a student error."],
  ["not_applicable", "Criterion does not apply."],
  ["unknown", "Cannot be determined from available evidence."],
] as const;

export const SUPPORTED_FACTS = [
  "parse_ok", "header_comment_present", "header_has_placeholder", "while_loop_present", "for_loop_present",
  "loop_inclusive_bound", "loop_counter_start", "loop_start_offset", "loop_compares_two_variables", "uses_pow",
  "uppercase_constant_count", "distinct_rate_literal_count", "rate_uses_multiplier_form", "function_def_count",
  "start_graphics_without_size", "comment_line_count",
];

export function newIdempotencyKey(): string {
  try {
    if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  } catch {
    /* fall through */
  }
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function readLocal(key: string, fallback = ""): string {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function writeLocal(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: value just is not remembered */
  }
}

export function downloadText(text: string, filename: string, type = "text/csv;charset=utf-8"): void {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function feedbackTone(status: string | null | undefined): string {
  return (
    {
      completed: "ok", partial: "warn", failed: "bad", stale: "neutral", superseded: "neutral",
      queued: "info", running: "info",
    } as Record<string, string>
  )[status ?? ""] ?? "neutral";
}

export function classificationTone(c: string): string {
  return ({ explicit: "ok", interpretation_required: "warn", external_evidence_required: "info", conflicting: "bad" } as Record<string, string>)[c] ?? "neutral";
}

// One-shot message that survives the screen remount triggered by changing the selected assessment.
export function setFlash(message: string): void {
  try {
    window.sessionStorage.setItem('gradelens.flash', message);
  } catch {
    /* not shown after remount */
  }
}

export function takeFlash(): string | null {
  try {
    const value = window.sessionStorage.getItem('gradelens.flash');
    window.sessionStorage.removeItem('gradelens.flash');
    return value;
  } catch {
    return null;
  }
}

// Re-read after a mutation. A read issued immediately after a write can observe the
// pre-commit state on this backend, so reload again shortly afterwards.
export function settle(...reloads: (() => void)[]): void {
  for (const delay of [0, 500, 1500]) {
    window.setTimeout(() => reloads.forEach((r) => r()), delay);
  }
}
