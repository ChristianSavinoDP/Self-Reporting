"""Analyze a self-report using Claude with extended thinking.

Reads output/data/data-<stem>.json and output/data/metrics-<stem>.md,
sends the user's data to Claude in a single API call, streams the analysis
to stdout, and writes the final report to output/<stem>.md.

If analysis fails, re-run with: python main.py analyze --period <period>
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils import PERIODS, file_stem, setup_log, log  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import anthropic

_USE_BEDROCK = os.environ.get("CLAUDE_CODE_USE_BEDROCK", "").lower() == "true"


def _claude_code_model() -> str | None:
    """Read the model the user has selected in Claude Code.

    Claude Code does not export the active model to subprocesses, but it
    persists the selection in its settings.json files. Project settings take
    precedence over user settings (matching Claude Code's own resolution).
    """
    candidates = [
        ROOT / ".claude" / "settings.local.json",
        ROOT / ".claude" / "settings.json",
        Path.home() / ".claude" / "settings.json",
    ]
    for path in candidates:
        try:
            model = json.loads(path.read_text()).get("model")
        except (OSError, ValueError):
            continue
        if model:
            return model
    return None


# Model resolution order:
#   1. REPORT_MODEL / ANTHROPIC_MODEL env override
#   2. (Bedrock only) the model selected in Claude Code's settings.json
#   3. hard-coded fallback
_FALLBACK_MODEL = "us.anthropic.claude-opus-4-8" if _USE_BEDROCK else "claude-opus-4-7"
MODEL = (
    os.environ.get("REPORT_MODEL")
    or os.environ.get("ANTHROPIC_MODEL")
    or (_claude_code_model() if _USE_BEDROCK else None)
    or _FALLBACK_MODEL
)


# ---------------------------------------------------------------------------
# Report manipulation helpers
# ---------------------------------------------------------------------------

def _fix_markdown(text: str) -> str:
    """Fix common markdown formatting issues in Claude's output."""
    lines = text.split("\n")
    result: list[str] = []

    def _is_list_item(s: str) -> bool:
        return bool(re.match(r'^[-*+]\s|^\d+\.\s', s.strip()))

    for i, line in enumerate(lines):
        stripped = line.strip()
        is_heading = stripped.startswith("#")
        is_hr = stripped == "---"
        is_table_row = stripped.startswith("|") and stripped.endswith("|")
        is_blockquote = stripped.startswith(">")
        is_list = _is_list_item(stripped)

        prev_stripped = result[-1].strip() if result else ""
        prev_is_table = prev_stripped.startswith("|") and prev_stripped.endswith("|")
        prev_is_list = _is_list_item(prev_stripped)

        # Blank line before headings, hrs, blockquotes, first table row, first list item
        needs_blank_before = (
            (is_heading or is_hr or is_blockquote) and prev_stripped
            or (is_table_row and not prev_is_table and prev_stripped)
            or (is_list and not prev_is_list and prev_stripped)
        )
        if needs_blank_before and result:
            result.append("")

        result.append(line)

        # Blank line after headings and hrs
        if (is_heading or is_hr) and i + 1 < len(lines) and lines[i + 1].strip():
            result.append("")
        # Blank line after blockquote if next is not another blockquote
        if is_blockquote and i + 1 < len(lines):
            next_s = lines[i + 1].strip()
            if next_s and not next_s.startswith(">"):
                result.append("")
        # Blank line after last list item before non-list content
        if is_list and i + 1 < len(lines):
            next_s = lines[i + 1].strip()
            if next_s and not _is_list_item(next_s):
                result.append("")

    # Fix table separators: |---| -> | --- |
    fixed: list[str] = []
    for line in result:
        if re.match(r'^\|[-:|]+(\|[-:|]+)+\|$', line.strip()):
            cells = line.strip().split("|")[1:-1]
            spaced = "| " + " | ".join(c.strip() for c in cells) + " |"
            fixed.append(spaced)
        else:
            fixed.append(line)

    # Collapse 2+ consecutive blank lines into 1
    cleaned: list[str] = []
    blank_count = 0
    for line in fixed:
        if not line.strip():
            blank_count += 1
            if blank_count <= 1:
                cleaned.append("")
        else:
            blank_count = 0
            cleaned.append(line)

    # Trailing whitespace removal
    return "\n".join(l.rstrip() for l in cleaned)


def _insert_analysis(report: str, analysis: str) -> str:
    """Append analysis block at the end of the report."""
    return report.rstrip() + "\n\n" + _fix_markdown(analysis).strip() + "\n"


def _extract_metrics_section(report: str) -> str:
    """Return the full report content (metrics tables) for context."""
    return report.strip()


# ---------------------------------------------------------------------------
# Streaming helper
# ---------------------------------------------------------------------------

_MAX_USER_JSON_CHARS = 200_000


def _split_user_data(user_data: dict) -> list[tuple[str, dict]]:
    """Split user data into parts that fit in context. Returns [(label, data_dict), ...]."""
    github_data = {k: v for k, v in user_data.items() if k != "jira"}
    jira_data = user_data.get("jira")

    github_str = json.dumps(github_data, indent=2, ensure_ascii=False)
    if len(github_str) <= _MAX_USER_JSON_CHARS and not jira_data:
        return [("complete", user_data)]

    if not jira_data:
        core = {k: v for k, v in github_data.items() if k != "reviewer_activity"}
        reviewer = {"reviewer_activity": github_data.get("reviewer_activity", {})}
        return [("github-core", core), ("reviewer-activity", reviewer)]

    github_str_size = len(github_str)
    jira_str = json.dumps(jira_data, indent=2, ensure_ascii=False)

    if github_str_size + len(jira_str) <= _MAX_USER_JSON_CHARS:
        return [("complete", user_data)]

    parts = []
    if github_str_size > _MAX_USER_JSON_CHARS:
        core = {k: v for k, v in github_data.items() if k != "reviewer_activity"}
        reviewer = {"reviewer_activity": github_data.get("reviewer_activity", {})}
        parts.append(("github-core", core))
        parts.append(("reviewer-activity", reviewer))
    else:
        parts.append(("github", github_data))
    parts.append(("jira", {"jira": jira_data}))
    return parts


def _stream(client, system, messages, max_tokens: int, budget_tokens: int, label: str) -> str:
    """Stream a Claude response to console; return the text."""
    chunks: list[str] = []
    thinking_shown = False
    text_shown = False

    stream_kwargs = dict(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    if _USE_BEDROCK:
        stream_kwargs["thinking"] = {"type": "adaptive"}
    else:
        stream_kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}

    with client.messages.stream(**stream_kwargs) as stream:
        for event in stream:
            if event.type == "content_block_start":
                if event.content_block.type == "thinking" and not thinking_shown:
                    print(f"  {label}: thinking...", flush=True)
                    thinking_shown = True
                elif event.content_block.type == "text" and not text_shown:
                    if thinking_shown:
                        print()
                    print(f"  {label}: generating...\n", flush=True)
                    text_shown = True
            elif event.type == "content_block_delta":
                if event.delta.type == "text_delta":
                    print(event.delta.text, end="", flush=True)
                    chunks.append(event.delta.text)
        if text_shown:
            print()
        msg = stream.get_final_message()

    u = msg.usage
    cached = getattr(u, "cache_read_input_tokens", 0) or 0
    stop = msg.stop_reason
    log(f"  {label}: {u.output_tokens} tokens output ({u.input_tokens} input, {cached} cache) [{stop}]")
    if stop != "end_turn":
        log(f"  {label}: response not completed (stop_reason={stop})")
        return ""
    return "".join(chunks).strip()


# ---------------------------------------------------------------------------
# Analysis instructions
# ---------------------------------------------------------------------------

_FULL_ANALYSIS_INSTRUCTIONS = (
    "Analyze the data for @{login} following prompt.md strictly. "
    "Return ONLY the analysis block:\n"
    "- Start with `---`\n"
    "- `#### PR Descriptions`\n"
    "- `---`\n"
    "- `#### Response to Reviews Received` (with blockquote metrics)\n"
    "- `---`\n"
    "- `#### Reviewer Activity` (with blockquote metrics)\n"
    "- `---`\n"
    "- `#### Code Quality` (human reviews only; bots only if critical issue unfixed)\n"
    "- `---`\n"
    "- `#### Jira Activity` (with blockquote; only if Jira data present)\n"
    "- `---`\n"
    "- Consolidated table `| Strengths | Areas for Improvement | Red Flags |` (GitHub + Jira together)\n"
    "- `---`\n"
    "- `#### Summary` (2-3 sentences of synthesis)\n\n"
    "Do not include `## @{login}` or the script-generated sections. "
    "If no Jira data, omit `#### Jira Activity` but the consolidated table "
    "reflects GitHub only."
)


def _sections_for_label(label: str) -> str:
    """Return which sections to analyze based on the data part."""
    if label == "github-core":
        return (
            "- `#### PR Descriptions`\n"
            "- `#### Response to Reviews Received`\n"
            "- `#### Code Quality`"
        )
    elif label == "reviewer-activity":
        return "- `#### Reviewer Activity`"
    elif label == "github":
        return (
            "- `#### PR Descriptions`\n"
            "- `#### Response to Reviews Received`\n"
            "- `#### Reviewer Activity`\n"
            "- `#### Code Quality`"
        )
    elif label == "jira":
        return "- `#### Jira Activity`"
    return ""


# ---------------------------------------------------------------------------
# Analysis strategies
# ---------------------------------------------------------------------------

def _analyze_single(
    client, system, login: str, fp: str, user_json_str: str, metrics_section: str,
    max_tokens: int, budget_tokens: int,
) -> str:
    """Single-request analysis when data fits in context."""
    msg = {"role": "user", "content": (
        f"Data for @{login} for the period `{fp}`:\n\n"
        f"```json\n{user_json_str}\n```\n\n"
        f"Script-generated report section (reference tables):\n\n"
        f"```markdown\n{metrics_section}\n```\n\n"
        + _FULL_ANALYSIS_INSTRUCTIONS.format(login=login)
    )}

    analysis = _stream(client, system, messages=[msg],
                       max_tokens=max_tokens, budget_tokens=budget_tokens,
                       label=f"@{login}")
    if len(analysis) < 100:
        log(f"  @{login}: failed response ({len(analysis)} chars) — retrying...")
        analysis = _stream(client, system, messages=[msg],
                           max_tokens=max_tokens + 10000, budget_tokens=budget_tokens + 6000,
                           label=f"@{login} (retry)")
    return analysis


def _analyze_multipart(
    client, system, login: str, fp: str,
    parts: list[tuple[str, dict]], metrics_section: str,
    max_tokens: int, budget_tokens: int,
) -> str:
    """Multi-request analysis: analyze each part, then consolidate."""
    partial_analyses: list[str] = []

    for i, (part_label, part_data) in enumerate(parts):
        part_json = json.dumps(part_data, indent=2, ensure_ascii=False)
        log(f"  Part {i+1}/{len(parts)} ({part_label}): {len(part_json)} chars")

        sections_for_part = _sections_for_label(part_label)
        msg = {"role": "user", "content": (
            f"PARTIAL data for @{login} for the period `{fp}` — part: {part_label}.\n\n"
            f"```json\n{part_json}\n```\n\n"
            f"Script-generated report section (reference tables):\n\n"
            f"```markdown\n{metrics_section}\n```\n\n"
            f"Analyze ONLY the following sections based on the provided data. "
            f"Follow prompt.md strictly:\n{sections_for_part}\n\n"
            f"Return only those sections, without the consolidated table or summary."
        )}

        part_analysis = _stream(client, system, messages=[msg],
                                max_tokens=max_tokens, budget_tokens=budget_tokens,
                                label=f"@{login} [{part_label}]")
        if len(part_analysis) < 50:
            log(f"  @{login} [{part_label}]: failed response — retrying...")
            part_analysis = _stream(client, system, messages=[msg],
                                    max_tokens=max_tokens + 10000,
                                    budget_tokens=budget_tokens + 6000,
                                    label=f"@{login} [{part_label}] (retry)")
        if len(part_analysis) < 50:
            log(f"  @{login} [{part_label}]: Error — part failed")
            return ""
        partial_analyses.append(part_analysis)

    log(f"  Consolidating {len(partial_analyses)} parts...")
    merged = "\n\n".join(partial_analyses)
    consolidation_msg = {"role": "user", "content": (
        f"Below are the partial analyses for @{login} for the period `{fp}`, "
        f"generated in separate parts. Consolidate them into a single analysis block "
        f"following the format from prompt.md:\n\n"
        f"{merged}\n\n"
        f"Return the complete consolidated block:\n"
        + _FULL_ANALYSIS_INSTRUCTIONS.format(login=login)
    )}

    analysis = _stream(client, system, messages=[consolidation_msg],
                       max_tokens=max_tokens, budget_tokens=budget_tokens,
                       label=f"@{login} [consolidation]")
    if len(analysis) < 100:
        log(f"  @{login} [consolidation]: failed response — retrying...")
        analysis = _stream(client, system, messages=[consolidation_msg],
                           max_tokens=max_tokens + 10000, budget_tokens=budget_tokens + 6000,
                           label=f"@{login} [consolidation] (retry)")
    return analysis


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(period: str, output: str = "output", language: str = "en") -> None:
    stem = file_stem(period)

    setup_log(ROOT / "logs" / f"analyze-{period}.log")

    data_dir    = ROOT / output / "data"
    data_path   = data_dir / f"data-{stem}.json"
    report_src  = data_dir / f"metrics-{stem}.md"

    output_dir  = ROOT / output
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{stem}.md"

    for p in (data_path, report_src):
        if not p.exists():
            log(f"Error: {p} not found — run 'python main.py report' first.")
            sys.exit(1)

    log(f"Sources : {data_dir}/")
    log(f"Writing : {report_path}")
    log(f"Model   : {MODEL} (extended thinking)")
    log("=" * 60)

    prompt_md  = (ROOT / "prompt.md").read_text(encoding="utf-8")
    data_json  = json.loads(data_path.read_text(encoding="utf-8"))
    report_md  = report_src.read_text(encoding="utf-8")

    # Get login from data JSON (most reliable) or fallback to token
    login = data_json.get("login", "")

    if not login:
        import requests as _requests
        token = os.environ.get("GITHUB_TOKEN", "")
        if token:
            resp = _requests.get(
                "https://api.github.com/user",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                timeout=15,
            )
            resp.raise_for_status()
            login = resp.json().get("login", "unknown")
        else:
            login = "unknown"

    user_max_tokens = 32000
    user_budget_tokens = 24000

    # Determine language instruction
    lang_instruction = ""
    if language == "es":
        lang_instruction = (
            "\n\nIMPORTANT: Write the entire analysis in Latin American Spanish. "
            "Use spanglish for common technical terms (PR, merge, thread, reviewer, "
            "pipeline, etc.) but all prose must be in Spanish with correct accents "
            "and special characters. Never omit accents or replace special characters.\n"
            "Use the Spanish section headers defined in prompt.md: "
            "'#### Descripciones de PRs', '#### Respuesta a Reviews Recibidas', "
            "'#### Actividad como Reviewer', '#### Calidad de Codigo', "
            "'#### Actividad en Jira', '#### Cierre'. "
            "Use '| Fortalezas | Areas de Mejora | Red Flags |' for the consolidated table."
        )

    if _USE_BEDROCK:
        for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
            os.environ.pop(k, None)
        client = anthropic.AnthropicBedrock(timeout=600.0)
    else:
        client = anthropic.Anthropic(timeout=600.0)
    system = [{
        "type": "text",
        "text": (
            "You are a software engineering analyst. Your sole reference framework "
            "for analyzing data is the prompt.md document below. Follow its instructions "
            "exactly: format, rules, alert signals, output structure, and prohibitions. "
            "Do not invent rules that are not there. If prompt.md does not say something "
            "is a problem, do not flag it as such."
            + lang_instruction + "\n\n"
            + prompt_md
        ),
        "cache_control": {"type": "ephemeral"},
    }]

    log(f"\n{'—' * 40}")
    log(f"@{login}: starting analysis...")

    user_json_str = json.dumps(data_json, indent=2, ensure_ascii=False)
    metrics_section = _extract_metrics_section(report_md)
    parts = _split_user_data(data_json)

    if len(parts) == 1:
        log(f"  Input: {len(user_json_str)} chars JSON + {len(metrics_section)} chars MD")
        analysis = _analyze_single(
            client, system, login, period, user_json_str, metrics_section,
            user_max_tokens, user_budget_tokens,
        )
    else:
        log(f"  Input total: {len(user_json_str)} chars JSON — splitting into {len(parts)} parts")
        analysis = _analyze_multipart(
            client, system, login, period, parts, metrics_section,
            user_max_tokens, user_budget_tokens,
        )

    if len(analysis) < 100:
        log(f"  @{login}: Error — analysis failed ({len(analysis)} chars)")
        log("  Re-run with: python main.py analyze --period " + period)
        sys.exit(1)

    report_md = _insert_analysis(report_md, analysis)
    report_path.write_text(report_md + "\n", encoding="utf-8")
    log(f"  @{login}: saved ({len(analysis)} chars)")
    log(f"\nDone: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze self-report via Claude API")
    parser.add_argument("--period", default="biweekly", choices=PERIODS)
    parser.add_argument("--output", default="output")
    parser.add_argument("--language", default="en", help="Report language (en, es, etc.)")
    args = parser.parse_args()
    run(args.period, args.output, args.language)


if __name__ == "__main__":
    main()
