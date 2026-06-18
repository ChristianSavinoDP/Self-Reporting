"""Render a Self-Report Markdown file to a styled, standalone HTML page.

Self-contained — no external Markdown dependency. Supports the subset of
Markdown the reports actually use: headings, paragraphs, tables, blockquotes
(including GitHub `[!WARNING]` alerts), unordered/ordered lists, horizontal
rules, fenced code blocks (with Mermaid diagrams rendered via CDN), and inline
`**bold**`, `*italic*`, `` `code` `` and links.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

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
# Page template
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #0d1117; --surface: #161b22; --border: #30363d;
  --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
  --warn-bg: #2d2212; --warn-border: #9e6a03;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  line-height: 1.6; font-size: 16px;
}
.container { max-width: 920px; margin: 0 auto; padding: 48px 24px 96px; }
h1 { font-size: 2em; border-bottom: 1px solid var(--border); padding-bottom: .3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid var(--border); padding-bottom: .3em; margin-top: 2em; }
h3 { font-size: 1.2em; margin-top: 1.6em; }
h4 { font-size: 1.05em; color: var(--accent); margin-top: 1.6em; text-transform: none; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
hr { border: 0; border-top: 1px solid var(--border); margin: 2em 0; }
code {
  background: rgba(110,118,129,.4); padding: .2em .4em; border-radius: 6px;
  font-size: 85%; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
pre { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px; overflow: auto; }
pre code { background: none; padding: 0; font-size: 90%; }
pre.mermaid { background: var(--surface); border: 1px solid var(--border); text-align: center; }
table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 14px; }
th, td { border: 1px solid var(--border); padding: 8px 12px; text-align: left; vertical-align: top; }
th { background: var(--surface); font-weight: 600; }
tr:nth-child(even) td { background: rgba(110,118,129,.08); }
blockquote {
  margin: 1em 0; padding: .4em 1em; border-left: 4px solid var(--border);
  color: var(--muted); background: rgba(110,118,129,.06);
}
blockquote.alert { border-radius: 6px; }
blockquote.alert-warning { border-left-color: var(--warn-border); background: var(--warn-bg); color: var(--text); }
.alert-title { font-weight: 700; margin: 0 0 .3em; text-transform: uppercase; font-size: .85em; letter-spacing: .04em; }
ul, ol { padding-left: 1.5em; }
li { margin: .25em 0; }
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
<div class="container">
{body}
</div>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true, theme: "dark" }});
</script>
</body>
</html>
"""


def _extract_title(md: str) -> str:
    for line in md.split("\n"):
        m = re.match(r"^#\s+(.*)$", line.strip())
        if m:
            return re.sub(r"[*`]", "", m.group(1)).strip()
    return "Self-Report"


def render_html(md: str) -> str:
    """Convert a Markdown string to a full standalone HTML document."""
    title = _extract_title(md)
    body = _md_to_html(md)
    return _TEMPLATE.format(title=html.escape(title), css=_CSS, body=body)


def render_html_report(md_path: Path, html_path: Path) -> Path:
    """Read a Markdown report file and write its HTML rendering."""
    md = Path(md_path).read_text(encoding="utf-8")
    html_path = Path(html_path)
    html_path.write_text(render_html(md), encoding="utf-8")
    log(f"HTML report: {html_path}")
    return html_path
