# GradeLens — local grading-consistency review (P0 + P1)

GradeLens links an anonymized gradebook to student submissions, retrieves
potentially comparable submissions, asks a local model to compare them against
the original source, and turns the results into an evidence-based review queue.
Instructors resolve missing expectations with versioned notes; an adopted note
triggers a scan of every in-scope submission. **Original grades are never
changed automatically.** Output is provisional review support, not a grade,
fairness finding, plagiarism finding or judgment of any grader.

Private class files, credentials, databases and internal planning inventories are
excluded. The repository includes invented demo data and the runnable P0/P1 app.

Post-build review and a step-by-step demo/real-data checklist:
[`docs/REVIEW-AND-RUN.md`](docs/REVIEW-AND-RUN.md).

**P1 (rubric evidence, draft-grade feedback, shared head-TA questions,
partial-credit calibration, pre-release snapshots):** run guide, demo
walkthrough, test results and limitations in
[`docs/P1-RUN-AND-DEMO.md`](docs/P1-RUN-AND-DEMO.md). The Grading workspace
is now the first tab; all P0 screens remain.

## How to run the demo (Windows)

**Prerequisites:** Python 3.12 (3.11 will fail), Node.js LTS, Git. Ollama is not needed for the synthetic demo.

**One-time setup**
```
git clone --branch backend https://github.com/COMPRyanLI/hackathon.git
cd hackathon
py -3.12 -m venv backend\.venv
.\setup-gradelens.cmd
```
Don't click in the window while setup runs; it can cancel the install.

**Run**
```
.\start-gradelens.cmd
```
Open **http://127.0.0.1:8000/?demo#/import**. Remove `?demo` for the full interface.

After pulling frontend changes, rebuild with `cd frontend; npm.cmd run build`. For live reload while editing, run `npm.cmd run dev` in `frontend/` and open port 5173 instead. Use `npm.cmd`, not `npm`, in PowerShell.

**Demo path:** Load data → Find comparisons → Review queue (decide, with a required reason) → Clarify guidance (approve a note, see impact) → Audit log.

**Known issue:** synthetic runs may come back `partial` with only the D007/D008 case; the D001/D003 case and D006 impact result depend on mock mode applying correctly.

## Quick start (Windows)

Prerequisites:
Python 3.12, Node.js LTS (v24 tested), Ollama running locally.

```powershell
git clone --branch backend https://github.com/COMPRyanLI/hackathon.git
cd hackathon
.\setup-gradelens.cmd     # one time: venv, pinned packages, UI build, .env
.\start-gradelens.cmd     # then open http://127.0.0.1:8000
```

`Ctrl+C` stops the server. Runs that were in progress are marked
`interrupted` on the next start and can be resumed from the Runs screen;
completed, cache-valid items are not recomputed.
If context, approved guidance, prompts, or model settings changed, start a new
run instead: resuming an old run with different analysis inputs is rejected.

Other commands:

```powershell
.\start-gradelens.cmd -Port 8100        # different port
powershell -ExecutionPolicy Bypass -File scripts\test.ps1   # UI build, then backend + Playwright tests
```

Development mode (hot reload): run `.\start-gradelens.cmd`, then in another
window `cd frontend; npm run dev` and open http://127.0.0.1:5173 (it proxies
`/api` to port 8000).

For a demo without private class files, open **Import → Load SYNTHETIC P1 demo
(SA3/SA6)** and select `[SYNTHETIC] synthetic-p1 · SA3`. Synthetic runs use marked
mock responses and do not require a downloaded model. Private-data tests skip
when their local inputs are absent. Real inference requires the models below.

**Impact review:** Shared questions → resolved question → Open impact review.
An `awaiting_head_ta` question must first be resolved with approved guidance;
requesting calibration alone does not create an impact scan.

### Models

Default local models (set per role in `.env`):

| Role | Default | Notes |
|---|---|---|
| Analyst (one summary per distinct file) | `qwen3.5:4b-q4_K_M` | Ollama `/api/chat`, JSON schema in `format`, `think=false` when supported |
| Reviewer (pair comparison, note scan) | `qwen3.5:4b-q4_K_M` | Same weights, separate prompt and call. Not an independent ensemble. |
| Embeddings | `qwen3-embedding:0.6b` | Ollama `/api/embed`, `truncate=false` |

If a model is missing, the app reports the exact command and never switches models:

```powershell
ollama pull qwen3.5:4b-q4_K_M
ollama pull qwen3-embedding:0.6b
```

Missing models make the affected stage fail **visibly**. Retrieval then
continues with the lexical, AST and exact-match channels, and the unavailable
channels are disclosed.

## Workflow

1. **Import** — preview the CSV + `SA1/` mapping and data-quality report, then
   commit. Identity is the exact leading S-number (`S001` ≠ `S0010`); any
   filename suffix (`_logo`, `_mystery`, …) is ignored for identity, ordering,
   retrieval and prompts, but every original path is kept as private
   provenance. All files for a student form one bundle; identical copies
   within a bundle are analyzed once with all aliases kept. Re-importing
   identical inputs changes nothing.
2. **Submissions** — source viewer with line numbers, aliases, static facts
   and flags (parse failures, `[NAME]` anonymization damage, escape warnings,
   possible residual identifying text, shared-content candidates).
3. **Context** — real instructions are pending, so real data runs in
   *content-only* mode: summaries describe what the code contains, and claims
   that a requirement is met are rejected. Synthetic demo context can only be
   attached to the synthetic demo dataset.
4. **Runs** — choose a 10–15 student pilot (default 12) and a reviewer budget
   (default 30 pairs). Stages: analyst summaries → source embeddings →
   summary embeddings → hybrid retrieval → reviewer → review cases. Coverage
   is always reported as non-exhaustive.
5. **Review queue** — side-by-side original source, the reviewer's evidence
   (every quote is validated against the source), contradictions of the
   analyst, alternative explanations and **one** focused question. Record a
   decision; optional proposed scores are human-entered proposals only.
6. **Head TA notes** — comment → draft → explicit approval (choose
   clarification / prospective policy / retrospective review). Approval
   invalidates dependent judgments as *current* (history is kept) and scans
   **every** in-scope submission, including previously unflagged and
   same-score work. Optional deterministic conditions (for example
   `{"feature": "loop_count", "op": ">", "value": 0}`) give reproducible
   results. Without a condition, a model scan can be requested; otherwise the
   result is `uncertain`. Overlapping guidance needs an explicit "both apply"
   confirmation or a supersede.
   Free-text exceptions or component/requirement scope require model or human
   review; a simple deterministic condition cannot override those restrictions.
   Prospective policy is excluded from the standard used to review past work.
7. **Audit & export** — append-only audit log; moderation report (CSV + JSON
   in `data/private/exports/`) with original scores, case decisions,
   applicability results and human-entered proposals. Formula-leading cells
   are escaped.

### Synthetic demo

"Load SYNTHETIC demo dataset" on the Import screen imports
`fixtures/synthetic/demo/`. It contains invented students D001–D009 and scripted
mock responses. Every screen shows a synthetic banner. The suggested pilot is
D001–D005 plus D007–D008, with run provider mode **mock**. Then adopt the loop
note on the D001/D003 case and look at its impact: D006 is found even though it
was never flagged. The scenarios are listed in `fixtures/synthetic/demo/README.md`.

## Hybrid retrieval

Five ranked channels per distinct file: exact bytes, lexical (TF-IDF over code
tokens plus character trigrams of string literals, so text art stays
distinguishable), structural (AST feature counts; unavailable on parse failure),
source vectors, and summary vectors (only from quote-validated analyst facts).
Ranks are fused with reciprocal rank fusion (k = 60, equal weights). Raw scores
from different channels are never averaged.

Bundle similarity is the maximum over artifact pairs, so extra files cannot add
up. Identical files across students vote only in the exact channel. Missing
channels cast no vote and are shown as unavailable. All 3,160 pairwise
similarities for 80 bundles are computed in memory with NumPy, and only the
budgeted pairs go to the reviewer. Similarity numbers are retrieval
diagnostics, not fairness, correctness or misconduct measures.

## Switching the reviewer to Claude (later)

Set all of these in `.env` on the backend (the analyst and embeddings can stay local):

```dotenv
REVIEWER_PROVIDER=anthropic
REVIEWER_MODEL=<available Claude model ID supporting the adapter's structured outputs>
ALLOW_CLOUD_INFERENCE=true
ANTHROPIC_API_KEY=<application API key>
```

The adapter uses the official `anthropic` SDK with Messages API structured
outputs. Refusal, truncation, authentication and connection errors are
distinct, visible failures, and there is no automatic fallback to another model
or back to local.
Replace the model placeholder with a model available to your API account.
Live Anthropic inference has not been verified; the adapter has fake-client
contract tests only.

With `ALLOW_CLOUD_INFERENCE=false` nothing is sent. Each comparison records
what was sent: original source of the pair, validated summaries, deterministic
differences, context and guidance. Grades, grader labels, student IDs and file
names are never sent. Check submissions for residual identifying text before
enabling cloud review (the Submissions screen flags author-style comments
without placeholders).

## Configuration

See `.env.example`. Key settings: `CSV_PATH`, `SA1_PATH`, `PRIVATE_DATA_DIR`,
`FILENAME_ID_REGEX`, per-role `*_PROVIDER` / `*_MODEL`, `OLLAMA_NUM_CTX`
(8192), `ANALYST_MAX_OUTPUT_TOKENS` (2048), `REVIEWER_MAX_OUTPUT_TOKENS` (3072),
`RETRIEVAL_TOP_K_PER_CHANNEL` (5), `INITIAL_REVIEW_PAIR_BUDGET` (30),
`ALLOW_CLOUD_INFERENCE` (false). `mock` providers are opt-in and every mock
output is labeled. On real data, mocks can only be enabled in `.env`, never
per run.

## Data handling

* `cs1_scores_anon.csv`, `SA1/`, `data/private/` (database, exports), `.env`,
  caches, logs, `node_modules/` and builds are git-ignored. The original CSV
  and source files are only read, never written.
* Submissions are parsed with `ast`/`tokenize` only. They are never imported,
  executed or installed.
* The server binds to `127.0.0.1`. Mutations require the `X-GradeLens-Client`
  header and a local origin. The "local-instructor" actor is a prototype
  label, not authentication; add real role checks before any shared hosting.
* Students in this data may be minors: do not publish screenshots,
  exports or databases, and do not put class material on a public URL without
  the account owner's explicit decision.

## Tests and evaluation

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.evaluation.ablation --assessment-id <synthetic id> `
    --labels ..\fixtures\synthetic\demo\eval_labels.json --prepare [--reviewer] [--provider-mode mock]
```

The test suite uses synthetic fixtures only. One test compares the private
snapshot with the documented counts and is skipped if the data is absent. It
covers all 16 acceptance tests in plan §10 plus adapter contracts (Ollama
request shape and error mapping, the Anthropic cloud guard, one bounded
repair), CSRF and an end-to-end API workflow.

The ablation script reports recall of labeled pairs within a fixed budget for
lexical+AST, source-vectors-only and hybrid retrieval, and optionally compares
the reviewer on source only vs summaries + source. The bundled labels describe
the synthetic fixture only. Real-class evaluation needs independent human
labels.

## Architecture

```
backend/app/importers   preview/commit, exact ID matching, bundles, provenance
backend/app/extraction  ast/tokenize facts, anonymization flags, chunks, pair differences
backend/app/providers   Ollama generation/embeddings, Anthropic reviewer, labeled mock, registry
backend/app/analysis    analyst, reviewer, evidence/quote validation, context, cases
backend/app/retrieval   lexical, structural, vectors, RRF fusion, budgeted selection
backend/app/guidance    notes, approval/supersede/conflicts, deterministic conditions, scope-wide scan
backend/app/jobs        persistent SQLite job table, single worker, resume/cancel
backend/app/exports     moderation report
backend/app/evaluation  retrieval and reviewer ablations
frontend/               React + TypeScript + Vite UI (served by FastAPI after build)
prompts/                versioned prompts (prompt file hash is part of every cache key)
fixtures/synthetic/     synthetic demo dataset, scripted mock responses, eval labels
```

Caches: extraction by file hash and parser version; analyst evidence by
content, context, provider/model, prompt and schema; embeddings by input hash,
model digest and preprocessing; reviews by both bundles, evidence, context,
guidance, model and prompt. Guidance or model changes never reuse stale
judgments, and history stays readable.
Generation cache keys include the resolved local model digest and generation
settings. Context changes invalidate current comparisons, cases and note
applicability results; original records and historical decisions remain.

## Measured on this laptop (2026-10-31, real SA1 pilot, scratch database)

These are measurements of throughput and behavior, not accuracy. No human-labeled
ground truth exists yet. Pilot: 12 students (seeded sample), budget 30, Ollama 0.40.13,
`qwen3.5:4b-q4_K_M` + `qwen3-embedding:0.6b`, content-only mode.

| Stage | Result |
|---|---|
| Analyst | 12/12 summaries, 10–25 s each, ~630–910 prompt / 570–1,030 output tokens, no repairs needed; 10 claims rejected by quote validation |
| Embeddings | 12 source + 12 summary vectors, 1,024 dimensions |
| Retrieval | 64 candidate pairs, all 5 channels available |
| Reviewer, first prompt | 26/30 succeeded, 4 `truncated` (output ran on until it hit the cap) |
| Reviewer, after list cap | 30/30 succeeded, 12–39 s (median 16 s), ≤ 2,445 output tokens; 46 claims rejected or trimmed by validation |
| Relations returned | 29 `insufficient_evidence`, 1 `not_comparable`. With no assignment instructions the local reviewer declines to call work equivalent and asks what the assignment requires. |
| End to end | about 15 min cold for 12 students (≈ 9 min with cached summaries) |

Expect review-queue cases on real data to stay mostly "unspecified expectation"
questions until the real instructions or rubric are entered on the Context
screen.

## Known limits

* Real assignment instructions, rubric, maximum points and multi-file scope
  are still pending, so real data runs in content-only mode.
* Local analyst and reviewer share one 4B model. The value of the two-stage
  design and of hybrid retrieval is a hypothesis to evaluate with
  human-reviewed cases (plan §10); no accuracy figure has been measured.
* No grading chronology, severity or drift analysis: the CSV has no timestamps
  or session data.
* The frontend has no automated UI tests. It is verified by type check, build
  and a manual browser walkthrough of the synthetic workflow (including note
  approval, impact review and export). This does not cover every UI state.
* The post-build check passed 59 backend tests and a fresh local Ollama run
  with two real submissions and one reviewed pair. These are functionality
  checks, not grading-accuracy measurements.
* Long or complex assignments beyond this small SA1 workload have not been
  validated against the local generation context window.
