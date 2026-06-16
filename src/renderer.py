"""Generate Markdown self-report for a single user."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .metrics import UserData
from .utils import log

if TYPE_CHECKING:
    from .jira_metrics import JiraUserData


def render_report(
    user: UserData,
    config: dict,
    output_dir: Path,
    filename: str = "report.md",
    period_label: str = "",
    jira_data: Optional["JiraUserData"] = None,
) -> Path:
    content = _markdown(user, config, period_label, jira_data)
    path = output_dir / filename
    path.write_text(content, encoding="utf-8")
    log(f"Report: {path}")
    return path


def _fmt_hours(h: Optional[float]) -> str:
    if h is None:
        return "—"
    return f"{h:.1f}h" if h < 24 else f"{h / 24:.1f}d"


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def _truncate(s: str, n: int = 50) -> str:
    return s[:n] + "..." if len(s) > n else s


def _markdown(
    user: UserData,
    config: dict,
    period_label: str = "",
    jira_data: Optional["JiraUserData"] = None,
) -> str:
    dr    = config.get("date_range", {})
    org   = os.environ.get("GITHUB_ORG", "")
    repos = org or ", ".join(f"{r['owner']}/{r['repo']}" for r in config.get("repositories", []))

    pr, r, ra = user.pull_requests, user.reviews, user.reviewer_activity

    display_name = ""
    if jira_data and jira_data.jira_display_name:
        display_name = jira_data.jira_display_name

    title = f"# Self-Report — {display_name}" if display_name else f"# Self-Report — @{user.login}"

    lines = [
        title,
        "",
        f"**Org / Repos:** {repos}  ",
        f"**Period:** {period_label or dr.get('start', '—') + ' to today'}  ",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Pull Requests",
        "",
        f"- Opened: {pr.opened}  |  Merged: **{pr.merged}**  |  Closed without merge: {pr.closed_unmerged}",
        f"- Average merge time: {_fmt_hours(pr.avg_merge_hours)}",
        f"- Average description length: {pr.avg_description_length:.0f} chars",
        f"- Empty descriptions: {pr.empty_descriptions}",
        f"- Average files changed: {pr.avg_files_changed:.1f}",
        f"- Average lines changed: {pr.avg_lines_changed:.0f}",
    ]

    draft_count = sum(1 for p in user.pr_samples if p.is_draft)
    if draft_count:
        lines.append(f"- Draft PRs: {draft_count}")

    lines += [
        "",
        "## Review Comments Received",
        "",
        f"- Threads received: **{r.threads_received}**",
        f"- Resolved: {r.resolved} ({_pct(r.resolution_rate)})",
        f"- Ignored (merged without addressing): **{r.ignored}** ({_pct(r.ignore_rate)})",
    ]
    if r.reacted_only:
        lines.append(f"- Acknowledged with reaction (no reply): {r.reacted_only}")
    lines.append(f"- Replied without resolving: {r.replied_not_resolved}")
    if r.open_pr_unresolved:
        lines.append(f"- Pending on open PRs: {r.open_pr_unresolved} (not counted in resolution rate)")

    lines += [
        "",
        "## Reviews Given",
        "",
        "### Human PRs",
        "",
        f"- PRs reviewed: **{ra.human_prs_reviewed}**",
        f"- Approved: {ra.human_approvals} | Changes requested: {ra.human_changes_requested} | Comments only: {ra.human_comments}",
    ]

    if ra.bot_prs_reviewed:
        lines += [
            "",
            "### Bot PRs",
            "",
            f"- PRs reviewed: **{ra.bot_prs_reviewed}**",
            f"- Approved: {ra.bot_approvals} | Changes requested: {ra.bot_changes_requested} | Comments only: {ra.bot_comments}",
        ]

    if jira_data:
        lines += _render_jira_section(jira_data)

    lines.append("")
    return "\n".join(lines)


_SIGNIFICANT_FLAG_KINDS = frozenset({"stuck_in_progress", "blocked", "no_pr_linked", "no_pr_comment"})

_KIND_LABELS: dict[str, str] = {
    "stuck_in_progress":   "Stuck",
    "blocked":             "Blocked",
    "missing_components":  "No components",
    "missing_implementer": "No implementer",
    "no_pr_linked":        "No PR",
    "no_pr_comment":       "No PR comment",
    "missing_ac":          "No AC",
    "missing_description": "No description",
}


def _fmt_state_journey(time_in_status: dict[str, float]) -> str:
    """Format ticket state journey showing time in each meaningful state."""
    skip_states = {"open", "ready", "closed"}
    parts = []
    for state, hours in time_in_status.items():
        if state.lower() in skip_states:
            continue
        if hours < 0.01:
            continue
        if hours < 1:
            parts.append(f"{state}: {hours * 60:.0f}m")
        elif hours < 24:
            parts.append(f"{state}: {hours:.1f}h")
        else:
            parts.append(f"{state}: {hours / 24:.1f}d")
    return " > ".join(parts) if parts else "—"


def _render_jira_section(jd: "JiraUserData") -> list[str]:
    lines = [
        "",
        "## Jira Tickets",
        "",
        f"- Total: **{jd.total_tickets}**  |  "
        f"With components: {jd.tickets_with_components}  |  "
        f"With implementer: {jd.tickets_with_implementer}  |  "
        f"Blocked: {jd.tickets_blocked}  |  "
        f"With PR linked: {jd.tickets_with_pr_linked}",
    ]

    if not jd.tickets:
        lines += ["", "_No tickets in this period._", ""]
        return lines

    lines += [
        "",
        "| Key | Summary | Status | State Journey | Blocked | PR Linked |",
        "| --- | ------- | ------ | ------------- | ------- | --------- |",
    ]
    for t in jd.tickets:
        blocked_str = "Yes" if t.is_blocked else "No"
        if t.pr_links:
            pr_str = "Yes"
        elif t.pr_linked_via_automation:
            pr_str = "Automation"
        else:
            pr_str = "No"
        journey = _fmt_state_journey(t.time_in_status)
        lines.append(
            f"| {t.key} | {_truncate(t.summary)} | {t.status} | "
            f"{journey} | {blocked_str} | {pr_str} |"
        )

    if jd.red_flags:
        by_ticket: dict[str, list] = {}
        for rf in jd.red_flags:
            by_ticket.setdefault(rf.ticket_key, []).append(rf)

        lines += [
            "",
            "### Red Flags",
            "",
            "| Ticket | Flags | Detail |",
            "| ------ | ----- | ------ |",
        ]
        for key, flags in by_ticket.items():
            kinds = ", ".join(_KIND_LABELS.get(rf.kind, rf.kind) for rf in flags)
            significant = [rf for rf in flags if rf.kind in _SIGNIFICANT_FLAG_KINDS]
            detail = " / ".join(rf.detail for rf in significant) if significant else "—"
            lines.append(f"| {key} | {kinds} | {detail} |")

    if jd.total_created > 0:
        lines += [
            "",
            "### Tickets Created",
            "",
            f"> **Total:** {jd.total_created} | "
            f"With components: {jd.created_with_components} | "
            f"With AC: {jd.created_with_ac}",
            "",
            "| Key | Summary | Type | Components | AC | Description |",
            "| --- | ------- | ---- | ---------- | -- | ----------- |",
        ]
        for t in jd.created_tickets:
            comp_str = ", ".join(t.components) if t.components else "—"
            if not t.has_ac:
                ac_str = "No"
            elif t.ac_uses_full_phrase:
                ac_str = "Yes"
            else:
                ac_str = "Abbreviated"
            desc_str = "Yes" if t.has_description else "No"
            lines.append(
                f"| {t.key} | {_truncate(t.summary)} | {t.issue_type or '—'} | {comp_str} | {ac_str} | {desc_str} |"
            )

    return lines
