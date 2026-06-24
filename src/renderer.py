"""Generate Markdown self-report for a single user."""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .metrics import UserData
from .utils import log, is_multi_month

if TYPE_CHECKING:
    from .jira_metrics import JiraUserData

# Tool authors, in order of contribution. The first is the primary author.
REPORT_AUTHOR = "ChristianSavinoDP"
REPORT_COLLABORATORS = ["ffernandez-dailypay", "logan-dp"]


# Code-generated report strings, per language. The AI analysis already honors
# the language; these keep the metrics sections (rendered here) in the same
# language so the report is never half English, half Spanish. Falls back to
# English for any unsupported language.
_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "org_repos": "Org / Repos",
        "period": "Period",
        "reports_to": "Reports to",
        "generated": "Generated",
        "data_sources": "Data sources",
        "pull_requests": "Pull Requests",
        "pr_opened": "Opened", "pr_merged": "Merged", "pr_closed": "Closed without merge",
        "pr_avg_merge": "Average merge time",
        "pr_avg_desc": "Average description length",
        "pr_empty_desc": "Empty descriptions",
        "pr_avg_files": "Average files changed",
        "pr_avg_lines": "Average lines changed",
        "pr_drafts": "Draft PRs",
        "chars": "chars",
        "reviews_received": "Review Comments Received",
        "threads_received": "Threads received",
        "resolved": "Resolved",
        "ignored": "Ignored (merged without addressing)",
        "reacted_only": "Acknowledged with reaction (no reply)",
        "replied_not_resolved": "Replied without resolving",
        "pending_open": "Pending on open PRs",
        "not_counted": "not counted in resolution rate",
        "reviews_given": "Reviews Given",
        "human_prs": "Human PRs", "bot_prs": "Bot PRs",
        "prs_reviewed": "PRs reviewed",
        "approved": "Approved", "changes_requested": "Changes requested", "comments_only": "Comments only",
        "threads_breakdown": "Review Threads Breakdown",
        "pie_resolved": "Resolved", "pie_outdated": "Outdated", "pie_reacted": "Acknowledged",
        "pie_replied": "Replied, not resolved", "pie_ignored": "Ignored", "pie_open": "Open / pending",
        "monthly_progress": "Monthly Progress: Tickets Resolved",
        "chart_title": "Tickets resolved per month",
        "chart_y": "Tickets",
        "jira_tickets": "Jira Tickets",
        "total": "Total", "with_components": "With components", "with_implementer": "With implementer",
        "blocked": "Blocked", "with_pr_linked": "With PR linked",
        "no_tickets": "_No tickets in this period._",
        "col_key": "Key", "col_summary": "Summary", "col_status": "Status",
        "col_journey": "State Journey", "col_blocked": "Blocked", "col_pr_linked": "PR Linked",
        "red_flags": "Red Flags",
        "col_ticket": "Ticket", "col_flags": "Flags", "col_detail": "Detail",
        "tickets_created": "Tickets Created",
        "with_ac": "With AC",
        "col_type": "Type", "col_components": "Components", "col_ac": "AC", "col_description": "Description",
        "epics_contributed": "Epics Contributed",
        "yes": "Yes", "no": "No", "automation": "Automation", "abbreviated": "Abbreviated",
    },
    "es": {
        "org_repos": "Org / Repos",
        "period": "Periodo",
        "reports_to": "Reporta a",
        "generated": "Generado",
        "data_sources": "Fuentes de datos",
        "pull_requests": "Pull Requests",
        "pr_opened": "Abiertos", "pr_merged": "Merged", "pr_closed": "Cerrados sin merge",
        "pr_avg_merge": "Tiempo promedio hasta merge",
        "pr_avg_desc": "Largo promedio de descripcion",
        "pr_empty_desc": "Descripciones vacias",
        "pr_avg_files": "Promedio de archivos cambiados",
        "pr_avg_lines": "Promedio de lineas cambiadas",
        "pr_drafts": "PRs en draft",
        "chars": "chars",
        "reviews_received": "Comentarios de Review Recibidos",
        "threads_received": "Threads recibidos",
        "resolved": "Resueltos",
        "ignored": "Ignorados (merged sin atender)",
        "reacted_only": "Reconocidos con reaccion (sin responder)",
        "replied_not_resolved": "Respondidos sin resolver",
        "pending_open": "Pendientes en PRs abiertos",
        "not_counted": "no cuentan en la tasa de resolucion",
        "reviews_given": "Reviews Realizados",
        "human_prs": "PRs de Humanos", "bot_prs": "PRs de Bots",
        "prs_reviewed": "PRs revisados",
        "approved": "Aprobados", "changes_requested": "Cambios solicitados", "comments_only": "Solo comentarios",
        "threads_breakdown": "Desglose de Threads de Review",
        "pie_resolved": "Resueltos", "pie_outdated": "Obsoletos", "pie_reacted": "Reconocidos",
        "pie_replied": "Respondidos, sin resolver", "pie_ignored": "Ignorados", "pie_open": "Abiertos / pendientes",
        "monthly_progress": "Progreso Mensual: Tickets Resueltos",
        "chart_title": "Tickets resueltos por mes",
        "chart_y": "Tickets",
        "jira_tickets": "Tickets de Jira",
        "total": "Total", "with_components": "Con componentes", "with_implementer": "Con implementer",
        "blocked": "Bloqueados", "with_pr_linked": "Con PR vinculado",
        "no_tickets": "_Sin tickets en este periodo._",
        "col_key": "Key", "col_summary": "Resumen", "col_status": "Estado",
        "col_journey": "Recorrido de Estados", "col_blocked": "Bloqueado", "col_pr_linked": "PR Vinculado",
        "red_flags": "Red Flags",
        "col_ticket": "Ticket", "col_flags": "Flags", "col_detail": "Detalle",
        "tickets_created": "Tickets Creados",
        "with_ac": "Con AC",
        "col_type": "Tipo", "col_components": "Componentes", "col_ac": "AC", "col_description": "Descripcion",
        "epics_contributed": "Epicas Contribuidas",
        "yes": "Si", "no": "No", "automation": "Automatizacion", "abbreviated": "Abreviado",
    },
}


def _strings(language: str) -> dict[str, str]:
    return _STRINGS.get((language or "en").lower(), _STRINGS["en"])


def render_report(
    user: UserData,
    config: dict,
    output_dir: Path,
    filename: str = "report.md",
    period_label: str = "",
    jira_data: Optional["JiraUserData"] = None,
    status_notes: Optional[list] = None,
    language: str = "en",
) -> Path:
    content = _markdown(user, config, period_label, jira_data, status_notes, language)
    path = output_dir / filename
    path.write_text(content, encoding="utf-8")
    log(f"Report: {path}")
    return path


def _fmt_hours(h: Optional[float]) -> str:
    if h is None:
        return "-"
    return f"{h:.1f}h" if h < 24 else f"{h / 24:.1f}d"


def _pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def _truncate(s: str, n: int = 50) -> str:
    # Strip first: a trailing space inside a table cell trips markdownlint's
    # table-pipe-alignment rule (MD060) and reads as a stray gap in the HTML.
    s = s.strip()
    return s[:n] + "..." if len(s) > n else s


def _normalize_blanks(md: str) -> str:
    """Collapse runs of blank lines to a single one (MD012). Sections that each
    pad with a leading and trailing blank line would otherwise stack doubles."""
    return re.sub(r"\n{3,}", "\n\n", md)


_MONTH_ABBR = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
    "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}


def _month_label(ym: str) -> str:
    """'2026-03' -> \"Mar '26\"."""
    try:
        year, month = ym.split("-")
        return f"{_MONTH_ABBR.get(month, month)} '{year[2:]}"
    except ValueError:
        return ym


def _monthly_resolution_chart(monthly: dict[str, int], login: str, t: dict[str, str]) -> list[str]:
    """Mermaid bar chart of resolved tickets per month (long periods only)."""
    if len(monthly) < 2:
        return []
    months = sorted(monthly.keys())
    labels = ", ".join(f'"{_month_label(m)}"' for m in months)
    values = ", ".join(str(monthly[m]) for m in months)
    max_val = max(monthly.values())
    return [
        "",
        f"## {t['monthly_progress']}",
        "",
        "```mermaid",
        "xychart-beta",
        f'    title "{t["chart_title"]}: @{login}"',
        f"    x-axis [{labels}]",
        f'    y-axis "{t["chart_y"]}" 0 --> {max_val + 1}',
        f"    bar [{values}]",
        "```",
        "",
    ]


def _threads_pie(r, t: dict[str, str]) -> list[str]:
    """Mermaid pie of how received review threads were handled.

    Only the non-zero slices are emitted. Skipped entirely when no threads were
    received, so an empty period does not render a blank circle.
    """
    if r.threads_received <= 0:
        return []
    slices = [
        (t["pie_resolved"], r.resolved),
        (t["pie_outdated"], r.outdated),
        (t["pie_reacted"], r.reacted_only),
        (t["pie_replied"], r.replied_not_resolved),
        (t["pie_ignored"], r.ignored),
        (t["pie_open"], r.open_pr_unresolved),
    ]
    rows = [f'    "{label}" : {value}' for label, value in slices if value > 0]
    if not rows:
        return []
    return [
        "",
        f"### {t['threads_breakdown']}",
        "",
        "```mermaid",
        "pie showData",
        *rows,
        "```",
        "",
    ]


def _markdown(
    user: UserData,
    config: dict,
    period_label: str = "",
    jira_data: Optional["JiraUserData"] = None,
    status_notes: Optional[list] = None,
    language: str = "en",
) -> str:
    t     = _strings(language)
    dr    = config.get("date_range", {})
    org   = os.environ.get("GITHUB_ORG", "")
    repos = org or ", ".join(f"{r['owner']}/{r['repo']}" for r in config.get("repositories", []))

    pr, r, ra = user.pull_requests, user.reviews, user.reviewer_activity

    display_name = ""
    if jira_data and jira_data.jira_display_name:
        display_name = jira_data.jira_display_name

    subject = display_name if display_name else f"@{user.login}"
    title = f"# Self-Report: {subject}"

    lines = [
        title,
        "",
        f"**{t['org_repos']}:** {repos}  ",
        f"**{t['period']}:** {period_label or dr.get('start', '-') + ' to today'}  ",
    ]
    if jira_data and jira_data.reports_to:
        lines.append(f"**{t['reports_to']}:** {jira_data.reports_to}  ")
    lines += [
        f"**{t['generated']}:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    if status_notes:
        lines.append(f"> [!WARNING] {t['data_sources']}")
        for note in status_notes:
            lines.append(f"> - {note}")
        lines.append("")

    lines += [
        f"## {t['pull_requests']}",
        "",
        f"- {t['pr_opened']}: {pr.opened}  |  {t['pr_merged']}: **{pr.merged}**  |  {t['pr_closed']}: {pr.closed_unmerged}",
        f"- {t['pr_avg_merge']}: {_fmt_hours(pr.avg_merge_hours)}",
        f"- {t['pr_avg_desc']}: {pr.avg_description_length:.0f} {t['chars']}",
        f"- {t['pr_empty_desc']}: {pr.empty_descriptions}",
        f"- {t['pr_avg_files']}: {pr.avg_files_changed:.1f}",
        f"- {t['pr_avg_lines']}: {pr.avg_lines_changed:.0f}",
    ]

    draft_count = sum(1 for p in user.pr_samples if p.is_draft)
    if draft_count:
        lines.append(f"- {t['pr_drafts']}: {draft_count}")

    lines += [
        "",
        f"## {t['reviews_received']}",
        "",
        f"- {t['threads_received']}: **{r.threads_received}**",
        f"- {t['resolved']}: {r.resolved} ({_pct(r.resolution_rate)})",
        f"- {t['ignored']}: **{r.ignored}** ({_pct(r.ignore_rate)})",
    ]
    if r.reacted_only:
        lines.append(f"- {t['reacted_only']}: {r.reacted_only}")
    lines.append(f"- {t['replied_not_resolved']}: {r.replied_not_resolved}")
    if r.open_pr_unresolved:
        lines.append(f"- {t['pending_open']}: {r.open_pr_unresolved} ({t['not_counted']})")

    lines += _threads_pie(r, t)

    lines += [
        "",
        f"## {t['reviews_given']}",
        "",
        f"### {t['human_prs']}",
        "",
        f"- {t['prs_reviewed']}: **{ra.human_prs_reviewed}**",
        f"- {t['approved']}: {ra.human_approvals} | {t['changes_requested']}: {ra.human_changes_requested} | {t['comments_only']}: {ra.human_comments}",
    ]

    if ra.bot_prs_reviewed:
        lines += [
            "",
            f"### {t['bot_prs']}",
            "",
            f"- {t['prs_reviewed']}: **{ra.bot_prs_reviewed}**",
            f"- {t['approved']}: {ra.bot_approvals} | {t['changes_requested']}: {ra.bot_changes_requested} | {t['comments_only']}: {ra.bot_comments}",
        ]

    # Monthly chart sits right after Reviews Given (before the Jira tables).
    if jira_data:
        multi_month = is_multi_month(dr.get("start"), dr.get("end"))
        if multi_month and jira_data.monthly_resolved:
            lines += _monthly_resolution_chart(
                jira_data.monthly_resolved, jira_data.jira_display_name or "user", t
            )
        lines += _render_jira_section(jira_data, t, language)

    body = _normalize_blanks("\n".join(lines).rstrip())
    return _normalize_blanks(body + "\n\n" + credits_block()).rstrip() + "\n"


# Unique opening of the credits footer. The analysis step anchors on this to
# strip and re-append the footer, keeping credits last in the final document.
CREDITS_ANCHOR = "**Report made by**"


def credits_block() -> str:
    """Attribution footer: all authors on one line, primary author first."""
    names = ", ".join(f"@{n}" for n in [REPORT_AUTHOR, *REPORT_COLLABORATORS])
    return f"---\n\n{CREDITS_ANCHOR} {names}\n"


_SIGNIFICANT_FLAG_KINDS = frozenset({"stuck_in_progress", "blocked", "no_pr_linked", "no_pr_comment"})

_KIND_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "stuck_in_progress":   "Stuck",
        "blocked":             "Blocked",
        "missing_components":  "No components",
        "missing_implementer": "No implementer",
        "no_pr_linked":        "No PR",
        "no_pr_comment":       "No PR comment",
        "missing_ac":          "No AC",
        "missing_description": "No description",
    },
    "es": {
        "stuck_in_progress":   "Estancado",
        "blocked":             "Bloqueado",
        "missing_components":  "Sin componentes",
        "missing_implementer": "Sin implementer",
        "no_pr_linked":        "Sin PR",
        "no_pr_comment":       "Sin comentario de PR",
        "missing_ac":          "Sin AC",
        "missing_description": "Sin descripcion",
    },
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
    return " > ".join(parts) if parts else "-"


def _render_jira_section(jd: "JiraUserData", t: dict[str, str], language: str = "en") -> list[str]:
    summary = (
        f"- {t['total']}: **{jd.total_tickets}**  |  "
        f"{t['with_components']}: {jd.tickets_with_components}  |  "
        f"{t['with_implementer']}: {jd.tickets_with_implementer}  |  "
        f"{t['blocked']}: {jd.tickets_blocked}  |  "
        f"{t['with_pr_linked']}: {jd.tickets_with_pr_linked}"
    )
    if jd.epics_contributed:
        summary += f"  |  {t['epics_contributed']}: {jd.epics_contributed}"
    lines = ["", f"## {t['jira_tickets']}", "", summary]

    if not jd.tickets:
        lines += ["", t["no_tickets"], ""]
        return lines

    lines += [
        "",
        f"| {t['col_key']} | {t['col_summary']} | {t['col_status']} | {t['col_journey']} | {t['col_blocked']} | {t['col_pr_linked']} |",
        "| --- | ------- | ------ | ------------- | ------- | --------- |",
    ]
    for tk in jd.tickets:
        blocked_str = t["yes"] if tk.is_blocked else t["no"]
        if tk.pr_links:
            pr_str = t["yes"]
        elif tk.pr_linked_via_automation:
            pr_str = t["automation"]
        else:
            pr_str = t["no"]
        journey = _fmt_state_journey(tk.time_in_status)
        lines.append(
            f"| {tk.key} | {_truncate(tk.summary)} | {tk.status} | "
            f"{journey} | {blocked_str} | {pr_str} |"
        )

    if jd.red_flags:
        kind_labels = _KIND_LABELS.get((language or "en").lower(), _KIND_LABELS["en"])
        by_ticket: dict[str, list] = {}
        for rf in jd.red_flags:
            by_ticket.setdefault(rf.ticket_key, []).append(rf)

        lines += [
            "",
            f"### {t['red_flags']}",
            "",
            f"| {t['col_ticket']} | {t['col_flags']} | {t['col_detail']} |",
            "| ------ | ----- | ------ |",
        ]
        for key, flags in by_ticket.items():
            kinds = ", ".join(kind_labels.get(rf.kind, rf.kind) for rf in flags)
            significant = [rf for rf in flags if rf.kind in _SIGNIFICANT_FLAG_KINDS]
            detail = " / ".join(rf.detail for rf in significant) if significant else "-"
            lines.append(f"| {key} | {kinds} | {detail} |")

    if jd.total_created > 0:
        lines += [
            "",
            f"### {t['tickets_created']}",
            "",
            f"> **{t['total']}:** {jd.total_created} | "
            f"{t['with_components']}: {jd.created_with_components} | "
            f"{t['with_ac']}: {jd.created_with_ac}",
            "",
            f"| {t['col_key']} | {t['col_summary']} | {t['col_type']} | {t['col_components']} | {t['col_ac']} | {t['col_description']} |",
            "| --- | ------- | ---- | ---------- | -- | ----------- |",
        ]
        for tk in jd.created_tickets:
            comp_str = ", ".join(tk.components) if tk.components else "-"
            if not tk.has_ac:
                ac_str = t["no"]
            elif tk.ac_uses_full_phrase:
                ac_str = t["yes"]
            else:
                ac_str = t["abbreviated"]
            desc_str = t["yes"] if tk.has_description else t["no"]
            lines.append(
                f"| {tk.key} | {_truncate(tk.summary)} | {tk.issue_type or '-'} | {comp_str} | {ac_str} | {desc_str} |"
            )

    return lines
