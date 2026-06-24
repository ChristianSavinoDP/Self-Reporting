#!/usr/bin/env python3
"""Self-Reporting: unified entry point.

Commands:
  report  : run full pipeline: GitHub + Jira + Claude analysis (single pass)
  collect : collect GitHub + Jira data only (no analysis)
  analyze : run Claude analysis on existing data (retry-friendly)

Usage:
    python main.py report                         # last 2 weeks
    python main.py report --period monthly
    python main.py report --period last-month
    python main.py report --language es           # report in Spanish
    python main.py collect --period biweekly      # data only, no analysis
    python main.py analyze --period biweekly      # re-run analysis on existing data
"""
import argparse
import json
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from src.utils import resolve_period, file_stem, to_json, PERIODS, setup_log, log
from src.metrics import (
    UserData, PRStats, ReviewStats, ReviewerActivity,
    PRSample, ThreadDetail, ReviewGiven, SelfThread,
    InsufficientRateLimitError,
)


def _add_period_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--period",   default="biweekly", choices=PERIODS,
                        help="biweekly (default) | monthly | last-month | yearly | historic | custom")
    parser.add_argument("--start-date", default=None,
                        help="Start date YYYY-MM-DD (required for --period custom)")
    parser.add_argument("--end-date", default=None,
                        help="End date YYYY-MM-DD (required for --period custom)")
    parser.add_argument("--output",   default="output")
    parser.add_argument("--language", default=None,
                        help="Report language override (en, es)")


def _log_path(cmd: str, period: str) -> Path:
    return Path("logs") / f"{cmd}-{period}.log"


def _resolve_github_login() -> str:
    """Detect the GitHub username from the token via API."""
    import requests
    token = os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        # Try gh CLI
        import subprocess
        try:
            r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=5)
            token = r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            pass
    if not token:
        log("Error: GITHUB_TOKEN not found. Set it in .env or via 'gh auth login'.")
        sys.exit(1)
    resp = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=15,
    )
    resp.raise_for_status()
    login = resp.json().get("login", "")
    if not login:
        log("Error: could not resolve GitHub username from token.")
        sys.exit(1)
    return login


def _load_config(args) -> tuple[dict, str, str]:
    # Build config entirely from environment variables
    config = {
        "user": _resolve_github_login(),
        "organization": os.environ.get("GITHUB_ORG", ""),
        "jira": {
            "email": os.environ.get("JIRA_EMAIL", ""),
            "project_key": os.environ.get("JIRA_PROJECT_KEY", ""),
        },
    }

    start, end = resolve_period(
        args.period,
        getattr(args, "start_date", None),
        getattr(args, "end_date", None),
    )
    config["date_range"] = {"start": start, "end": end}
    return config, start, end


def _period_label(period: str, start: str | None, end: str | None) -> str:
    if period == "historic":
        return "all time"
    span = f"{start} to {end}"
    return {
        "biweekly":   f"last 2 weeks ({span})",
        "monthly":    f"current month ({span})",
        "last-month": f"last month ({span})",
        "yearly":     f"year to date ({span})",
        "custom":     f"custom ({span})",
    }.get(period, span)


def _get_language(args) -> str:
    """Resolve language from CLI flag, default 'en'."""
    return args.language or "en"


def _user_from_dict(raw: dict) -> UserData:
    """Reconstruct UserData from a JSON dict."""
    rev = raw.get("reviews", {})
    ra  = raw.get("reviewer_activity", {})
    return UserData(
        login=raw.get("login", ""),
        name=raw.get("name", ""),
        avatar_url=raw.get("avatar_url", ""),
        repos_analyzed=raw.get("repos_analyzed", []),
        pull_requests=PRStats(**raw.get("pull_requests", {})),
        reviews=ReviewStats(
            threads_received=rev.get("threads_received", 0),
            resolved=rev.get("resolved", 0),
            ignored=rev.get("ignored", 0),
            reacted_only=rev.get("reacted_only", 0),
            replied_not_resolved=rev.get("replied_not_resolved", 0),
            outdated=rev.get("outdated", 0),
            open_pr_unresolved=rev.get("open_pr_unresolved", 0),
            threads=[ThreadDetail(**t) for t in rev.get("threads", [])],
        ),
        reviewer_activity=ReviewerActivity(
            human_prs_reviewed=ra.get("human_prs_reviewed", 0),
            human_approvals=ra.get("human_approvals", 0),
            human_changes_requested=ra.get("human_changes_requested", 0),
            human_comments=ra.get("human_comments", 0),
            bot_prs_reviewed=ra.get("bot_prs_reviewed", 0),
            bot_approvals=ra.get("bot_approvals", 0),
            bot_changes_requested=ra.get("bot_changes_requested", 0),
            bot_comments=ra.get("bot_comments", 0),
            reviews=[ReviewGiven(**r) for r in ra.get("reviews", [])],
        ),
        pr_samples=[PRSample(**s) for s in raw.get("pr_samples", [])],
        self_threads=[SelfThread(**st) for st in raw.get("self_threads", [])],
    )


def cmd_collect(args) -> None:
    """Collect GitHub + Jira data and render metrics report (no AI analysis)."""
    from src.metrics import collect_metrics
    from src.jira_metrics import collect_jira_metrics
    from src.renderer import render_report

    setup_log(_log_path("collect", args.period))
    language   = _get_language(args)
    config, start, end = _load_config(args)
    stem       = file_stem(args.period)
    label      = _period_label(args.period, start, end)
    output_dir = Path(args.output) / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    log("Self-Report: Data Collection")
    log("=" * 40)
    log(f"Period: {label}")
    log(f"Output: {output_dir}/")

    # status_notes surfaces data-source problems at the TOP of the report
    # instead of failing silently in the logs.
    status_notes: list[str] = []

    try:
        user = collect_metrics(config)
    except ValueError as e:
        log(f"\nConfiguration error: {e}")
        sys.exit(1)
    except InsufficientRateLimitError as e:
        log(f"\nRate limit check: {e}")
        sys.exit(1)
    except Exception as e:
        log(f"\nGitHub collection failed: {e}")
        log("Cannot continue without GitHub data.")
        sys.exit(1)

    # Collect Jira metrics
    log("\n" + "=" * 40)
    log("Jira Enrichment")
    log("=" * 40)
    # project_key intentionally excluded: Jira is queried by person, not project.
    jira_configured = all([
        os.environ.get("JIRA_URL", ""),
        os.environ.get("JIRA_EMAIL", ""),
        os.environ.get("JIRA_TOKEN", ""),
    ])
    try:
        jira_data = collect_jira_metrics(config, user)
    except Exception as e:
        log(f"Warning: Jira collection failed: {e}")
        log("Continuing without Jira data...")
        jira_data = None
        status_notes.append(f"**Jira data unavailable**: collection failed: {e}")
    else:
        if jira_data is None:
            if jira_configured:
                status_notes.append(
                    "**Jira data unavailable**: Jira is configured but returned no data "
                    "(check JIRA_PROJECT_KEY, JIRA_EMAIL, and account permissions)."
                )
            else:
                status_notes.append(
                    "**Jira data not included**: Jira is not configured. Set "
                    "`JIRA_URL`, `JIRA_EMAIL`, and `JIRA_TOKEN` (or `JIRA_API_TOKEN`) "
                    "in `.env` to include Jira metrics."
                )

    # Save raw data
    data = to_json(user)
    if jira_data:
        data["jira"] = to_json(jira_data)
    # Persist the language so the HTML renderer (which may run later via
    # `html-*` from data alone) localizes its labels to match the report.
    data["language"] = language

    data_path = output_dir / f"data-{stem}.json"
    with open(data_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    log(f"Data: {data_path}")

    # Render metrics report
    render_report(user, config, output_dir,
                  filename=f"metrics-{stem}.md",
                  period_label=label,
                  jira_data=jira_data,
                  status_notes=status_notes,
                  language=language)
    log("\nData collection complete!")


def _generate_html(period: str, output: str) -> None:
    """Render the final Markdown report to a styled, standalone HTML file."""
    from src.html_renderer import render_html_report

    stem = file_stem(period)
    md_path = Path(output) / f"{stem}.md"
    if not md_path.exists():
        # Fall back to the metrics-only report when no AI analysis exists yet.
        md_path = Path(output) / "data" / f"metrics-{stem}.md"
    if not md_path.exists():
        log(f"  HTML: no report found at {md_path}: skipping.")
        return
    # Structured data drives the hero + stat grid; absent, the page renders
    # from the Markdown alone.
    data_path = Path(output) / "data" / f"data-{stem}.json"
    html_path = Path(output) / f"{stem}.html"
    render_html_report(md_path, html_path, data_path if data_path.exists() else None)
    log(f"  HTML: {html_path}")


def cmd_analyze(args) -> None:
    """Run Claude analysis on existing data (retry-friendly)."""
    from src.analyze import run as analyze_run

    setup_log(_log_path("analyze", args.period))
    language = _get_language(args)

    try:
        analyze_run(args.period, args.output, language)
    except ImportError:
        log("Error: anthropic not installed. Install with: pip install anthropic")
        sys.exit(1)
    _generate_html(args.period, args.output)


def cmd_html(args) -> None:
    """Render the existing Markdown report to standalone HTML (no API calls)."""
    setup_log(_log_path("html", args.period))
    _generate_html(args.period, args.output)


def cmd_report(args) -> None:
    """Full pipeline: collect data + analyze (single pass)."""
    setup_log(_log_path("report", args.period))

    log("=" * 60)
    log("  Step 1/2: Collecting Data (GitHub + Jira)")
    log("=" * 60)
    cmd_collect(args)

    log()
    log("=" * 60)
    log("  Step 2/2: Claude AI Analysis")
    log("=" * 60)
    language = _get_language(args)
    try:
        from src.analyze import run as analyze_run
        analyze_run(args.period, args.output, language)
    except ImportError:
        log("Warning: anthropic not installed: skipping AI analysis.")
        log("         Install with: pip install anthropic")

    log()
    log("=" * 60)
    log("  Generating HTML view")
    log("=" * 60)
    _generate_html(args.period, args.output)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Self-Reporting: GitHub + Jira performance self-assessment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    for name, help_text in [
        ("report",  "Full pipeline: collect data + AI analysis + HTML (single pass)"),
        ("collect", "Collect GitHub + Jira data only (no AI analysis)"),
        ("analyze", "Run AI analysis on existing data (retry-friendly)"),
        ("html",    "Render existing Markdown report to standalone HTML"),
    ]:
        sp = sub.add_parser(name, help=help_text)
        _add_period_args(sp)

    args = parser.parse_args()
    # Surface custom-date errors as a clean argparse message, not a traceback.
    try:
        resolve_period(args.period, getattr(args, "start_date", None), getattr(args, "end_date", None))
    except ValueError as e:
        parser.error(str(e))
    {
        "report": cmd_report,
        "collect": cmd_collect,
        "analyze": cmd_analyze,
        "html": cmd_html,
    }[args.command](args)


if __name__ == "__main__":
    main()
