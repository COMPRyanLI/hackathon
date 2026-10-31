"""Typed, line-anchored source facts and component-role inference (P1).

Facts are observations computed by application code from the AST, never by a
model, and nothing is executed. A fact that cannot be determined is ``None``
(unknown), never ``False``. Roles are inferred from code evidence only; file
names and suffixes are never consulted.
"""
from __future__ import annotations

import ast
import re
from typing import Any

from app.extraction.static import PLACEHOLDER_RE

FACTS_VERSION = "facts-v2"
MAPPER_VERSION = "roles-v1"

# Scalar facts usable in typed conditions, with their value type.
SCALAR_FACTS: dict[str, type] = {
    "parse_ok": bool,
    "header_comment_present": bool,
    "header_has_placeholder": bool,
    "while_loop_present": bool,
    "for_loop_present": bool,
    "loop_inclusive_bound": bool,
    "loop_counter_start": float,
    "loop_start_offset": float,
    "loop_compares_two_variables": bool,
    "uses_pow": bool,
    "uppercase_constant_count": float,
    "distinct_rate_literal_count": float,
    "rate_uses_multiplier_form": bool,
    "function_def_count": float,
    "start_graphics_without_size": bool,
    "comment_line_count": float,
    "graphics_callback_keyword_count": float,
    "graphics_framerate": float,
    # Candidate SA6 reason-taxonomy evidence (descriptive facts, not official rubric weights).
    "diagonal_check_present": bool,
    "diagonal_check_complete": bool,
    "backtracking_present": bool,
}


def _const_value(node: ast.AST, constants: dict[str, float]) -> float | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _const_value(node.operand, constants)
        return None if inner is None else -inner
    return None


def _plus_one(node: ast.AST) -> bool:
    return (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
            and isinstance(node.right, ast.Constant) and node.right.value == 1)


def _header(lines: list[str]) -> dict[str, Any]:
    comment_lines, placeholder = [], False
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            comment_lines.append(number)
            placeholder = placeholder or bool(PLACEHOLDER_RE.search(stripped))
            continue
        if stripped.startswith(('"""', "'''")) and not comment_lines:
            comment_lines.append(number)  # module docstring used as a header
            placeholder = placeholder or bool(PLACEHOLDER_RE.search(stripped))
        break
    return {"present": bool(comment_lines), "lines": comment_lines[:10], "placeholder": placeholder}


def artifact_facts(source: str) -> dict[str, Any]:
    lines = source.splitlines()
    facts: dict[str, Any] = {
        "facts_version": FACTS_VERSION,
        "header": _header(lines),
        "comment_line_count": sum(1 for line in lines if line.lstrip().startswith("#")),
    }
    try:
        tree = ast.parse(source)  # inspection only
    except SyntaxError as exc:
        facts.update(parse_ok=False, parse_error_line=exc.lineno, loops=[], pow_uses=[], constants=[],
                     rate_literals=[], function_defs=[], start_graphics=[])
        return facts

    constants: dict[str, float] = {}
    constant_rows = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            value = _const_value(node.value, constants)
            if value is not None and name.isupper():
                constants[name] = value
                constant_rows.append({"name": name, "line": node.lineno, "value": value})

    # Last literal assignment to each simple name before a given line (for loop counters).
    assignments: dict[str, list[tuple[int, float | None]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            assignments.setdefault(node.targets[0].id, []).append(
                (node.lineno, _const_value(node.value, constants)))

    def start_value(name: str, before_line: int) -> float | None:
        prior = [v for line, v in assignments.get(name, []) if line < before_line]
        return prior[-1] if prior else None

    loops = []
    pow_uses = []
    loop_spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.While):
            row: dict[str, Any] = {"kind": "while", "line": node.lineno, "inclusive_bound": None,
                                   "counter": None, "counter_start": None, "start_offset": None,
                                   "compares_two_variables": None}
            test = node.test
            if isinstance(test, ast.Compare) and len(test.ops) == 1:
                left, right, op = test.left, test.comparators[0], test.ops[0]
                if isinstance(left, ast.Name):
                    row["counter"] = left.id
                    row["counter_start"] = start_value(left.id, node.lineno)
                changed = {n.targets[0].id for n in ast.walk(node) if isinstance(n, ast.Assign)
                           and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
                changed |= {n.target.id for n in ast.walk(node) if isinstance(n, ast.AugAssign)
                            and isinstance(n.target, ast.Name)}
                # Two values that both change inside the loop (e.g. two growing balances),
                # as opposed to a counter compared with a fixed limit.
                row["compares_two_variables"] = bool(
                    isinstance(left, ast.Name) and isinstance(right, ast.Name)
                    and left.id in changed and right.id in changed)
                if isinstance(op, (ast.LtE, ast.GtE)):
                    row["inclusive_bound"] = True
                elif isinstance(op, (ast.Lt, ast.Gt, ast.NotEq)):
                    row["inclusive_bound"] = False
            loops.append(row)
            loop_spans.append((node.lineno, getattr(node, "end_lineno", node.lineno)))
        elif isinstance(node, ast.For):
            row = {"kind": "for", "line": node.lineno, "inclusive_bound": None,
                   "counter": node.target.id if isinstance(node.target, ast.Name) else None,
                   "counter_start": None, "start_offset": None, "compares_two_variables": False}
            it = node.iter
            if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range" and it.args:
                args = it.args
                end = args[1] if len(args) >= 2 else args[0]
                row["inclusive_bound"] = _plus_one(end)
                if len(args) >= 2:
                    row["counter_start"] = _const_value(args[0], constants)
                    row["start_offset"] = 1.0 if _plus_one(args[0]) else 0.0
                else:
                    row["counter_start"], row["start_offset"] = 0.0, 0.0
            loops.append(row)
            loop_spans.append((node.lineno, getattr(node, "end_lineno", node.lineno)))

    def in_loop(line: int) -> bool:
        return any(a <= line <= b for a, b in loop_spans)

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            pow_uses.append({"line": node.lineno, "kind": "operator", "in_loop": in_loop(node.lineno)})
        elif isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Name) and node.func.id == "pow")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "pow")):
            pow_uses.append({"line": node.lineno, "kind": "call", "in_loop": in_loop(node.lineno)})

    # Distinct non-integer literals in (0, 2): interest rates written as 0.05 or 1.05,
    # whether named constants or inline.
    rate_literals = sorted({round(float(n.value), 6) for n in ast.walk(tree)
                            if isinstance(n, ast.Constant) and isinstance(n.value, float)
                            and 0 < n.value < 2 and n.value != 1})
    starts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "start_graphics":
            keywords = {k.arg for k in node.keywords}
            starts.append({"line": node.lineno, "positional_args": len(node.args),
                           "callback_keywords": sorted(k for k in keywords if k and
                               (k.startswith("mouse_") or k.startswith("key_"))),
                           "framerate": next((_const_value(k.value, constants) for k in node.keywords
                                               if k.arg == "framerate"), None),
                           "has_size": len(node.args) >= 3 or {"width", "height"} <= keywords})
    facts.update(_puzzle_facts(tree))
    facts.update(
        parse_ok=True,
        loops=sorted(loops, key=lambda r: r["line"]),
        pow_uses=sorted(pow_uses, key=lambda r: r["line"]),
        constants=constant_rows,
        rate_literals=rate_literals,
        function_defs=[{"name": n.name, "line": n.lineno} for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))],
        start_graphics=starts,
    )
    return facts


def _is_abs_of_difference(node: ast.AST) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "abs"
            and len(node.args) == 1 and isinstance(node.args[0], ast.BinOp) and isinstance(node.args[0].op, ast.Sub))


def _puzzle_facts(tree: ast.Module) -> dict[str, Any]:
    """Diagonal checks and backtracking, as structural observations."""
    abs_lines, sub_lines, add_lines = [], [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
            left, right = node.left, node.comparators[0]
            if _is_abs_of_difference(left) and _is_abs_of_difference(right):
                abs_lines.append(node.lineno)
            elif all(isinstance(x, ast.BinOp) and isinstance(x.op, ast.Sub) for x in (left, right)):
                sub_lines.append(node.lineno)
            elif all(isinstance(x, ast.BinOp) and isinstance(x.op, ast.Add) for x in (left, right)):
                add_lines.append(node.lineno)
    backtracking: bool | None = None
    backtrack_lines: list[int] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        recursive = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                     and n.func.id == fn.name]
        if not recursive:
            continue
        undo = [n.lineno for n in ast.walk(fn)
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "pop")
                or (isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Subscript)
                    and isinstance(n.value, ast.Constant) and n.value.value in (0, None, False))]
        backtracking = bool(undo) or bool(backtracking)
        backtrack_lines += undo or [recursive[0].lineno]
    return {"diagonal": {"abs_lines": abs_lines, "sub_lines": sub_lines, "add_lines": add_lines},
            "backtracking": {"present": backtracking, "lines": backtrack_lines}}


def scalar_fact(facts: dict[str, Any], name: str) -> Any:
    """Typed scalar view. Returns None when the fact cannot be determined."""
    if name == "parse_ok":
        return facts.get("parse_ok")
    if name == "header_comment_present":
        return facts["header"]["present"]
    if name == "header_has_placeholder":
        return facts["header"]["placeholder"]
    if name == "comment_line_count":
        return float(facts.get("comment_line_count", 0))
    if not facts.get("parse_ok"):
        return None
    loops = facts["loops"]
    if name == "while_loop_present":
        return any(l["kind"] == "while" for l in loops)
    if name == "for_loop_present":
        return any(l["kind"] == "for" for l in loops)
    if name == "loop_inclusive_bound":
        known = [l["inclusive_bound"] for l in loops if l["inclusive_bound"] is not None]
        return None if not known else any(known)
    if name in ("loop_counter_start", "loop_start_offset"):
        key = "counter_start" if name == "loop_counter_start" else "start_offset"
        bounded = [l for l in loops if l["inclusive_bound"]] or [l for l in loops if l["inclusive_bound"] is not None]
        return bounded[0][key] if bounded else None
    if name == "loop_compares_two_variables":
        known = [l["compares_two_variables"] for l in loops if l["compares_two_variables"] is not None]
        return None if not known else any(known)
    if name == "uses_pow":
        return bool(facts["pow_uses"])
    if name == "uppercase_constant_count":
        return float(len(facts["constants"]))
    if name == "distinct_rate_literal_count":
        return float(len(facts["rate_literals"]))
    if name == "rate_uses_multiplier_form":
        rates = facts["rate_literals"]
        return None if not rates else any(r > 1 for r in rates)
    if name == "function_def_count":
        return float(len(facts["function_defs"]))
    if name == "start_graphics_without_size":
        calls = facts["start_graphics"]
        return None if not calls else any(not c["has_size"] for c in calls)
    if name == "graphics_callback_keyword_count":
        calls = facts["start_graphics"]
        return None if not calls else float(sum(len(c["callback_keywords"]) for c in calls))
    if name == "graphics_framerate":
        calls = facts["start_graphics"]
        values = {c["framerate"] for c in calls}
        return next(iter(values)) if len(values) == 1 else None
    if name in ("diagonal_check_present", "diagonal_check_complete"):
        d = facts.get("diagonal", {})
        present = bool(d.get("abs_lines") or d.get("sub_lines") or d.get("add_lines"))
        if name == "diagonal_check_present":
            return present
        return bool(d.get("abs_lines")) or (bool(d.get("sub_lines")) and bool(d.get("add_lines")))
    if name == "backtracking_present":
        return facts.get("backtracking", {}).get("present")  # None when no recursion was found
    raise KeyError(name)


def fact_lines(facts: dict[str, Any], name: str) -> list[int]:
    """Source lines supporting a fact, for evidence highlighting."""
    if name in ("header_comment_present", "header_has_placeholder"):
        return facts["header"]["lines"]
    if name.startswith("loop") or name in ("while_loop_present", "for_loop_present"):
        return [l["line"] for l in facts.get("loops", [])]
    if name == "uses_pow":
        return [p["line"] for p in facts.get("pow_uses", [])]
    if name in ("uppercase_constant_count", "distinct_rate_literal_count", "rate_uses_multiplier_form"):
        return [c["line"] for c in facts.get("constants", [])]
    if name == "function_def_count":
        return [f["line"] for f in facts.get("function_defs", [])]
    if name == "start_graphics_without_size" or name.startswith("graphics_"):
        return [c["line"] for c in facts.get("start_graphics", [])]
    if name.startswith("diagonal"):
        d = facts.get("diagonal", {})
        return sorted(d.get("abs_lines", []) + d.get("sub_lines", []) + d.get("add_lines", []))
    if name == "backtracking_present":
        return facts.get("backtracking", {}).get("lines", [])
    return []


# ---------------------------------------------------------------- component roles


def _sa3_scores(facts: dict[str, Any]) -> tuple[int, int, list[str]]:
    """Return (program_1 score, program_2 score, reasons) from code evidence."""
    if not facts.get("parse_ok"):
        return 0, 0, ["parse failed; role cannot be inferred from structure"]
    reasons = []
    p1 = p2 = 0
    rates = len(facts["rate_literals"])
    two_vars = scalar_fact(facts, "loop_compares_two_variables")
    counter_bound = any(l["counter"] and not l["compares_two_variables"] for l in facts["loops"])
    if rates >= 2:
        p2 += 1
        reasons.append(f"{rates} distinct rate constants (two accounts)")
    elif rates == 1:
        p1 += 1
        reasons.append("one rate constant (single account)")
    if two_vars:
        p2 += 2
        reasons.append("loop compares two changing balances")
    elif counter_bound:
        p1 += 1
        reasons.append("loop bounded by a counter against a fixed limit")
    return p1, p2, reasons


def infer_roles(assignment_code: str, artifacts: list[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Propose a role per artifact of one bundle. Ambiguity is reported, not resolved."""
    if assignment_code.upper() != "SA3":
        role = "whole_assignment"
        status = "proposed" if len(artifacts) == 1 else "ambiguous"
        note = ("single file; completeness of the submission is not established by file count"
                if len(artifacts) == 1 else "several files; component scope unconfirmed")
        return [{"artifact_id": a, "role": role if status == "proposed" else "ambiguous", "status": status,
                 "evidence": [note]} for a, _ in artifacts]
    scored = []
    for artifact_id, facts in artifacts:
        p1, p2, reasons = _sa3_scores(facts)
        guess = "program_1" if p1 > p2 else "program_2" if p2 > p1 else None
        loops = facts.get("loops", [])
        both_shapes = (any(l["compares_two_variables"] for l in loops)
                       and any(l["counter"] and not l["compares_two_variables"] for l in loops))
        if guess and (both_shapes or (min(p1, p2) > 0 and abs(p1 - p2) < 2)):
            guess = None
            reasons.append("evidence for both programs in one file")
        scored.append({"artifact_id": artifact_id, "guess": guess, "reasons": reasons, "scores": [p1, p2]})
    guesses = [s["guess"] for s in scored]
    out = []
    for s in scored:
        clash = s["guess"] is not None and guesses.count(s["guess"]) > 1
        if s["guess"] is None or clash:
            out.append({"artifact_id": s["artifact_id"], "role": "ambiguous", "status": "ambiguous",
                        "evidence": s["reasons"] + (["another file in this bundle has the same proposed role"]
                                                    if clash else [])})
        else:
            out.append({"artifact_id": s["artifact_id"], "role": s["guess"], "status": "proposed",
                        "evidence": s["reasons"]})
    return out


SAFE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|\S")


def safe_token_ngrams(source: str, n: int = 3) -> list[str]:
    """Baseline token n-grams that keep literals, case, '#' inside strings and
    indentation markers (contrast with regex comment stripping)."""
    import io
    import tokenize

    tokens: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.NL, tokenize.ENDMARKER):
                continue
            if tok.type == tokenize.INDENT:
                tokens.append("<INDENT>")
            elif tok.type == tokenize.DEDENT:
                tokens.append("<DEDENT>")
            elif tok.type == tokenize.NEWLINE:
                tokens.append("<NL>")
            else:
                tokens.append(tok.string)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        tokens = SAFE_TOKEN_RE.findall(source)
    return [" ".join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1))] or tokens
