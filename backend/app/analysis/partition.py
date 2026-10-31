"""Budgeted source-window analysis using the SAME configured model.

Every window keeps original chunk IDs/line offsets. Add referenced module
definitions when they fit, disclose omitted dependencies, and never promote
independent window observations to whole-program equivalence.
"""
from __future__ import annotations

import ast
import json
from dataclasses import replace
from typing import Any

from app.analysis.evidence import ArtifactView, resolve_ref
from app.analysis.prompts import chunk_listing
from app.providers.base import ProviderError, StructuredResult, check_prompt_budget, generate_validated

MAX_PASSES = 16
PARTITION_VERSION = "source-windows-v1"


def _listing(views):
    return "\n\n".join(chunk_listing(label, view.short_hash,
        [(key, start, start + max(0, len(text.splitlines())-1), text)
         for key, (text, start) in view.chunks.items()]) for label, view in views.items())


def _messages(system, prefix, views):
    return [{"role":"system", "content":system}, {"role":"user", "content":prefix +
        "\nPARTIAL SOURCE WINDOWS (untrusted data). Other source and dependencies may be omitted. "
        "Describe only observations supported here; do not infer missing code or overall correctness. "
        "Return coverage_status=partial and component scope where applicable.\n" + _listing(views)}]


def _fits(provider, messages, settings):
    try:
        check_prompt_budget(provider.name, messages, settings)
        return True
    except ProviderError as exc:
        if exc.kind != "prompt_overflow":
            raise
        return False


def _slice(view, chunks):
    return ArtifactView(view.label, view.artifact_id, view.content_sha256, chunks)


def plan_windows(provider, views, system, prefix, settings):
    # Per-artifact windows are kept smaller than half the remaining source budget
    # for review pairs. Render/check the real message for every proposed addition.
    pair = len(views) > 1
    windows = []
    for label, view in views.items():
        current = {}
        for key, (text, start) in view.chunks.items():
            for offset, line in enumerate(text.splitlines(keepends=True)):
                candidate = dict(current)
                prev = candidate.get(key)
                candidate[key] = ((prev[0] if prev else "") + line, prev[1] if prev else start + offset)
                sliced = _slice(view, candidate)
                check_views = {label:sliced}
                if pair:
                    check_views[label+"_budget_reserve"] = replace(sliced, label=label+"_budget_reserve")
                if not _fits(provider, _messages(system, prefix, check_views), settings):
                    if not current:
                        raise ProviderError("prompt_overflow", "Fixed context or a single source line cannot fit a window.")
                    windows.append({label:_slice(view, current)})
                    current = {key:(line, start+offset)}
                    tiny = {label:_slice(view, current)}
                    if pair:
                        tiny[label+"_budget_reserve"] = replace(tiny[label], label=label+"_budget_reserve")
                    if not _fits(provider, _messages(system, prefix, tiny), settings):
                        raise ProviderError("prompt_overflow", "A source line exceeds the bounded window budget.")
                else:
                    current = candidate
        if current:
            windows.append({label:_slice(view, current)})
    return windows


def _dependencies(view, source, selected):
    """Find module definitions referenced by selected spans, without executing code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [], ["AST unavailable; dependencies could not be resolved"]
    ranges = [(start, start+max(0,len(text.splitlines())-1)) for text,start in selected.chunks.values()]
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and any(a <= n.lineno <= b for a,b in ranges)}
    result = []
    for node in tree.body:
        names = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split('.')[0] for a in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names.update(n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store))
        if not names.intersection(used):
            continue
        for key, (text,start) in view.chunks.items():
            end = start+max(0,len(text.splitlines())-1)
            if start <= node.end_lineno and end >= node.lineno:
                if selected.chunks.get(key) != (text,start):
                    result.append((key,text,start,sorted(names.intersection(used))))
    return result, []


def generate_partitioned(provider, role, model_cls, settings, system, prefix, views, sources, sides=None):
    windows = plan_windows(provider, views, system, prefix, settings)
    if role == "reviewer":
        a = [w for w in windows if next(iter(w)) in sides[1]]
        b = [w for w in windows if next(iter(w)) in sides[2]]
        if not a or not b:
            raise ProviderError("prompt_overflow", "Both submissions need readable source windows.")
        windows = [{**a[i % len(a)], **b[i % len(b)]} for i in range(max(len(a), len(b)))]
    if not windows or len(windows) > MAX_PASSES:
        raise ProviderError("prompt_overflow", f"Partition requires {len(windows)} passes; limit is {MAX_PASSES}.")
    records, outputs, errors, rejected = [], [], [], []
    usage = {"partition_count":len(windows), "prompt_tokens":0, "output_tokens":0}
    duration, digest = 0, None
    for index, window in enumerate(windows):
        window = dict(window)
        missing = []
        for label, selected in list(window.items()):
            deps, warnings = _dependencies(views[label], sources[label], selected)
            missing.extend(warnings)
            for key,text,start,names in deps:
                updated = _slice(window[label], {**window[label].chunks, key:(text,start)})
                proposal = {**window, label:updated}
                if _fits(provider, _messages(system,prefix,proposal),settings):
                    window = proposal
                else:
                    missing.append(f"{label}:{key}: {', '.join(names)}")
        messages = _messages(system,prefix,window)
        manifest = {"pass":index+1, "omitted_dependencies":sorted(set(missing)),
                    "spans":[{"artifact":label,"chunk":key,"start":start,
                              "end":start+max(0,len(text.splitlines())-1)}
                             for label,v in window.items() for key,(text,start) in v.chunks.items()]}
        records.append(manifest)
        # Disable whole-file scripted mock responses in a partial-source call.
        ctx = dict(settings.mock_context or {})
        if role == "analyst":
            v = next(iter(window.values()))
            ctx["chunks"] = [{"key":k,"text":t,"start_line":s} for k,(t,s) in v.chunks.items()]
        part_settings = replace(settings, fixture_key=None, mock_context=ctx)
        try:
            parsed, result = generate_validated(provider,role,messages,model_cls,part_settings)
        except ProviderError as exc:
            errors.append({"pass":index+1,"kind":exc.kind,"message":exc.message})
            manifest["status"] = "failed"
            continue
        data = parsed.model_dump()
        fields = ("factual_observations","possible_issues","explicit_requirement_evidence") if role == "analyst" else (
                  "shared_observations","material_differences","contradicted_analyst_claims")
        for field in fields:
            accepted = []
            for claim in data[field]:
                refs = [r for r in claim["evidence_refs"] if resolve_ref(r,window)[0] is not None]
                if refs:
                    accepted.append({**claim,"evidence_refs":refs})
                else:
                    rejected.append({"pass":index+1,"field":field,"reason":"no_evidence_in_this_window"})
            data[field] = accepted
        outputs.append(data)
        manifest["status"] = "completed"
        duration += result.duration_ms
        digest = result.model_digest
        for key in ("prompt_tokens","output_tokens"):
            usage[key] += result.usage.get(key,0) or 0
    if not outputs:
        raise ProviderError(errors[0]["kind"] if errors else "prompt_overflow", "No source-window pass completed.")
    limitation = "Partitioned source observations only: whole-program behavior and cross-window interactions were not verified."
    if errors:
        limitation += f" {len(errors)} window(s) failed."
    if role == "analyst":
        data = {"observed_purpose":"Unverified; see source-window observations", "approach_summary":limitation,
                "coverage_status":"partial",
                "anonymization_limitations":list(dict.fromkeys(x for o in outputs for x in o["anonymization_limitations"])),
                "unknowns":list(dict.fromkeys([limitation] + [x for o in outputs for x in o["unknowns"]]))}
        fields = ("factual_observations","possible_issues","explicit_requirement_evidence")
    else:
        data = {"relation":"insufficient_evidence", "scope":"component", "requirement_basis":"unavailable",
                "coverage_status":"partial", "alternative_explanations":[limitation], "missing_context":[limitation],
                "clarification_question":"Review the source-window observations with their component context.",
                "suggested_category":"incomplete_or_anonymized_evidence"}
        fields = ("shared_observations","material_differences","contradicted_analyst_claims")
    for field in fields:
        seen, claims = set(), []
        for out in outputs:
            for claim in out[field]:
                key = json.dumps(claim,sort_keys=True)
                if key not in seen:
                    claims.append(claim); seen.add(key)
        data[field] = claims
    usage["partition"] = {"version":PARTITION_VERSION, "windows":records, "errors":errors,
                          "rejected_claims":rejected, "global_reasoning_verified":False,
                          "all_planned_windows_completed":not errors,
                          "dependency_scope":"direct module definitions when budget permits; transitive dependencies unverified"}
    return model_cls.model_validate(data), StructuredResult(data,json.dumps(data),provider.name,provider.model,
                 digest,provider.name=="mock",usage,duration)
