import re
from dataclasses import dataclass


@dataclass
class SearchTerm:
    value: str
    exclude: bool = False
    phrase: bool = False


@dataclass
class SearchGroup:
    terms: list[SearchTerm]
    op: str = "AND"


def _strip_quotes(token: str) -> tuple[str, bool]:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        return token[1:-1], True
    return token, False


def tokenize_query(query: str) -> list[str]:
    pattern = re.compile(r'"[^"]+"|\'[^\']+\'|\(|\)|\bOR\b|[^\s()]+', re.IGNORECASE)
    return [m.group(0) for m in pattern.finditer(query.strip()) if m.group(0).strip()]


def parse_query(query: str) -> list[SearchGroup]:
    tokens = tokenize_query(query)
    if not tokens:
        return []

    groups: list[SearchGroup] = []
    current = SearchGroup(terms=[], op="AND")
    or_mode = False
    paren_stack: list[SearchGroup] = []

    def flush_current() -> None:
        nonlocal current
        if current.terms:
            groups.append(current)
        current = SearchGroup(terms=[], op="AND")

    for raw in tokens:
        upper = raw.upper()
        if raw == "(":
            paren_stack.append(current)
            current = SearchGroup(terms=[], op="AND")
            or_mode = False
            continue
        if raw == ")":
            flush_current()
            if paren_stack:
                parent = paren_stack.pop()
                if current.terms:
                    parent.terms.append(SearchTerm(value="__group__", phrase=True))
                current = parent
            or_mode = False
            continue
        if upper == "OR":
            or_mode = True
            current.op = "OR"
            continue

        exclude = raw.startswith("-")
        token = raw[1:] if exclude else raw
        value, phrase = _strip_quotes(token)
        if not value:
            continue

        term = SearchTerm(value=value, exclude=exclude, phrase=phrase)
        if or_mode and current.terms:
            current.op = "OR"
        current.terms.append(term)
        if not or_mode:
            current.op = "AND"
        or_mode = False

    flush_current()
    return groups


def _fts_escape_term(term: str) -> str:
    return term.replace('"', '""')


def term_to_fts(term: SearchTerm) -> str:
    value = _fts_escape_term(term.value)
    needs_quote = (
        term.phrase
        or any(ch in value for ch in " -:/@.")
        or "_" in value
    )
    return f'"{value}"' if needs_quote else value


def groups_to_fts_match(groups: list[SearchGroup]) -> str:
    if not groups:
        return ""

    parts: list[str] = []
    for group in groups:
        if not group.terms:
            continue
        if group.op == "OR":
            inner = " OR ".join(term_to_fts(term) for term in group.terms)
        else:
            inner = " ".join(term_to_fts(term) for term in group.terms)
        if len(group.terms) > 1 and group.op == "OR":
            inner = f"({inner})"
        parts.append(inner)
    return " ".join(parts)


def split_include_exclude(query: str) -> tuple[str | None, list[str]]:
    groups = parse_query(query)
    exclude_terms: list[str] = []
    include_groups: list[SearchGroup] = []

    for group in groups:
        positive = []
        for term in group.terms:
            if term.exclude:
                exclude_terms.append(term.value)
            else:
                positive.append(term)
        if positive:
            include_groups.append(SearchGroup(terms=positive, op=group.op))

    match = groups_to_fts_match(include_groups)
    return match or None, exclude_terms


def build_works_fts_query(q: str) -> tuple[str | None, list[str]]:
    return split_include_exclude(q)


def build_prompt_fts_query(prompt: str) -> tuple[str | None, list[str]]:
    # NAI 咒语常用逗号分词，FTS 里需先当作空格处理，避免 fts5 syntax error
    normalized = re.sub(r"\s*,\s*", " ", prompt.strip())
    return split_include_exclude(normalized)