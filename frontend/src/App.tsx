import { useEffect, useState } from "react";
import { api, type Assessment, type PublicSettings } from "./api";
import { ErrorBox, Notice, useLoad } from "./components/common";
import { settle } from "./p1api";
import AuditScreen from "./screens/AuditScreen";
import ContextScreen from "./screens/ContextScreen";
import GradingScreen from "./screens/GradingScreen";
import ImportScreen from "./screens/ImportScreen";
import IssuesScreen from "./screens/IssuesScreen";
import PrereleaseScreen from "./screens/PrereleaseScreen";
import RubricScreen from "./screens/RubricScreen";
import NotesScreen from "./screens/NotesScreen";
import ReviewScreen from "./screens/ReviewScreen";
import RunsScreen from "./screens/RunsScreen";
import SubmissionsScreen from "./screens/SubmissionsScreen";
import CalibrationDemo from "./screens/CalibrationDemo";

export type Screen =
  | "grading" | "issues" | "rubric" | "prerelease"
  | "import" | "submissions" | "context" | "runs" | "review" | "notes" | "audit";

const SCREENS: { id: Screen; label: string }[] = [
  { id: "grading", label: "Grading workspace" },
  { id: "issues", label: "Shared questions" },
  { id: "rubric", label: "Rubric" },
  { id: "prerelease", label: "Pre-release & snapshots" },
  { id: "import", label: "1 · Import" },
  { id: "submissions", label: "2 · Submissions" },
  { id: "context", label: "3 · Context" },
  { id: "runs", label: "4 · Runs & coverage" },
  { id: "review", label: "5 · Review queue" },
  { id: "notes", label: "6 · Head TA notes" },
  { id: "audit", label: "7 · Audit & export" },
];

// Open with ?demo (e.g. http://127.0.0.1:5173/?demo#/import) to show only the demo path.
const DEMO = new URLSearchParams(window.location.search).has("demo");
const CALIBRATION = new URLSearchParams(window.location.search).get("demo") === "calibration";
const CALIBRATION_NAV: { id: Screen; label: string }[] = [
  { id: "grading", label: "1 · Grade & feedback" },
  { id: "issues", label: "2 · Ask once" },
  { id: "notes", label: "3 · Apply guidance" },
  { id: "prerelease", label: "4 · Review changes" },
];
const DEMO_NAV: { id: Screen; label: string }[] = [
  { id: "import", label: "1 · Load data" },
  { id: "runs", label: "2 · Find comparisons" },
  { id: "review", label: "3 · Review queue" },
  { id: "notes", label: "4 · Clarify guidance" },
  { id: "audit", label: "5 · Audit log" },
];
const VISIBLE = CALIBRATION ? CALIBRATION_NAV : DEMO ? DEMO_NAV : SCREENS;
const DEFAULT_SCREEN: Screen = CALIBRATION ? "grading" : DEMO ? "import" : "grading";

export interface Nav {
  go: (screen: Screen, params?: Record<string, string | number>) => void;
  params: URLSearchParams;
}

function parseHash(): { screen: Screen; params: URLSearchParams } {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [name, query = ""] = raw.split("?");
  const screen = (SCREENS.find((s) => s.id === name)?.id ?? DEFAULT_SCREEN) as Screen;
  return { screen, params: new URLSearchParams(query) };
}

export default function App() {
  const [route, setRoute] = useState(parseHash());
  const settings = useLoad(() => api.get<PublicSettings>("/settings/public"), []);
  const assessments = useLoad(() => api.get<Assessment[]>("/assessments"), []);
  const [assessmentId, setAssessmentId] = useState<number | null>(() => {
    const stored = window.localStorage.getItem("gradelens.assessment");
    return stored ? Number(stored) : null;
  });

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const list = assessments.data;
    if (!list || list.length === 0) return;
    if (assessmentId === null || !list.some((a) => a.id === assessmentId)) {
      setAssessmentId(list[list.length - 1].id);
    }
  }, [assessments.data, assessmentId]);

  useEffect(() => {
    try {
      if (assessmentId !== null) window.localStorage.setItem("gradelens.assessment", String(assessmentId));
    } catch {
      /* storage unavailable: selection just is not remembered */
    }
  }, [assessmentId]);

  const nav: Nav = {
    params: route.params,
    go: (screen, params) => {
      const query = params ? "?" + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString() : "";
      window.location.hash = `#/${screen}${query}`;
    },
  };

  const assessment = assessments.data?.find((a) => a.id === assessmentId) ?? null;
  const refreshAssessments = () => settle(assessments.reload);
  const onImported = (id: number) => {
    setAssessmentId(id);
    settle(assessments.reload);
  };

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <strong>GradeLens</strong>
          <span className="muted"> · local grading-consistency review</span>
        </div>
        <label className="assessment-select">
          <span>Assessment</span>
          <select
            value={assessmentId ?? ""}
            onChange={(e) => {
              setAssessmentId(e.target.value ? Number(e.target.value) : null);
              nav.go(route.screen);
            }}
            aria-label="Selected assessment"
          >
            {!assessments.data?.length ? <option value="">(none imported)</option> : null}
            {assessments.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.is_synthetic ? "[SYNTHETIC] " : ""}
                {a.import_warning ? "[IMPORT MISMATCH] " : ""}
                {a.dataset_key} · {a.name} v{a.version} ({a.submission_count} submissions)
              </option>
            ))}
          </select>
        </label>
        <nav className="nav" aria-label="Screens">
          {VISIBLE.map((s) => (
            <a key={s.id} href={`#/${s.id}`} className={route.screen === s.id ? "active" : ""}>
              {s.label}
            </a>
          ))}
        </nav>
      </header>

      <div className="thesis" role="note">
        <span><strong>Review support:</strong> {CALIBRATION ? "surfaces possible grading issues and reuses approved guidance." : "finds comparable submissions, quotes the evidence, asks one focused question."}</span>
        <span><strong>People decide:</strong> what counts, what is fair, and whether any grade changes.</span>
      </div>

      {assessment?.is_synthetic ? (
        <div className="synthetic-banner" role="status">
          SYNTHETIC DEMO DATA: invented for testing, not real students or findings
        </div>
      ) : null}

      <main className="main" key={`${assessmentId}-${route.screen}`}>
        {CALIBRATION ? <CalibrationDemo assessment={assessment} nav={nav} step={route.screen} onImported={onImported} /> : <>
        {assessment?.import_warning ? <Notice tone="warn">{assessment.import_warning}</Notice> : null}
        <ErrorBox error={assessments.error || settings.error} />
        {route.screen === "import" && <ImportScreen settings={settings.data} onImported={onImported} assessment={assessment} />}
        {route.screen !== "import" && !assessment ? (
          <p className="muted">Import a dataset (or load the synthetic demo) on the Import screen first.</p>
        ) : null}
        {assessment && route.screen === "grading" && <GradingScreen assessment={assessment} nav={nav} />}
        {assessment && route.screen === "issues" && <IssuesScreen assessment={assessment} nav={nav} />}
        {assessment && route.screen === "rubric" && <RubricScreen assessment={assessment} />}
        {assessment && route.screen === "prerelease" && <PrereleaseScreen assessment={assessment} nav={nav} />}
        {assessment && route.screen === "submissions" && <SubmissionsScreen assessment={assessment} nav={nav} />}
        {assessment && route.screen === "context" && <ContextScreen assessment={assessment} onSaved={refreshAssessments} />}
        {assessment && route.screen === "runs" && <RunsScreen assessment={assessment} settings={settings.data} nav={nav} />}
        {assessment && route.screen === "review" && <ReviewScreen assessment={assessment} nav={nav} />}
        {assessment && route.screen === "notes" && (
          <NotesScreen assessment={assessment} nav={nav} onGuidanceChanged={refreshAssessments} />
        )}
        {assessment && route.screen === "audit" && <AuditScreen assessment={assessment} />}
        </>}
      </main>

      <footer className="footer">
        Provisional review support for instructors. Not an automated grade, not a fairness or misconduct determination.
        Original grades are never changed automatically.
      </footer>
    </div>
  );
}
