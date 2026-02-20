from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Sequence, Tuple


def count_substring(full_text: str, substring: str, *, case_insensitive: bool = True) -> int:
    s = full_text or ""
    sub = substring or ""
    if not s or not sub:
        return 0
    if case_insensitive:
        return s.lower().count(sub.lower())
    return s.count(sub)


def count_regex(full_text: str, pattern: str, *, case_insensitive: bool = True, multiline: bool = True, dotall: bool = False) -> int:
    s = full_text or ""
    if not s or not pattern:
        return 0
    flags = 0
    if case_insensitive:
        flags |= re.IGNORECASE
    if multiline:
        flags |= re.MULTILINE
    if dotall:
        flags |= re.DOTALL
    try:
        return len(re.findall(pattern, s, flags=flags))
    except Exception:
        return 0


def extract_dates(full_text: str) -> List[str]:
    s = full_text or ""
    if not s:
        return []
    # dd/mm/yyyy, dd-mm-yyyy, yyyy-mm-dd
    patterns = [
        r"\b\d{2}/\d{2}/\d{4}\b",
        r"\b\d{2}-\d{2}-\d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
    ]
    out: List[str] = []
    seen = set()
    for p in patterns:
        for m in re.findall(p, s):
            if m in seen:
                continue
            seen.add(m)
            out.append(m)
    return out


def extract_money(full_text: str) -> List[str]:
    s = full_text or ""
    if not s:
        return []
    # Brazilian currency: R$ 1.234,56
    rx = re.compile(r"R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}")
    out: List[str] = []
    seen = set()
    for m in rx.findall(s):
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


def extract_installments(full_text: str) -> List[str]:
    s = full_text or ""
    if not s:
        return []
    patterns = [
        r"\b\d{1,2}\s*[xX]\s*(?:de\s*)?R\$\s*\d{1,3}(?:\.\d{3})*,\d{2}\b",
        r"\b\d{1,2}\s*parcelas?\b",
        r"\bparcela\s*\d{1,2}\s*/\s*\d{1,2}\b",
    ]
    out: List[str] = []
    seen = set()
    for p in patterns:
        for m in re.findall(p, s, flags=re.IGNORECASE):
            mm = str(m).strip()
            if not mm or mm in seen:
                continue
            seen.add(mm)
            out.append(mm)
    return out


def find_lines_with_keyword(full_text: str, keyword: str, *, window: int = 2, case_insensitive: bool = True, max_hits: int = 80) -> List[str]:
    s = full_text or ""
    kw = (keyword or "").strip()
    if not s or not kw:
        return []

    lines = s.splitlines()
    out: List[str] = []
    seen = set()
    needle = kw.lower() if case_insensitive else kw

    for i, line in enumerate(lines):
        hay = line.lower() if case_insensitive else line
        if needle not in hay:
            continue
        a = max(0, i - max(0, int(window)))
        b = min(len(lines), i + max(0, int(window)) + 1)
        block = "\n".join(lines[a:b]).strip()
        if not block or block in seen:
            continue
        seen.add(block)
        out.append(block)
        if len(out) >= max_hits:
            break
    return out


@dataclass
class TableBlock:
    kind: str
    text: str


def extract_table_like_blocks(full_text: str) -> List[TableBlock]:
    """
    Best-effort: find contiguous blocks that look like tables (CSV/TSV/pipe aligned).
    """
    s = full_text or ""
    if not s:
        return []

    lines = s.splitlines()
    blocks: List[TableBlock] = []

    def looks_table_line(line: str) -> Tuple[bool, str]:
        l = line.rstrip("\n")
        if l.count(",") >= 2:
            return True, "csv"
        if "\t" in l:
            return True, "tsv"
        if l.count("|") >= 2:
            return True, "pipe"
        # many aligned columns (multiple spaces) with digits
        if re.search(r"\d", l) and re.search(r"\s{2,}", l):
            return True, "aligned"
        return False, ""

    cur: List[str] = []
    cur_kind = ""
    for line in lines:
        ok, kind = looks_table_line(line)
        if ok:
            if not cur:
                cur_kind = kind
            cur.append(line)
            continue
        if cur:
            text = "\n".join(cur).strip()
            if text:
                blocks.append(TableBlock(kind=cur_kind or "table", text=text))
            cur = []
            cur_kind = ""
    if cur:
        text = "\n".join(cur).strip()
        if text:
            blocks.append(TableBlock(kind=cur_kind or "table", text=text))

    return blocks


def _join_blocks(blocks: Sequence[TableBlock]) -> str:
    return "\n\n".join([b.text for b in blocks if b.text]).strip()

