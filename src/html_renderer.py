"""Render a Self-Report Markdown file to a styled, standalone HTML page.

Self-contained — no external Markdown dependency. Supports the subset of
Markdown the reports actually use: headings, paragraphs, tables, blockquotes
(including GitHub `[!WARNING]` alerts), unordered/ordered lists, horizontal
rules, fenced code blocks (with Mermaid diagrams rendered via CDN), and inline
`**bold**`, `*italic*`, `` `code` `` and links.

The page is laid out as a "report card": a hero banner with the subject and
headline numbers, a "By the Numbers" stat grid, and the report body split into
tabs (Overview / AI Analysis / Metrics). The hero and stat grid are built from
the structured `data-<stem>.json` when available; the body is rendered from the
Markdown so `make html-*` keeps working from the `.md` alone.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .renderer import CREDITS_ANCHOR
from .utils import log

# ---------------------------------------------------------------------------
# Inline formatting
# ---------------------------------------------------------------------------

_LINK_RE   = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE   = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_CODE_RE   = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Escape HTML then re-apply inline Markdown formatting."""
    # Protect inline code spans from escaping their inner markup.
    code_spans: list[str] = []

    def _stash_code(m: re.Match) -> str:
        code_spans.append(m.group(1))
        return f"\x00{len(code_spans) - 1}\x00"

    text = _CODE_RE.sub(_stash_code, text)
    text = html.escape(text, quote=False)
    text = _LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>', text
    )
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    # Restore code spans (escaped) last.
    text = re.sub(
        r"\x00(\d+)\x00",
        lambda m: f"<code>{html.escape(code_spans[int(m.group(1))], quote=False)}</code>",
        text,
    )
    return text


# ---------------------------------------------------------------------------
# Block parsing
# ---------------------------------------------------------------------------

_ALERT_RE     = re.compile(r"^\[!(\w+)\]\s*(.*)$")
_UL_RE        = re.compile(r"^[-*+]\s+")
_OL_RE        = re.compile(r"^\d+\.\s+")
_LIST_MARK_RE = re.compile(r"^([-*+]|\d+\.)\s+")


def _is_list_item(s: str) -> bool:
    return bool(_UL_RE.match(s) or _OL_RE.match(s))


def _is_table_sep(line: str) -> bool:
    return bool(re.match(r"^\|?[\s:|-]+\|?$", line.strip())) and "-" in line


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _md_to_html(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Blank line
        if not stripped:
            i += 1
            continue

        # Fenced code block (```), special-casing mermaid
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            body: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1  # closing fence
            content = "\n".join(body)
            if lang == "mermaid":
                out.append(f'<pre class="mermaid">{html.escape(content, quote=False)}</pre>')
            else:
                out.append(f"<pre><code>{html.escape(content, quote=False)}</code></pre>")
            continue

        # Horizontal rule
        if stripped == "---":
            out.append("<hr>")
            i += 1
            continue

        # Heading
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # Table: a row followed by a separator row
        if stripped.startswith("|") and i + 1 < n and _is_table_sep(lines[i + 1]):
            header = _split_row(line)
            i += 2  # skip header + separator
            rows: list[list[str]] = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(_split_row(lines[i]))
                i += 1
            out.append(_render_table(header, rows))
            continue

        # Blockquote (may be a GitHub alert)
        if stripped.startswith(">"):
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            out.append(_render_blockquote(quote))
            continue

        # Lists (unordered or ordered)
        if _is_list_item(stripped):
            ordered = bool(_OL_RE.match(stripped))
            items: list[str] = []
            while i < n and _is_list_item(lines[i].strip()):
                item = _LIST_MARK_RE.sub("", lines[i].strip())
                items.append(f"<li>{_inline(item)}</li>")
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue

        # Paragraph (consume consecutive non-blank, non-structural lines).
        # A line ending in two+ spaces is a Markdown hard break -> <br>.
        para: list[str] = []
        while i < n and lines[i].strip() and not _starts_block(lines[i]):
            sep = "<br>" if lines[i].rstrip("\n").endswith("  ") else ""
            para.append(_inline(lines[i].strip()) + sep)
            i += 1
        out.append("<p>" + " ".join(para).removesuffix("<br>") + "</p>")

    return "\n".join(out)


def _starts_block(line: str) -> bool:
    s = line.strip()
    return (
        s.startswith(("#", ">", "```", "|"))
        or s == "---"
        or _is_list_item(s)
    )


def _render_table(header: list[str], rows: list[list[str]]) -> str:
    cols = len(header)
    thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
    body_rows = []
    for row in rows:
        cells = (row + [""] * cols)[:cols]
        body_rows.append("".join(f"<td>{_inline(c)}</td>" for c in cells))
    tbody = "".join(f"<tr>{r}</tr>" for r in body_rows)
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"


def _render_blockquote(quote_lines: list[str]) -> str:
    """Render a blockquote; detect GitHub `[!WARNING]`-style alerts."""
    if quote_lines:
        m = _ALERT_RE.match(quote_lines[0])
        if m:
            kind = m.group(1).lower()
            first = m.group(2).strip()
            rest = quote_lines[1:]
            inner_lines = ([first] if first else []) + rest
            inner = _md_to_html("\n".join(inner_lines))
            label = kind.capitalize()
            return (
                f'<blockquote class="alert alert-{kind}">'
                f'<p class="alert-title">{html.escape(label)}</p>{inner}</blockquote>'
            )
    inner = _md_to_html("\n".join(quote_lines))
    return f"<blockquote>{inner}</blockquote>"


# ---------------------------------------------------------------------------
# Report structure: preamble, sections, highlights
# ---------------------------------------------------------------------------

# Cycle of accent colors for stat cards and pills.
_ACCENTS = ["#58a6ff", "#bc8cff", "#3fb950", "#39c5cf", "#f0883e", "#f778ba", "#e3b341", "#ff7b72"]

_META_RE    = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_SUMMARY_TITLES = {"summary", "cierre"}


def _strip_credits(md: str) -> tuple[str, str]:
    """Split off the credits footer. Returns (body_md, credits_html)."""
    pre, sep, post = md.partition(CREDITS_ANCHOR)
    credits_html = ""
    if sep:
        line = (sep + post).strip().splitlines()[0] if (sep + post).strip() else ""
        credits_html = _inline(line) if line else ""
    body = pre.rstrip()
    if body.endswith("---"):
        body = body[: -len("---")].rstrip()
    return body, credits_html


def _parse_preamble(preamble_lines: list[str]) -> tuple[str, dict[str, str], str]:
    """Parse the report head. Returns (subject_name, meta_dict, notes_md).

    The title `# Self-Report: <name>` yields the hero name; `**Label:** value`
    lines become the meta dict; anything else (e.g. the data-source warning) is
    returned as notes Markdown to render at the top of the Overview tab.
    """
    name = ""
    meta: dict[str, str] = {}
    note_lines: list[str] = []

    for line in preamble_lines:
        s = line.strip()
        hm = _HEADING_RE.match(s)
        if hm and len(hm.group(1)) == 1:
            title = re.sub(r"[*`]", "", hm.group(2)).strip()
            name = title.split(":", 1)[1].strip() if ":" in title else title
            continue
        mm = _META_RE.match(s)
        if mm:
            meta[mm.group(1).strip()] = mm.group(2).strip()
            continue
        note_lines.append(line)

    return name, meta, "\n".join(note_lines).strip()


def _split_sections(body_md: str) -> tuple[str, list[dict]]:
    """Split the body into a preamble and a list of top-level sections.

    A section begins at a level-2 (`##`, script metrics) or level-4 (`####`, AI
    analysis) heading. Level-3 headings stay inside the metrics section they
    belong to. Returns (preamble_md, sections) where each section is
    {kind: 'metrics'|'analysis', title, md}.
    """
    lines = body_md.split("\n")
    first = None
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line.strip())
        if m and len(m.group(1)) in (2, 4):
            first = idx
            break

    if first is None:
        return body_md.strip(), []

    preamble_md = "\n".join(lines[:first]).strip()
    sections: list[dict] = []
    cur: dict | None = None
    for line in lines[first:]:
        m = _HEADING_RE.match(line.strip())
        if m and len(m.group(1)) in (2, 4):
            cur = {
                "kind": "metrics" if len(m.group(1)) == 2 else "analysis",
                "title": m.group(2).strip(),
                "lines": [line],
            }
            sections.append(cur)
        elif cur is not None:
            cur["lines"].append(line)

    for sec in sections:
        sec["md"] = _strip_trailing_hr("\n".join(sec["lines"]).strip())
    return preamble_md, sections


def _strip_trailing_hr(md: str) -> str:
    """Drop trailing `---` separators a section absorbs from the next block."""
    return re.sub(r"(?:\s*\n\s*---\s*)+$", "", md.strip()).strip()


def _extract_consolidated_table(md: str) -> tuple[str | None, str]:
    """Pull the Strengths/Areas/Red Flags table out of a section.

    Returns (table_md_or_None, remaining_md). Surrounding `---` rules and blank
    lines are removed with the table so the host section reads cleanly.
    """
    lines = md.split("\n")
    n = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = {c.strip().lower() for c in s.strip("|").split("|")}
        if not (cells & {"strengths", "fortalezas"}):
            continue
        if i + 1 >= n or not _is_table_sep(lines[i + 1]):
            continue

        end = i
        while end < n and lines[end].strip().startswith("|"):
            end += 1
        table_md = "\n".join(lines[i:end])

        start = i
        k = start - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k >= 0 and lines[k].strip() == "---":
            start = k
        k = end
        while k < n and not lines[k].strip():
            k += 1
        if k < n and lines[k].strip() == "---":
            end = k + 1

        remaining = "\n".join(lines[:start] + lines[end:]).strip()
        return table_md, remaining
    return None, md


# ---------------------------------------------------------------------------
# Hero and stat cards (from structured data)
# ---------------------------------------------------------------------------

def _num(v: int) -> str:
    return f"{v:,}"


def _stat_cards(data: dict) -> list[tuple[str, str, str, str]]:
    """Build (value, label, sublabel, accent) tuples from the structured data."""
    pr   = data.get("pull_requests", {}) or {}
    ra   = data.get("reviewer_activity", {}) or {}
    rev  = data.get("reviews", {}) or {}
    jira = data.get("jira") or {}

    opened   = pr.get("opened", 0)
    merged   = pr.get("merged", 0)
    reviewed = ra.get("human_prs_reviewed", 0)
    review_repos = {r.get("repo") for r in ra.get("reviews", []) if r.get("repo")}
    repos = data.get("repos_analyzed", []) or []

    threads  = rev.get("threads_received", 0)
    resolved = rev.get("resolved", 0)
    open_pr  = rev.get("open_pr_unresolved", 0)
    closed   = threads - open_pr
    res_pct  = round(100 * resolved / closed) if closed else 0

    lines_est = round(pr.get("avg_lines_changed", 0.0) * opened)

    cards: list[tuple[str, str, str, str]] = []
    cards.append((_num(opened), "PRs Authored", f"{merged} merged", _ACCENTS[0]))
    cards.append((_num(reviewed), "PRs Reviewed",
                  f"across {len(review_repos)} repos" if review_repos else "human PRs", _ACCENTS[1]))
    if threads:
        cards.append((_num(threads), "Review Threads", f"{res_pct}% resolved", _ACCENTS[5]))
    if jira:
        cards.append((_num(jira.get("total_created", 0)), "Tickets Created",
                      f"{jira.get('created_with_ac', 0)} with AC", _ACCENTS[2]))
        cards.append((_num(jira.get("total_tickets", 0)), "Tickets Assigned",
                      f"{jira.get('tickets_with_pr_linked', 0)} with PR linked", _ACCENTS[3]))
    if repos:
        cards.append((_num(len(repos)), "Repos Touched", "contributed", _ACCENTS[6]))
    if lines_est > 0:
        cards.append(("~" + _num(lines_est), "Lines Changed", "estimated, adds + deletions", _ACCENTS[4]))
    return cards


def _build_hero(name: str, meta: dict[str, str], data: dict | None) -> str:
    name = name or "Self-Report"
    org      = meta.get("Org / Repos", "")
    period   = meta.get("Period", "")
    reports  = meta.get("Reports to", "")
    if not reports and data:
        reports = (data.get("jira") or {}).get("reports_to", "")
    generated = meta.get("Generated", "")

    pills: list[str] = []
    if period:
        pills.append((period, _ACCENTS[0]))
    if data:
        pr = data.get("pull_requests", {}) or {}
        ra = data.get("reviewer_activity", {}) or {}
        jira = data.get("jira") or {}
        repos = data.get("repos_analyzed", []) or []
        pills.append((f"{pr.get('opened', 0)} PRs Authored", _ACCENTS[1]))
        pills.append((f"{ra.get('human_prs_reviewed', 0)} PRs Reviewed", _ACCENTS[2]))
        if repos:
            pills.append((f"{len(repos)} Repos", _ACCENTS[6]))
        if jira:
            pills.append((f"{jira.get('total_tickets', 0)} Tickets", _ACCENTS[3]))

    pills_html = "".join(
        f'<span class="pill" style="--c:{c}">{html.escape(text)}</span>' for text, c in pills
    )

    meta_rows = []
    for label, value in (("Review Period", period), ("Reporting To", reports), ("Generated", generated)):
        if value:
            meta_rows.append(
                f'<div class="meta-row"><span class="meta-label">{html.escape(label)}</span>'
                f'<span class="meta-value">{html.escape(value)}</span></div>'
            )
    meta_html = "".join(meta_rows)

    sub = f'<div class="hero-sub">{html.escape(org)}</div>' if org else ""
    return (
        '<header class="hero">'
        '<div class="hero-main">'
        '<div class="eyebrow">Self-Report</div>'
        f'<h1 class="hero-name">{html.escape(name)}</h1>'
        f'{sub}'
        f'<div class="pills">{pills_html}</div>'
        '</div>'
        f'<div class="hero-meta">{meta_html}</div>'
        '</header>'
    )


def _build_numbers(data: dict | None, period: str) -> str:
    if not data:
        return ""
    cards = _stat_cards(data)
    if not cards:
        return ""
    card_html = "".join(
        f'<div class="card" style="--c:{accent}">'
        f'<div class="card-value">{html.escape(value)}</div>'
        f'<div class="card-label">{html.escape(label)}</div>'
        f'<div class="card-sub">{html.escape(sub)}</div>'
        '</div>'
        for value, label, sub, accent in cards
    )
    period_html = f'<span class="numbers-period">{html.escape(period)}</span>' if period else ""
    return (
        '<div class="numbers">'
        f'<div class="numbers-head"><span class="numbers-chip">By the Numbers</span>{period_html}</div>'
        f'<div class="cards">{card_html}</div>'
        '</div>'
    )


# ---------------------------------------------------------------------------
# Tabs assembly
# ---------------------------------------------------------------------------

def _build_body(body_md: str, data: dict | None) -> str:
    """Assemble the hero, stat grid and tabbed body from the report Markdown."""
    preamble_md, sections = _split_sections(body_md)
    name, meta, notes_md = _parse_preamble(preamble_md.split("\n"))
    period = meta.get("Period", "")

    # Pull the consolidated highlights table into the Overview tab.
    highlights_md: str | None = None
    for sec in sections:
        if sec["kind"] == "analysis":
            table, remaining = _extract_consolidated_table(sec["md"])
            if table:
                highlights_md = table
                sec["md"] = _strip_trailing_hr(remaining)
                break

    summary_md: str | None = None
    analysis_secs: list[dict] = []
    metrics_secs: list[dict] = []
    for sec in sections:
        if sec["kind"] == "metrics":
            metrics_secs.append(sec)
        elif sec["title"].strip().lower() in _SUMMARY_TITLES:
            summary_md = sec["md"]
        else:
            analysis_secs.append(sec)

    # --- Overview tab ---
    overview_parts: list[str] = []
    if notes_md:
        overview_parts.append(f'<div class="notes">{_md_to_html(notes_md)}</div>')
    overview_parts.append(_build_numbers(data, period))
    if summary_md:
        overview_parts.append(f'<div class="summary-box">{_md_to_html(summary_md)}</div>')
    if highlights_md:
        overview_parts.append(f'<div class="highlights">{_md_to_html(highlights_md)}</div>')
    overview_html = "\n".join(p for p in overview_parts if p)

    analysis_html = "\n".join(
        f'<section class="report-section">{_md_to_html(s["md"])}</section>'
        for s in analysis_secs if s["md"].strip()
    )
    metrics_html = "\n".join(
        f'<section class="report-section">{_md_to_html(s["md"])}</section>'
        for s in metrics_secs if s["md"].strip()
    )

    tabs = [("overview", "Overview", overview_html)]
    if analysis_html:
        tabs.append(("analysis", "AI Analysis", analysis_html))
    if metrics_html:
        tabs.append(("metrics", "Metrics", metrics_html))

    hero = _build_hero(name, meta, data)
    buttons = "".join(
        f'<button class="tab-btn{" active" if i == 0 else ""}" '
        f'onclick="showTab(\'tab-{tab_id}\', this)">{html.escape(label)}</button>'
        for i, (tab_id, label, _) in enumerate(tabs)
    )
    panels = "".join(
        f'<section class="tab-panel{" active" if i == 0 else ""}" id="tab-{tab_id}">{content}</section>'
        for i, (tab_id, _, content) in enumerate(tabs)
    )
    return f'{hero}<nav class="tabs">{buttons}</nav>{panels}'


# ---------------------------------------------------------------------------
# Page template
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #090c12; --bg2: #0d1117; --surface: #12161f; --surface2: #161b22;
  --border: #232a35; --border-soft: #1c222c;
  --text: #e6edf3; --muted: #8b949e; --faint: #6e7681; --accent: #58a6ff;
  --warn-bg: #2d2212; --warn-border: #9e6a03;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background:
    radial-gradient(1100px 520px at 78% -8%, rgba(88,166,255,.10), transparent 60%),
    radial-gradient(900px 480px at 8% -4%, rgba(188,140,255,.08), transparent 55%),
    var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.6; font-size: 16px;
}
.page { max-width: 1040px; margin: 0 auto; padding: 40px 28px 96px; }

/* Hero */
.hero {
  display: flex; justify-content: space-between; align-items: flex-start; gap: 32px;
  padding: 4px 4px 28px; border-bottom: 1px solid var(--border-soft);
}
.hero-main { flex: 1 1 auto; min-width: 0; }
.eyebrow {
  font-size: .72rem; letter-spacing: .18em; text-transform: uppercase;
  color: var(--accent); font-weight: 700; margin-bottom: 10px;
}
.hero-name {
  font-size: 2.9rem; line-height: 1.05; font-weight: 800; margin: 0;
  background: linear-gradient(180deg, #fff, #aeb9c6);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
.hero-sub { color: var(--muted); margin-top: 8px; font-size: 1.02rem; }
.pills { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
.pill {
  font-size: .8rem; font-weight: 600; padding: 5px 12px; border-radius: 999px;
  color: var(--c); border: 1px solid color-mix(in srgb, var(--c) 45%, transparent);
  background: color-mix(in srgb, var(--c) 12%, transparent);
}
.hero-meta { text-align: right; flex: 0 0 auto; min-width: 200px; }
.meta-row { margin: 0 0 12px; }
.meta-label { display: block; font-size: .68rem; letter-spacing: .08em; text-transform: uppercase; color: var(--faint); }
.meta-value { display: block; font-weight: 600; color: var(--text); }

/* Tabs */
.tabs { display: flex; gap: 4px; margin: 26px 0 22px; border-bottom: 1px solid var(--border); }
.tab-btn {
  appearance: none; background: none; border: 0; cursor: pointer;
  color: var(--muted); font-size: .95rem; font-weight: 600; font-family: inherit;
  padding: 12px 18px; border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.tab-btn:hover { color: var(--text); }
.tab-btn.active { color: var(--text); border-bottom-color: var(--accent); }
.tab-panel { display: none; animation: fade .18s ease; }
.tab-panel.active { display: block; }
@keyframes fade { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

/* By the Numbers */
.numbers { margin: 8px 0 28px; }
.numbers-head { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; }
.numbers-chip {
  font-size: 1.05rem; font-weight: 700;
  padding: 8px 14px; border-radius: 10px;
  background: var(--surface2); border: 1px solid var(--border);
}
.numbers-period { color: var(--faint); font-size: .85rem; margin-left: auto; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(168px, 1fr)); gap: 14px; }
.card {
  background: linear-gradient(180deg, var(--surface), var(--bg2));
  border: 1px solid var(--border); border-radius: 14px; padding: 18px 18px 16px;
  position: relative; overflow: hidden;
}
.card::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px; background: var(--c);
}
.card-value { font-size: 2.1rem; font-weight: 800; line-height: 1; color: var(--c); }
.card-label { margin-top: 10px; font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; color: var(--muted); font-weight: 700; }
.card-sub { margin-top: 4px; font-size: .78rem; color: var(--faint); }

/* Overview extras */
.notes { margin-bottom: 22px; }
.summary-box {
  background: var(--surface); border: 1px solid var(--border);
  border-left: 3px solid var(--accent); border-radius: 12px; padding: 4px 22px 18px; margin-bottom: 22px;
}
.highlights { margin-top: 8px; }
.highlights table { font-size: 14px; }

/* Sections */
.report-section + .report-section { margin-top: 8px; }
h1, h2, h3, h4 { font-weight: 700; }
h2 { font-size: 1.5rem; margin: 1.8em 0 .6em; padding-bottom: .3em; border-bottom: 1px solid var(--border); }
h3 { font-size: 1.15rem; margin: 1.5em 0 .5em; color: #c9d4e0; }
h4 { font-size: 1.2rem; margin: 1.4em 0 .5em; color: var(--text); }
.summary-box h4 { color: var(--accent); }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
hr { border: 0; border-top: 1px solid var(--border); margin: 1.6em 0; }
code {
  background: rgba(110,118,129,.4); padding: .2em .4em; border-radius: 6px;
  font-size: 85%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
pre { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; overflow: auto; }
pre code { background: none; padding: 0; font-size: 90%; }
pre.mermaid { background: var(--surface); border: 1px solid var(--border); text-align: center; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 14px; }
th, td { border: 1px solid var(--border); padding: 9px 12px; text-align: left; vertical-align: top; }
th { background: var(--surface2); font-weight: 600; }
tr:nth-child(even) td { background: rgba(110,118,129,.06); }
blockquote {
  margin: 1em 0; padding: .5em 1em; border-left: 3px solid var(--border);
  color: var(--muted); background: rgba(110,118,129,.06); border-radius: 0 8px 8px 0;
}
blockquote.alert { border-radius: 8px; }
blockquote.alert-warning { border-left-color: var(--warn-border); background: var(--warn-bg); color: var(--text); }
.alert-title { font-weight: 700; margin: 0 0 .3em; text-transform: uppercase; font-size: .85em; letter-spacing: .04em; }
ul, ol { padding-left: 1.4em; }
li { margin: .25em 0; }
.credits { margin-top: 48px; padding-top: 18px; border-top: 1px solid var(--border-soft); color: var(--faint); font-size: .85rem; text-align: center; }

@media (max-width: 640px) {
  .hero { flex-direction: column; gap: 18px; }
  .hero-meta { text-align: left; }
  .hero-name { font-size: 2.2rem; }
}
"""

_SCRIPTS = """
<script>
function showTab(id, btn) {
  document.querySelectorAll('.tab-panel').forEach(function (p) { p.classList.remove('active'); });
  document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.remove('active'); });
  document.getElementById(id).classList.add('active');
  btn.classList.add('active');
}
</script>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({ startOnLoad: true, theme: "dark" });
</script>
"""

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="page">
{body}
{credits}
</div>
{scripts}
</body>
</html>
"""


def _extract_title(md: str) -> str:
    for line in md.split("\n"):
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return re.sub(r"[*`]", "", m.group(1)).strip()
    return "Self-Report"


def render_html(md: str, data: dict | None = None) -> str:
    """Convert a Markdown report to a full standalone HTML document.

    `data` is the structured `data-<stem>.json` dict used to build the hero and
    the stat grid. When omitted, the page still renders from the Markdown alone.
    """
    title = _extract_title(md)
    body_md, credits_html = _strip_credits(md)
    body = _build_body(body_md, data)
    credits = f'<footer class="credits">{credits_html}</footer>' if credits_html else ""
    return _TEMPLATE.format(
        title=html.escape(title), css=_CSS, body=body, credits=credits, scripts=_SCRIPTS
    )


def render_html_report(md_path: Path, html_path: Path, data_path: Path | None = None) -> Path:
    """Read a Markdown report file and write its HTML rendering.

    When `data_path` points at a readable `data-<stem>.json`, its contents drive
    the hero banner and the stat grid.
    """
    md = Path(md_path).read_text(encoding="utf-8")
    data: dict | None = None
    if data_path and Path(data_path).exists():
        try:
            data = json.loads(Path(data_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log(f"  HTML: could not read structured data ({data_path}): {e}")
    html_path = Path(html_path)
    html_path.write_text(render_html(md, data), encoding="utf-8")
    log(f"HTML report: {html_path}")
    return html_path
