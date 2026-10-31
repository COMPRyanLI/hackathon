import { useEffect, useState } from "react";
import { api, type Assessment } from "../api";
import { Badge, ErrorBox, Notice, errorText } from "../components/common";

const MODE_TEXT: Record<string, string> = {
  content_only:
    "Content-only mode: no confirmed assignment instructions. The models may describe what the source contains, but requirement claims are blocked and academic equivalence stays provisional or unknown.",
  no_rubric:
    "Instructions available, no approved guidance yet. Explicit requirements can be assessed; unspecified partial credit or style expectations become questions for the instructor.",
  approved_guidance:
    "Approved instructor guidance exists. Comparisons are evaluated against the approved versions while unresolved context stays visible.",
};

export default function ContextScreen({ assessment, onSaved }: { assessment: Assessment; onSaved: () => void }) {
  const [form, setForm] = useState({
    instructions_status: assessment.instructions_status,
    instructions_text: assessment.instructions_text,
    reference_solution_text: assessment.reference_solution_text,
    reference_solution_status: assessment.reference_solution_status,
    max_points_confirmed: assessment.max_points_confirmed === null ? "" : String(assessment.max_points_confirmed),
    multi_file_scope_note: assessment.multi_file_scope_note,
  });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setForm({
      instructions_status: assessment.instructions_status,
      instructions_text: assessment.instructions_text,
      reference_solution_text: assessment.reference_solution_text,
      reference_solution_status: assessment.reference_solution_status,
      max_points_confirmed: assessment.max_points_confirmed === null ? "" : String(assessment.max_points_confirmed),
      multi_file_scope_note: assessment.multi_file_scope_note,
    });
  }, [assessment]);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      const body = {
        ...form,
        max_points_confirmed: form.max_points_confirmed.trim() === "" ? null : Number(form.max_points_confirmed),
      };
      const result = await api.patch<Assessment>(`/assessments/${assessment.id}/context`, body);
      setSaved(`Saved. Context version ${result.context_version}. Analyst summaries for changed context will be regenerated on the next run.`);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const statusOptions = assessment.is_synthetic
    ? [
        ["pending", "Pending (content-only)"],
        ["synthetic_demo", "Synthetic demo context (labeled)"],
      ]
    : [
        ["pending", "Pending — real instructions not yet provided"],
        ["confirmed_real", "Confirmed real instructions"],
      ];

  return (
    <section>
      <h2>Assignment context</h2>
      <p>
        Current mode: <Badge tone="info">{assessment.context_mode}</Badge>
      </p>
      <p className="muted">{MODE_TEXT[assessment.context_mode]}</p>
      {assessment.instructions_status === "pending" ? (
        <Notice tone="warn">
          Real assignment instructions and rubric are still pending. Requirements are never inferred from an example
          solution, source comments or file names.
        </Notice>
      ) : null}
      {assessment.instructions_status === "synthetic_demo" ? (
        <Notice tone="warn">This is synthetic demo context, invented for testing. It is not the real course assignment.</Notice>
      ) : null}
      <form
        className="form-stack"
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
      >
        <label>
          Instructions status
          <select
            value={form.instructions_status}
            onChange={(e) => setForm({ ...form, instructions_status: e.target.value as Assessment["instructions_status"] })}
          >
            {statusOptions.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Assignment instructions
          <textarea rows={6} value={form.instructions_text} onChange={(e) => setForm({ ...form, instructions_text: e.target.value })} />
        </label>
        <label>
          Example / reference solution (optional; shows one acceptable approach, not a list of requirements)
          <textarea
            rows={5}
            className="mono"
            value={form.reference_solution_text}
            onChange={(e) => setForm({ ...form, reference_solution_text: e.target.value })}
          />
        </label>
        <label>
          Reference solution status
          <select
            value={form.reference_solution_status}
            onChange={(e) => setForm({ ...form, reference_solution_status: e.target.value })}
          >
            <option value="absent">absent</option>
            <option value="example_unconfirmed">example (status unconfirmed)</option>
            <option value="instructor_confirmed_example">instructor-confirmed example</option>
          </select>
        </label>
        <label>
          Maximum points (confirmed by the instructor; leave blank if unknown)
          <input value={form.max_points_confirmed} onChange={(e) => setForm({ ...form, max_points_confirmed: e.target.value })} inputMode="decimal" />
        </label>
        <label>
          Multi-file scope note (how extra files should be treated)
          <textarea rows={3} value={form.multi_file_scope_note} onChange={(e) => setForm({ ...form, multi_file_scope_note: e.target.value })} />
        </label>
        <div>
          <button type="submit" className="primary" disabled={busy}>
            Save context
          </button>
        </div>
      </form>
      <ErrorBox error={error} />
      {saved ? <Notice tone="ok">{saved}</Notice> : null}
    </section>
  );
}
