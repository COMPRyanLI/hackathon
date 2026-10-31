# College30: team grading and video demonstration packet

**30 invented students (T001–T030), two Python assignments each, 60 submissions.**
All marks and grader names start blank. No real student data is included.

| Folder/file | Purpose |
|---|---|
| `assignments/` | Two short college-level tasks, online-source links and sample expected results |
| `rubrics/` | Detailed demo-authored 10-point rubrics with partial-credit anchors |
| `reference_answers/` | Complete original Python sample solutions |
| `submissions/CONTROL/`, `submissions/HIGHER/` | Files for all 30 students in each assignment |
| `grading/` | Blank totals, 600 blank criterion review rows, two-reader allocation and team instructions |
| `director_only/` | Designed scenarios; hide from graders until their independent pass |
| `DEMO_150_SECONDS.md` | Timed recording sequence, narration, preflight and fallback |
| `SOURCES.md` | Online sources and exact distinction between public and locally authored material |
| `manifest.json` | Student/file inventory, hashes and intentional parse-error declaration |
| `validate.py` | Static checks of all submissions and executable checks of reference answers only |
| `validate_import.py` | Disposable-database import, idempotence and literal impact-condition checks |
| `load_into_gradelens.py` | Preview/import using the existing importer while preserving SYNTHETIC labeling |

Start with [the team grading instructions](grading/TEAM_WORKFLOW.md). Each task
has correct alternatives, recurring mistakes and near-matches with different
behavior. This cohort is deliberately constructed, not representative human data.
T030's CONTROL file has an intentional syntax error. Its HIGHER file has an
undefined return variable. These are demonstration cases, not generator failures.

From the repository root:

```powershell
backend\.venv\Scripts\python.exe fixtures\synthetic\college30\validate.py
backend\.venv\Scripts\python.exe fixtures\synthetic\college30\validate_import.py
backend\.venv\Scripts\python.exe fixtures\synthetic\college30\load_into_gradelens.py
```

The last command only previews. The import check uses a disposable database.
For a separately stored demonstration database:

```powershell
backend\.venv\Scripts\python.exe fixtures\synthetic\college30\load_into_gradelens.py --database data/private/college30-demo/gradelens.db --commit
$env:DATABASE_URL = 'sqlite:///C:/Users/李烨坤/Documents/ChatGPT/Dartmouth AI70 Hackathon/data/private/college30-demo/gradelens.db'
.\start-gradelens.cmd
```

Adjust the absolute database path for another teammate's checkout. The start
script should be run in that same terminal. On completion, clear the override
with `Remove-Item Env:DATABASE_URL`. No changes to `.env` are needed.
To import your completed copy, add `--scores path/to/scores_team.csv`.
If graders differ across assignments, import each assignment with its own sheet
using `--assessment CONTROL` or `--assessment HIGHER`.
Changing scores creates a new version; identical reimport is idempotent.

Select `[SYNTHETIC] synthetic-college30-v1 · CONTROL` or `HIGHER` in the UI.
The loader attaches assignment text, the analytic rubric and the reference as
**P0 Context**. It does not pretend the P1 SA1–SA6 rubric parser supports these
custom exercises. Confirm the reference example manually after your team reviews it.

For real analysis of invented code, use **Runs → Configured providers (.env)**
with your local models available. The dataset is synthetic; this run's inference
can still be real. The pre-existing mock scripts do not cover these assignments.
P1 draft feedback automatically chooses mock mode on synthetic assessments;
do not use that as evidence of live model performance on this new cohort.

Use the P0 source/retrieval/review/approved-note/impact/export workflow here.
For P1 shared-question and partial-credit-anchor features, use the separately
labeled existing `synthetic-p1` dataset and the alternate recording route.
Do not rename these exercises SA3/SA6 to trigger unrelated detectors.

The loader does not run models, assign grades, approve policy or execute submissions.
The data generator is included for reproducibility but overwrites its generated
submissions and blank forms. Never rerun it over completed grading work.
