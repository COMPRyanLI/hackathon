"""Draft rubric extraction from grading-guide page text.

Produces a *proposed* rubric tree with page/quote provenance. Nothing here is
authoritative: classifications mark what needs interpretation or external
evidence, and validation (``validate.py``) records conflicts instead of
silently fixing them. Only SA1-SA6 are extracted; later sections are out of
scope for this version.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

PARSER_VERSION = "guide-parse-v2-layout"
IN_SCOPE = {f"SA{i}" for i in range(1, 7)}

# "SA3 (how rich) (10 points):", "SA7 Counter-Timer (10 points):", "Lab1 (...) (5 points):"
HEADING_RE = re.compile(r"\b(SA\d+|Lab\s*\d*)\b([^:]{0,60}?)\(\s*(\d+(?:\.\d+)?)\s*points\s*\)\s*:")
ITEM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*points?\s*:\s*")
PROGRAM_RE = re.compile(r"(Program\s*(\d+))\s*\(\s*(\d+(?:\.\d+)?)\s*points\s*\)")
SHARED_ITEM_RE = re.compile(r"(\d)\.\s+(.+?):\s*(\d+(?:\.\d+)?)\s*points")
NOTE_SPLIT_RE = re.compile(r"(?:^|\s)(\d)\)\s+")

EXTERNAL_RE = re.compile(r"screenshot|output|observation|specified in the assignment|code that I provided"
                         r"|\brequired\b", re.I)
INTERPRET_RE = re.compile(r"\bcorrect|correctness|working code|how close|good variable|self-explanatory"
                          r"|draws the strings|syntax we do not teach|libraries we do not expect", re.I)
BINDINGS = [
    (re.compile(r"upper-case variable names", re.I), "uppercase_constants"),
    (re.compile(r"defining constants", re.I), "uppercase_constants"),
    (re.compile(r"setting up the loop", re.I), "loop_setup"),
    (re.compile(r"header comments", re.I), "header_comment"),
    (re.compile(r"while loop", re.I), "while_loop_policy"),
    (re.compile(r"exponentiation|simple interest", re.I), "pow_policy"),
    (re.compile(r"constants for window size|start_graphics", re.I), "start_graphics_size"),
]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def slug(text: str, words: int = 5) -> str:
    stop = {"for", "the", "a", "an", "of", "if", "they", "it", "is", "to", "and", "in", "using", "points", "point"}
    tokens = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in stop]
    return "_".join(tokens[:words]) or "item"


@dataclass
class DraftCriterion:
    key: str
    parent_key: str | None
    assignment: str
    title: str
    page: int
    source_quote: str
    max_points: Decimal | None
    rule_kind: str
    classification: str
    component_role: str | None = None
    applies_to: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    fact_binding: str | None = None
    is_container: bool = False
    phrased_as_award: bool = False


@dataclass
class ParsedGuide:
    criteria: list[DraftCriterion]
    headings: dict[str, Decimal]
    raw_sections: dict[str, str]
    shared_text: str


def _page_of(offset: int, starts: list[int]) -> int:
    page = 1
    for index, start in enumerate(starts, start=1):
        if offset >= start:
            page = index
    return page


def _classify(text: str, kind: str) -> str:
    if kind in ("resubmit", "hold"):
        return "interpretation_required"
    text = re.split(r"(?<=[a-z)])\.\s", text, maxsplit=1)[0]  # first sentence carries the criterion
    if EXTERNAL_RE.search(text):
        return "external_evidence_required"
    if INTERPRET_RE.search(text):
        return "interpretation_required"
    return "explicit"


def _binding(text: str) -> str | None:
    for pattern, name in BINDINGS:
        if pattern.search(text):
            return name
    return None


def parse_guide(pages: list[str]) -> ParsedGuide:
    texts = [normalize(p) for p in pages]
    starts, joined, item_indents = [], "", {}
    for raw_page, text in zip(pages, texts):
        starts.append(len(joined))
        # Retain source indentation at each point-item offset before flattening
        # prose for the existing heading/quote parser. Wrapped lines are not items.
        cursor = 0
        for line in raw_page.splitlines():
            normalized_line = normalize(line)
            if not normalized_line:
                continue
            position = text.find(normalized_line, cursor)
            if position < 0:
                continue
            if ITEM_RE.match(normalized_line) and len(list(ITEM_RE.finditer(normalized_line))) == 1:
                item_indents[len(joined) + position] = len(line.expandtabs()) - len(line.expandtabs().lstrip())
            cursor = position + len(normalized_line)
        joined += text + " "
    criteria: list[DraftCriterion] = []
    used: set[str] = set()

    def unique(key: str) -> str:
        base, n = key, 2
        while key in used:
            key, n = f"{base}_{n}", n + 1
        used.add(key)
        return key

    headings = [m for m in HEADING_RE.finditer(joined)]
    first = headings[0].start() if headings else len(joined)
    shared = joined[:first]

    # Shared good-coding-practice items.
    criteria.append(DraftCriterion("shared.practice", None, "shared", "Good coding practice (shared)",
                                   _page_of(0, starts), shared[:160], Decimal("2") if "2 out of 10" in shared else None,
                                   "award", "explicit", is_container=True))
    used.add("shared.practice")
    for m in SHARED_ITEM_RE.finditer(shared):
        title, points = m.group(2).strip(), Decimal(m.group(3))
        exempt = re.search(r"\(doesn'?t apply for ([^)]*)\)", title, re.I)
        applies = []
        if exempt:
            excluded = re.findall(r"SA\d+", exempt.group(1))
            applies = [a for a in sorted(IN_SCOPE) if a not in excluded]
        criteria.append(DraftCriterion(
            unique(f"shared.practice.{slug(title, 3)}"), "shared.practice", "shared", title,
            _page_of(m.start(), starts), m.group(0), points, "award", _classify(title, "award"),
            applies_to=applies, fact_binding=_binding(title)))
    for sentence in re.split(r"(?<=\.)\s+", shared):
        if re.search(r"resubmit", sentence, re.I):
            criteria.append(DraftCriterion(unique("shared.rule.untaught_syntax_resubmit"), None, "shared", sentence,
                                           _page_of(joined.find(sentence), starts), sentence, None, "resubmit",
                                           "interpretation_required"))
        elif re.search(r"deduct\s+(\d+)\s+point", sentence, re.I):
            criteria.append(DraftCriterion(unique("shared.rule.uncited_library_deduction"), None, "shared", sentence,
                                           _page_of(joined.find(sentence), starts), sentence, Decimal(
                                               re.search(r"deduct\s+(\d+)", sentence, re.I).group(1)),
                                           "deduction", "interpretation_required"))

    heading_points: dict[str, Decimal] = {}
    raw_sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        code = heading.group(1)
        end = headings[index + 1].start() if index + 1 < len(headings) else len(joined)
        if code not in IN_SCOPE:
            continue
        body_start = heading.end()
        section = joined[body_start:end]
        raw_sections[code] = joined[heading.start():end]
        heading_points[code] = Decimal(heading.group(3))
        root_key = code
        criteria.append(DraftCriterion(root_key, None, code, normalize(heading.group(0)).rstrip(":"),
                                       _page_of(heading.start(), starts), heading.group(0), Decimal(heading.group(3)),
                                       "award", "explicit", is_container=True))
        used.add(root_key)

        notes_text = ""
        note_at = re.search(r"\bNote:\s*", section)
        if note_at:
            notes_text = section[note_at.end():]
            section_main = section[:note_at.start()]
        else:
            section_main = section

        # Program subsections (e.g. SA3 Program1 / Program2).
        parts: list[tuple[str | None, str, int, Decimal | None]] = []
        programs = list(PROGRAM_RE.finditer(section_main))
        if programs:
            parts.append((None, section_main[:programs[0].start()], body_start, None))
            for p_index, prog in enumerate(programs):
                p_end = programs[p_index + 1].start() if p_index + 1 < len(programs) else len(section_main)
                parts.append((f"program_{prog.group(2)}", section_main[prog.end():p_end],
                              body_start + prog.end(), Decimal(prog.group(3))))
        else:
            parts.append((None, section_main, body_start, None))

        for role, text, offset, sub_points in parts:
            parent = root_key
            if role:
                parent = unique(f"{code}.{role}")
                criteria.append(DraftCriterion(parent, root_key, code, f"{role.replace('_', ' ').title()}",
                                               _page_of(offset, starts), text[:120].strip(), sub_points, "award",
                                               "explicit", component_role=role, is_container=True))
            deduct_at = re.search(r"Deduct points as follows:\s*", text)
            award_text = text[:deduct_at.start()] if deduct_at else text
            deduct_text = text[deduct_at.end():] if deduct_at else ""
            last_award_key = None
            award_indent = None
            resumed_awards = False
            for kind, chunk, chunk_offset in (("award", award_text, offset), ("deduction", deduct_text,
                                                                               offset + (deduct_at.end() if deduct_at else 0))):
                items = list(ITEM_RE.finditer(chunk))
                for i_index, item in enumerate(items):
                    i_end = items[i_index + 1].start() if i_index + 1 < len(items) else len(chunk)
                    title = chunk[item.end():i_end].strip()
                    quote = chunk[item.start():i_end].strip()
                    points = Decimal(item.group(1))
                    if kind == "award" and re.search(r"good coding practice", title, re.I):
                        key = unique(f"{code}.practice")
                        criteria.append(DraftCriterion(
                            key, parent, code, title, _page_of(chunk_offset + item.start(), starts), quote, points,
                            "award", "explicit", component_role=role, dependencies=["shared.practice"],
                            is_container=True))
                        continue
                    warning = re.search(r"leave a warning", title, re.I)
                    indent = item_indents.get(chunk_offset + item.start())
                    if kind == "award" and indent is not None:
                        award_indent = indent
                    if kind == "deduction":
                        if indent is not None and award_indent is not None and indent <= award_indent:
                            resumed_awards = True
                        elif indent is None and re.match(r"For using constants for window size\b", title, re.I) and warning:
                            # Compatibility for previously stored flat text of this
                            # guide: the independently confirmed award/warning clause.
                            # Do not infer hierarchy merely to make arithmetic balance.
                            resumed_awards = True
                    item_kind = "award" if kind == "award" or resumed_awards else "deduction"
                    phrased_award = item_kind == "deduction" and bool(re.match(r"For\b", title))
                    item_parent = (last_award_key or parent) if item_kind == "deduction" else parent
                    key = unique(f"{code}.{role or 'main'}.{slug(title)}")
                    exceptions = [warning.group(0)] if warning else []
                    criteria.append(DraftCriterion(
                        key, item_parent, code, title, _page_of(chunk_offset + item.start(), starts), quote, points,
                        item_kind, _classify(title, item_kind), component_role=role, exceptions=exceptions,
                        fact_binding=_binding(title), phrased_as_award=phrased_award))
                    if item_kind == "award":
                        last_award_key = key
                    if warning:
                        criteria.append(DraftCriterion(
                            unique(f"{key}.warning"), key, code, warning.group(0), _page_of(chunk_offset + item.start(),
                                                                                             starts),
                            quote, Decimal("0"), "warning", "explicit", component_role=role,
                            fact_binding=_binding(title)))

        if notes_text:
            numbered = NOTE_SPLIT_RE.split(" " + notes_text)
            notes = [n.strip() for n in numbered[2::2]] if len(numbered) > 1 else [notes_text.strip()]
            for note in notes:
                note_offset = joined.find(note[:40]) if note else body_start
                if re.search(r"give them (?:a )?0|resubmit", note, re.I):
                    kind = "resubmit"
                elif re.search(r"\bok(?:ay)?\b", note, re.I):
                    kind = "allowance"
                else:
                    kind = "info"
                criteria.append(DraftCriterion(
                    unique(f"{code}.note.{slug(note, 4)}"), root_key, code, note, _page_of(note_offset, starts), note,
                    None, kind, "interpretation_required" if kind == "resubmit" else "explicit",
                    fact_binding=_binding(note)))
        # Practice-level escalation hints inside items ("Check with me if required").
    return ParsedGuide(criteria, heading_points, raw_sections, shared)


def criteria_as_dicts(parsed: ParsedGuide) -> list[dict[str, Any]]:
    return [{**c.__dict__, "max_points": None if c.max_points is None else str(c.max_points)} for c in parsed.criteria]
