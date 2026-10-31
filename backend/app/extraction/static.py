"""Deterministic static extraction. Parsing is inspection only: submissions are
never imported, compiled to bytecode, executed or evaluated.

Three representations are kept separate:
  * original source text (evidence; never stripped or normalised),
  * lexical tokens (retrieval),
  * AST feature counts (retrieval; unavailable when parsing fails).
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
import warnings
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

PARSER_VERSION = "static-v1-py3.12"
CHUNK_MAX_LINES = 40

# Generic bracketed placeholders introduced by anonymisation, e.g. [NAME].
PLACEHOLDER_RE = re.compile(r"\[[A-Z][A-Z_]{1,24}\]")
AUTHOR_LINE_RE = re.compile(r"^\s*#\s*(author|name|by|student)\s*:?\s*(.*)$", re.IGNORECASE)

# Features usable in deterministic note conditions. AST features are absent
# (not zero) when parsing fails.
TEXT_FEATURES = ("line_count", "nonblank_line_count", "comment_line_count", "text_print_call_count",
                 "placeholder_count", "parse_ok")
AST_FEATURES = ("print_call_count", "loop_count", "function_def_count", "branch_count", "import_count",
                "string_literal_count", "top_level_statement_count", "max_depth", "uses_cs1lib",
                "call_count")


@dataclass
class IssueDraft:
    kind: str
    severity: str
    line: int | None
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkDraft:
    key: str
    start_line: int
    end_line: int
    text: str


@dataclass
class ExtractionResult:
    parse_status: str
    parse_error: dict[str, Any] | None
    features: dict[str, Any]
    issues: list[IssueDraft]
    chunks: list[ChunkDraft]


def decode_source(data: bytes) -> tuple[str, str, list[IssueDraft]]:
    """Decode without normalising newlines or whitespace."""
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
        if text.startswith("﻿"):
            encoding = "utf-8-sig"
        return text, encoding, []
    except UnicodeDecodeError as exc:
        text = data.decode("latin-1")
        return text, "latin-1", [IssueDraft(
            "decode_fallback", "warning", None,
            "File is not valid UTF-8; decoded as Latin-1 for display. Characters may be misrepresented.",
            {"position": exc.start},
        )]


def make_chunks(text: str, max_lines: int = CHUNK_MAX_LINES) -> list[ChunkDraft]:
    """Line-based chunks whose text is an exact slice of the original source."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return [ChunkDraft("c0", 1, 1, "")]
    chunks = []
    for index, start in enumerate(range(0, len(lines), max_lines)):
        part = lines[start:start + max_lines]
        chunks.append(ChunkDraft(f"c{index}", start + 1, start + len(part), "".join(part)))
    return chunks


def line_count(text: str) -> int:
    return len(text.splitlines()) or (1 if text else 0)


def _placeholder_locations(text: str) -> dict[int, set[str]]:
    """Map line -> {'comment','string','code'} for placeholder occurrences."""
    found: dict[int, set[str]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if not PLACEHOLDER_RE.search(tok.string) and tok.string != "[":
                continue
            if tok.type == tokenize.COMMENT and PLACEHOLDER_RE.search(tok.string):
                found.setdefault(tok.start[0], set()).add("comment")
            elif tok.type in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)) and PLACEHOLDER_RE.search(tok.string):
                for offset, segment in enumerate(tok.string.splitlines()):
                    if PLACEHOLDER_RE.search(segment):
                        found.setdefault(tok.start[0] + offset, set()).add("string")
        # A placeholder split into OP '[' NAME OP ']' tokens is in code position.
        lines = text.splitlines()
        for number, line in enumerate(lines, start=1):
            if PLACEHOLDER_RE.search(line) and number not in found:
                found.setdefault(number, set()).add("code")
        return found
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    for number, line in enumerate(text.splitlines(), start=1):
        for match in PLACEHOLDER_RE.finditer(line):
            before = line[: match.start()]
            if "#" in before and before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
                kind = "comment"
            elif before.count('"') % 2 == 1 or before.count("'") % 2 == 1:
                kind = "string"
            else:
                kind = "code"
            found.setdefault(number, set()).add(kind)
    return found


def _text_features(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    return {
        "line_count": len(lines),
        "nonblank_line_count": sum(1 for line in lines if line.strip()),
        "comment_line_count": sum(1 for line in lines if line.lstrip().startswith("#")),
        "text_print_call_count": len(re.findall(r"\bprint\s*\(", text)),
        "placeholder_count": len(PLACEHOLDER_RE.findall(text)),
    }


class _AstWalker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.node_counts: Counter[str] = Counter()
        self.call_names: Counter[str] = Counter()
        self.imports: set[str] = set()
        self.literals: list[dict[str, Any]] = []
        self.max_depth = 0
        self._depth = 0

    def generic_visit(self, node: ast.AST) -> None:
        self.node_counts[type(node).__name__] += 1
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        super().generic_visit(node)
        self._depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            self.call_names[func.id] += 1
        elif isinstance(func, ast.Attribute):
            self.call_names[func.attr] += 1
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.add(alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.add(node.module.split(".")[0])
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and len(self.literals) < 300:
            self.literals.append({"line": node.lineno, "value": node.value})
        self.generic_visit(node)


def _ast_features(tree: ast.Module) -> dict[str, Any]:
    walker = _AstWalker()
    walker.visit(tree)
    counts = walker.node_counts
    return {
        "node_counts": dict(counts),
        "call_names": dict(walker.call_names),
        "imports": sorted(walker.imports),
        "print_call_count": walker.call_names.get("print", 0),
        "call_count": counts.get("Call", 0),
        "loop_count": counts.get("For", 0) + counts.get("While", 0) + counts.get("AsyncFor", 0)
        + counts.get("comprehension", 0),
        "function_def_count": counts.get("FunctionDef", 0) + counts.get("AsyncFunctionDef", 0),
        "branch_count": counts.get("If", 0) + counts.get("IfExp", 0) + counts.get("Match", 0),
        "import_count": counts.get("Import", 0) + counts.get("ImportFrom", 0),
        "string_literal_count": len(walker.literals),
        "string_literals": walker.literals,
        "top_level_statement_count": len(tree.body),
        "max_depth": walker.max_depth,
        "uses_cs1lib": "cs1lib" in walker.imports,
    }


def extract(text: str) -> ExtractionResult:
    issues: list[IssueDraft] = []
    features = _text_features(text)
    parse_error: dict[str, Any] | None = None
    placeholder_lines = _placeholder_locations(text)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            # ast.parse builds a syntax tree only; nothing is executed.
            tree = ast.parse(text, filename="<artifact>", mode="exec")
        except SyntaxError as exc:
            tree = None
            parse_error = {"line": exc.lineno, "offset": exc.offset, "message": exc.msg}
    for warning in caught:
        if issubclass(warning.category, SyntaxWarning):
            issues.append(IssueDraft(
                "escape_sequence_warning", "info", warning.lineno or None,
                f"Python 3.12 reports: {warning.message}. Behaviour may differ across Python versions; "
                "not treated as an error.",
            ))

    if tree is not None:
        features.update(_ast_features(tree))
        features["parse_ok"] = True
        parse_status = "ok"
    else:
        features["parse_ok"] = False
        parse_status = "syntax_error"
        line = parse_error["line"] if parse_error else None
        lines = text.splitlines()
        error_text = lines[line - 1] if line and 0 < line <= len(lines) else ""
        near_placeholder = bool(PLACEHOLDER_RE.search(error_text)) or (
            line is not None and "code" in placeholder_lines.get(line, set())
        )
        if near_placeholder:
            issues.append(IssueDraft(
                "possible_anonymization_artifact", "warning", line,
                "Parsing failed on a line where an anonymization placeholder appears in code. "
                "This is possible anonymization damage, not a demonstrated student syntax mistake.",
                {"parser_message": parse_error["message"] if parse_error else ""},
            ))
        else:
            issues.append(IssueDraft(
                "syntax_error", "warning", line,
                "Parsing failed under the Python 3.12 grammar. This could reflect a student error, a "
                "Python-version difference, or anonymization; it needs human review. Text-based "
                "retrieval still includes this file.",
                {"parser_message": parse_error["message"] if parse_error else ""},
            ))

    for line_number in sorted(placeholder_lines):
        kinds = placeholder_lines[line_number]
        if "code" in kinds:
            if parse_error and parse_error.get("line") == line_number:
                continue  # already reported with the parse failure
            issues.append(IssueDraft(
                "possible_anonymization_artifact", "warning", line_number,
                "Anonymization placeholder appears in code position (for example inside an identifier). "
                "Possible anonymization damage; do not treat as a student mistake.",
                {"locations": sorted(kinds)},
            ))
        elif "string" in kinds:
            issues.append(IssueDraft(
                "anonymization_placeholder", "info", line_number,
                "Anonymization placeholder inside a string literal. Printed output, spacing and "
                "artwork width may differ from the original submission.",
                {"locations": sorted(kinds)},
            ))
        else:
            issues.append(IssueDraft(
                "anonymization_placeholder", "info", line_number,
                "Anonymization placeholder in a comment.",
                {"locations": sorted(kinds)},
            ))

    for line_number, line in enumerate(text.splitlines(), start=1):
        match = AUTHOR_LINE_RE.match(line)
        if match and match.group(2).strip() and not PLACEHOLDER_RE.search(match.group(2)):
            issues.append(IssueDraft(
                "possible_identifying_text", "info", line_number,
                "Author-style comment without an anonymization placeholder. Check for residual "
                "identifying text before sharing or sending this file to a cloud provider (heuristic).",
            ))

    return ExtractionResult(parse_status, parse_error, features, issues, make_chunks(text))


# ---------------------------------------------------------------- lexical tokens


def _char_ngrams(value: str, n: int = 3) -> list[str]:
    if len(value) < n:
        return [value] if value else []
    return [value[i:i + n] for i in range(len(value) - n + 1)]


def _string_body(token_text: str) -> str:
    match = re.match(r"^[rRbBuUfF]{0,2}('''|\"\"\"|'|\")", token_text)
    if not match:
        return token_text
    quote = match.group(1)
    body = token_text[match.end():]
    return body[: -len(quote)] if body.endswith(quote) else body


def lexical_tokens(text: str) -> list[str]:
    """Code tokens plus character trigrams of string contents and comment words.

    String literals are kept (as trigrams) because in text-art programs the
    strings and their spacing are most of the work.
    """
    out: list[str] = []
    fstring_middle = getattr(tokenize, "FSTRING_MIDDLE", -1)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.NAME:
                out.append("n:" + tok.string)
            elif tok.type == tokenize.OP:
                out.append("o:" + tok.string)
            elif tok.type == tokenize.NUMBER:
                out.append("d:" + tok.string)
            elif tok.type == tokenize.STRING:
                out.append("str")
                out.extend("s:" + g for g in _char_ngrams(_string_body(tok.string)))
            elif tok.type == fstring_middle:
                out.extend("s:" + g for g in _char_ngrams(tok.string))
            elif tok.type == tokenize.COMMENT:
                out.extend("c:" + w.lower() for w in re.findall(r"\w+", tok.string))
        return out
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return ["r:" + t for t in re.findall(r"[A-Za-z_]\w*|\d+|\S", text)]


def structural_vector(features: dict[str, Any]) -> dict[str, float] | None:
    """Explicit AST feature counts; None when the AST is unavailable."""
    if not features.get("parse_ok"):
        return None
    vector: dict[str, float] = {}
    for name, count in features.get("node_counts", {}).items():
        vector[f"node:{name}"] = float(count)
    for name, count in features.get("call_names", {}).items():
        vector[f"call:{name}"] = float(count)
    for module in features.get("imports", []):
        vector[f"import:{module}"] = 1.0
    return vector
