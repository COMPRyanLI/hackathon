// Minimal client for the local GradeLens API. Mutations carry the CSRF header
// the backend requires; all responses are rendered as text, never as HTML.

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (typeof d === "object" && d && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .join("; ");
  }
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return JSON.stringify(detail);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    headers["X-GradeLens-Client"] = "1";
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
  });
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    const detail = data && typeof data === "object" && "detail" in data ? (data as { detail: unknown }).detail : data;
    throw new ApiError(response.status, `${response.status}: ${describe(detail)}`, detail);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return entries.length ? "?" + entries.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join("&") : "";
}

// ------------------------------------------------------------------ types (subset of backend shapes)

export type Json = Record<string, unknown>;

export interface NoteVersion {
  id: number;
  note_id: number;
  version: number;
  status: "comment" | "draft" | "approved" | "superseded";
  original_text: string;
  interpretation: string;
  note_kind: string | null;
  scope_component: string;
  scope_requirement: string;
  examples: unknown[];
  applicability_conditions: Json | null;
  exceptions: string;
  rationale: string;
  scoring_implication: string;
  source_case_id: number | null;
  supersedes_version_id: number | null;
  coexists_with: number[];
  actor: string;
  created_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  superseded_at: string | null;
}

export interface Assessment {
  import_warning?: string | null;
  id: number;
  dataset_key: string;
  name: string;
  version: number;
  batch_id: number;
  is_synthetic: boolean;
  instructions_status: "pending" | "synthetic_demo" | "confirmed_real";
  instructions_text: string;
  reference_solution_text: string;
  reference_solution_status: string;
  max_points_confirmed: number | null;
  multi_file_scope_note: string;
  context_version: number;
  context_mode: "content_only" | "no_rubric" | "approved_guidance";
  current_guidance: NoteVersion[];
  submission_count: number;
}

export interface PublicSettings {
  csv_path: string;
  sa1_path: string;
  dataset_key: string;
  assessment_name: string;
  filename_id_regex: string;
  retrieval_top_k_per_channel: number;
  initial_review_pair_budget: number;
  allow_cloud_inference: boolean;
  actor: string;
}

export interface ScoreInfo {
  student_id: string;
  score: number | null;
  status: string;
  raw: string;
  grader: string | null;
}

export interface SubmissionSummary {
  id: number;
  student_id: string;
  artifact_count: number;
  alias_count: number;
  files: string[];
  component_scope_status: string;
  parse_failures: number;
  issue_kinds: string[];
  score: ScoreInfo | null;
}

export interface Issue {
  kind: string;
  severity: string;
  line: number | null;
  message: string;
  detail: Json;
}

export interface EvidenceRef {
  artifact: string;
  artifact_id: number;
  source_sha256: string;
  chunk_id: string;
  quote: string;
  line_start: number;
  line_end: number;
}

export interface Claim {
  text?: string;
  correction?: string;
  analyst_claim?: string;
  evidence_refs: EvidenceRef[];
  dropped_refs?: number;
}

export interface RejectedClaim {
  field: string;
  text: string;
  reason: string;
  claim_kept?: boolean;
  failures?: { ref: Json; reason: string }[];
}

export interface EvidenceRecord {
  id: number;
  status: string;
  provider: string;
  model: string;
  is_mock: boolean;
  prompt_version: string;
  evidence: {
    observed_purpose: string;
    observed_purpose_status?: string;
    approach_summary: string;
    explicit_requirement_evidence: Claim[];
    factual_observations: Claim[];
    possible_issues: Claim[];
    anonymization_limitations: string[];
    unknowns: string[];
    coverage_status: string;
  } | null;
  rejected_claims: RejectedClaim[];
  error_kind: string | null;
  error_message: string | null;
  duration_ms: number;
  created_at: string | null;
}

export interface ArtifactView {
  id: number;
  label: string | null;
  position: number;
  content_sha256: string;
  size_bytes: number;
  line_count: number;
  encoding: string;
  source_text: string;
  parse_status: string;
  parse_error: Json | null;
  features: Json;
  string_literal_count: number;
  cross_student_copies: number;
  aliases: { relative_path: string; size_bytes: number }[];
  chunks: { key: string; start_line: number; end_line: number }[];
  issues: Issue[];
  evidence: EvidenceRecord | null;
}

export interface SubmissionDetail extends SubmissionSummary {
  assessment_id: number;
  is_synthetic: boolean;
  artifacts: ArtifactView[];
}

export interface Run {
  id: number;
  assessment_id: number;
  kind: string;
  status: string;
  stage: string;
  params: Json;
  progress: Record<string, Json>;
  coverage: Json;
  provider_snapshot: Json;
  error: string | null;
  attempts: number;
  cancel_requested: boolean;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ChannelDiag {
  status: string;
  rank: number | null;
  score: number | null;
  reason?: string;
  partial?: string;
}

export interface CandidatePair {
  id: number;
  submission_a_id: number;
  submission_b_id: number;
  student_a: string | null;
  student_b: string | null;
  fused_score: number;
  channels: Record<string, ChannelDiag>;
  reasons: string[];
  contributing: Json[];
  scope: string;
  selected: boolean;
  selection_reason: string | null;
  is_random_control: boolean;
}

export interface CaseSummary {
  id: number;
  run_id: number;
  origin: string;
  category: string;
  student_a: string | null;
  student_b: string | null;
  submission_a_id: number;
  submission_b_id: number;
  score_a: number | null;
  score_b: number | null;
  score_gap: number | null;
  grader_a: string | null;
  grader_b: string | null;
  headline: string;
  question: string;
  priority: number;
  is_mock: boolean;
  status: string;
  is_current: boolean;
  stale_reason: string | null;
  guidance_versions: Json[];
  decision_count: number;
  created_at: string | null;
}

export interface Comparison {
  id: number;
  status: string;
  provider: string;
  model: string;
  is_mock: boolean;
  prompt_version: string;
  result: (Json & {
    relation?: string;
    scope?: string;
    requirement_basis?: string;
    equivalence_level?: string | null;
    shared_observations?: Claim[];
    material_differences?: Claim[];
    contradicted_analyst_claims?: Claim[];
    alternative_explanations?: string[];
    missing_context?: string[];
    clarification_question?: string;
    suggested_category?: string;
    context_mode?: string;
    deterministic_differences?: Json;
  }) | null;
  rejected_claims: RejectedClaim[];
  sent_content: Json & { excluded?: string[]; included?: string[] };
  error_kind: string | null;
  error_message: string | null;
  guidance_versions: Json[];
  is_current: boolean;
  stale_reason: string | null;
  duration_ms: number;
}

export interface Decision {
  id: number;
  action: string;
  comment: string;
  proposed_score_a: number | null;
  proposed_score_b: number | null;
  guidance_versions: Json[];
  actor: string;
  created_at: string | null;
}

export interface CaseDetail extends CaseSummary {
  is_synthetic: boolean;
  candidate: CandidatePair;
  comparison: Comparison | null;
  sides: { submission_id: number; student_id: string | null; component_scope_status: string; artifacts: ArtifactView[] }[];
  decisions: Decision[];
  context_mode: string;
}

export interface ImpactItem {
  id: number;
  submission_id: number;
  student_id: string | null;
  status: string;
  method: string;
  rationale: string;
  evidence: Json[];
  priority_rank: number;
  previously_flagged: boolean;
  is_mock: boolean;
  is_current: boolean;
  stale_reason: string | null;
  score: ScoreInfo | null;
  decisions: { id: number; action: string; comment: string; proposed_score: number | null; actor: string; created_at: string | null }[];
}

export interface Impact {
  note: NoteVersion;
  is_synthetic: boolean;
  scan_run: Run | null;
  status_counts: Record<string, number>;
  items: ImpactItem[];
  statement: string;
}

export interface ProviderStatus {
  ollama: Json & { reachable?: boolean; version?: string; installed_models?: string[]; error?: string; base_url?: string };
  roles: Record<string, Json & { provider: string; model: string; ready?: boolean; installed?: boolean; pull_command?: string; is_mock?: boolean; warning?: string; capabilities?: string[]; cloud_allowed?: boolean; api_key_configured?: boolean; sends?: string }>;
  allow_cloud_inference: boolean;
  local_only: boolean;
}

export interface AuditEvent {
  id: number;
  at: string | null;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: number | null;
  detail: Json;
}

export const CASE_ACTIONS = [
  "difference_justified",
  "needs_clarification",
  "context_incomplete",
  "possible_recording_error",
  "dismiss_model_analysis",
] as const;

export const APPLICABILITY_ACTIONS = ["confirm_applies", "confirm_not_applicable", "needs_review", "dismiss_model_analysis"] as const;

export const CONDITION_FEATURES = [
  "line_count", "nonblank_line_count", "comment_line_count", "text_print_call_count", "placeholder_count", "parse_ok",
  "print_call_count", "loop_count", "function_def_count", "branch_count", "import_count", "string_literal_count",
  "top_level_statement_count", "max_depth", "uses_cs1lib", "call_count",
];
