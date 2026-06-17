"""Collect Jira ticket metrics for a single user."""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, TYPE_CHECKING

from .jira_client import JiraClient, create_jira_client
from .utils import log

if TYPE_CHECKING:
    from .metrics import UserData

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STUCK_IN_PROGRESS_HOURS = 72

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PR_URL_RE = re.compile(r"https://github\.com/([\w.\-]+)/([\w.\-]+)/pull/(\d+)")
_AC_RE      = re.compile(r"acceptance criteria|\bacceptance\b|\bcriteria\b|\bac\b", re.IGNORECASE)
_AC_FULL_RE = re.compile(r"acceptance criteria", re.IGNORECASE)

_REVIEW_KEYWORDS = ("review", "qa", "testing", "test", "staging", "validation")
_PAUSED_KEYWORDS = ("hold", "blocked", "paused", "waiting", "impediment",
                    "stalled", "deferred", "pending")
_CLOSED_WITHOUT_CODE_KEYWORDS = ("closed", "cancelled", "canceled", "rejected",
                                  "won't fix", "wont fix", "duplicate", "invalid",
                                  "obsolete", "discarded")


def _matches_keywords(name: str, keywords: tuple) -> bool:
    lower = name.lower()
    return any(k in lower for k in keywords)


def _detect_ac(text: str) -> tuple[bool, bool]:
    """Returns (has_ac, ac_uses_full_phrase)."""
    uses_full = bool(_AC_FULL_RE.search(text))
    return bool(_AC_RE.search(text)), uses_full


_BLOCK_NODES = {"paragraph", "heading", "blockquote", "codeBlock", "listItem", "panel"}


def _adf_to_text(node: Any) -> str:
    """Recursively extract plain text from Atlassian Document Format."""
    if not node or not isinstance(node, dict):
        return ""
    node_type = node.get("type")
    if node_type == "text":
        return node.get("text", "")
    if node_type in ("inlineCard", "blockCard", "embedCard"):
        return node.get("attrs", {}).get("url", "")
    text = "".join(_adf_to_text(c) for c in node.get("content", []))
    if node_type in _BLOCK_NODES:
        text += "\n"
    return text


def _extract_pr_links(text: str, org: str) -> list[str]:
    matches = _PR_URL_RE.findall(text)
    results = []
    for gh_org, repo, num in matches:
        if not org or gh_org.lower() == org.lower():
            results.append(f"https://github.com/{gh_org}/{repo}/pull/{num}")
    return list(set(results))


def _parse_jira_dt(s: str) -> datetime:
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    return datetime.fromisoformat(s)


def _business_hours(start: datetime, end: datetime) -> float:
    """Count hours between two datetimes excluding weekends."""
    if start >= end:
        return 0.0
    total_seconds = 0.0
    current = start
    while current < end:
        if current.weekday() < 5:
            next_day = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            segment_end = min(end, next_day)
            total_seconds += (segment_end - current).total_seconds()
        current = current.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return total_seconds / 3600


def _build_jql(project_key: str, role: str, account_id: str,
               start_date: Optional[str], end_date: Optional[str] = None,
               date_field: str = "updated") -> str:
    jql = f'project = "{project_key}" AND {role} = "{account_id}" AND issuetype != Epic'
    if start_date:
        jql += f' AND {date_field} >= "{start_date}"'
    if end_date:
        try:
            end_plus_one = date.fromisoformat(end_date) + timedelta(days=1)
            jql += f' AND {date_field} < "{end_plus_one.isoformat()}"'
        except ValueError:
            jql += f' AND {date_field} <= "{end_date}"'
    return jql


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class StatusTransition:
    from_status: str
    to_status: str
    timestamp: str


@dataclass
class JiraTicket:
    key: str
    summary: str
    status: str
    status_category: str
    components: list[str] = field(default_factory=list)
    has_implementer: bool = False
    is_blocked: bool = False
    block_reason: str = ""
    time_in_status: dict[str, float] = field(default_factory=dict)
    transitions: list[StatusTransition] = field(default_factory=list)
    pr_links: list[str] = field(default_factory=list)
    linked_github_prs: list[int] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    has_ac: bool = False
    has_description: bool = False
    ac_uses_full_phrase: bool = False
    pr_linked_via_automation: bool = False
    last_comment_preview: str = ""
    issue_type: str = ""
    resolved_at: str = ""
    assigned_at: str = ""


@dataclass
class JiraRedFlag:
    ticket_key: str
    kind: str
    detail: str


@dataclass
class JiraUserData:
    jira_account_id: str
    jira_display_name: str = ""
    tickets: list[JiraTicket] = field(default_factory=list)
    total_tickets: int = 0
    tickets_with_components: int = 0
    tickets_with_implementer: int = 0
    tickets_blocked: int = 0
    tickets_with_pr_linked: int = 0
    created_tickets: list[JiraTicket] = field(default_factory=list)
    total_created: int = 0
    created_with_components: int = 0
    created_with_ac: int = 0
    monthly_resolved: dict[str, int] = field(default_factory=dict)
    red_flags: list[JiraRedFlag] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _discover_custom_fields(client: JiraClient) -> dict[str, str]:
    """Returns {lowercased_field_name: field_id} for all custom fields."""
    try:
        fields = client.get("/field")
        return {
            f["name"].lower(): f["id"]
            for f in fields
            if f.get("custom") and f.get("name")
        }
    except Exception as e:
        log(f"    Warning: could not fetch custom fields from Jira: {e}")
        return {}


def _detect_project_statuses(client: JiraClient, project_key: str) -> dict[str, dict]:
    """Returns {status_name: {category, is_review, is_paused}} for the project."""
    try:
        data = client.get(f"/project/{project_key}/statuses")
    except Exception as e:
        log(f"    Warning: could not fetch project statuses: {e}")
        return {}

    seen: dict[str, dict] = {}
    for issue_type in data:
        for s in issue_type.get("statuses", []):
            name = s.get("name", "")
            if name in seen:
                continue
            cat_key = (s.get("statusCategory") or {}).get("key", "")
            seen[name] = {
                "category": cat_key,
                "is_review": _matches_keywords(name, _REVIEW_KEYWORDS),
                "is_paused": _matches_keywords(name, _PAUSED_KEYWORDS),
            }
    return seen


def _resolve_account_id(client: JiraClient, email: str) -> tuple[Optional[str], str]:
    """Return (account_id, display_name) for the given email."""
    try:
        results = client.get("/user/search", {"query": email, "maxResults": 10})
        for u in results:
            if u.get("emailAddress", "").lower() == email.lower():
                return u["accountId"], u.get("displayName", "")
        if len(results) == 1:
            return results[0]["accountId"], results[0].get("displayName", "")
    except Exception as e:
        log(f"    Warning: could not resolve Jira accountId for {email}: {e}")
    return None, ""


# ---------------------------------------------------------------------------
# Per-ticket processing
# ---------------------------------------------------------------------------

def _compute_status_time(
    changelog: dict,
    current_status: str,
    created_at: str,
) -> tuple[dict[str, float], list[StatusTransition]]:
    histories = sorted(changelog.get("histories", []), key=lambda h: h.get("created", ""))

    events: list[dict] = []
    for h in histories:
        for item in h.get("items", []):
            if item.get("field") == "status":
                events.append({
                    "ts": h["created"],
                    "from": item.get("fromString", ""),
                    "to": item.get("toString", ""),
                })
                break

    time_in_status: dict[str, float] = {}
    transitions: list[StatusTransition] = []
    now_str = datetime.now(tz=timezone.utc).isoformat()
    segments: list[tuple[str, str, str]] = []

    if events:
        initial_status = events[0]["from"]
        segments.append((initial_status, created_at, events[0]["ts"]))
        for i, ev in enumerate(events):
            end_ts = events[i + 1]["ts"] if i + 1 < len(events) else now_str
            segments.append((ev["to"], ev["ts"], end_ts))
        transitions = [
            StatusTransition(from_status=ev["from"], to_status=ev["to"], timestamp=ev["ts"])
            for ev in events
        ]
    else:
        segments.append((current_status, created_at, now_str))

    for status, start, end in segments:
        try:
            hours = _business_hours(_parse_jira_dt(start), _parse_jira_dt(end))
            time_in_status[status] = time_in_status.get(status, 0.0) + max(0.0, hours)
        except Exception:
            pass

    return time_in_status, transitions


def _find_assigned_at(changelog: dict, account_id: str) -> str:
    """Return the timestamp of the last time account_id was assigned to the ticket."""
    histories = sorted(changelog.get("histories", []), key=lambda h: h.get("created", ""))
    last_assigned = ""
    for h in histories:
        for item in h.get("items", []):
            if item.get("field") == "assignee" and item.get("to") == account_id:
                last_assigned = h["created"]
                break
    return last_assigned


def _hours_in_progress_since(
    transitions: list[StatusTransition],
    current_status: str,
    created_at: str,
    in_progress_names: list[str],
    cutoff: str,
) -> float:
    """Hours spent in in-progress statuses from cutoff onwards."""
    now_str = datetime.now(tz=timezone.utc).isoformat()
    events = sorted(transitions, key=lambda t: t.timestamp)

    segments: list[tuple[str, str, str]] = []
    if events:
        segments.append((events[0].from_status, created_at, events[0].timestamp))
        for i, ev in enumerate(events):
            end_ts = events[i + 1].timestamp if i + 1 < len(events) else now_str
            segments.append((ev.to_status, ev.timestamp, end_ts))
    else:
        segments.append((current_status, created_at, now_str))

    try:
        cutoff_dt = _parse_jira_dt(cutoff) if cutoff else None
    except Exception:
        cutoff_dt = None

    total = 0.0
    for status, start, end in segments:
        if status not in in_progress_names:
            continue
        try:
            start_dt = _parse_jira_dt(start)
            end_dt = _parse_jira_dt(end)
            if cutoff_dt:
                start_dt = max(start_dt, cutoff_dt)
            if start_dt >= end_dt:
                continue
            total += _business_hours(start_dt, end_dt)
        except Exception:
            pass
    return total


def _process_ticket(
    issue: dict,
    changelog: dict,
    flagged_field_id: Optional[str],
    impl_field_id: Optional[str],
    status_map: dict[str, dict],
    gh_pr_numbers: set[int],
    gh_org: str,
    remote_links: list,
    dev_pr_urls: list[str],
    assignee_account_id: Optional[str] = None,
) -> Optional[JiraTicket]:
    fields = issue.get("fields", {})
    key = issue.get("key", "")

    resolution = (fields.get("resolution") or {}).get("name", "")
    if resolution.lower() == "duplicate":
        return None

    status_obj = fields.get("status", {})
    status_name = status_obj.get("name", "")
    status_cat = (status_obj.get("statusCategory") or {}).get("key", "")
    issue_type = (fields.get("issuetype") or {}).get("name", "")

    components = [c["name"] for c in (fields.get("components") or []) if c.get("name")]
    has_implementer = bool(impl_field_id and fields.get(impl_field_id))

    is_blocked, block_reason = False, ""
    if flagged_field_id and fields.get(flagged_field_id):
        is_blocked, block_reason = True, "flagged"
    elif status_map.get(status_name, {}).get("is_paused"):
        is_blocked, block_reason = True, f"paused_status:{status_name}"

    created_at = fields.get("created", "")
    time_in_status, transitions = _compute_status_time(changelog, status_name, created_at)

    pr_links: list[str] = []

    comments_list = (fields.get("comment") or {}).get("comments", [])
    for c in comments_list:
        text = _adf_to_text(c.get("body", {}))
        pr_links.extend(_extract_pr_links(text, gh_org))

    last_comment_preview = ""
    if comments_list:
        last_text = _adf_to_text(comments_list[-1].get("body", {})).strip()
        last_comment_preview = last_text[:150] + ("..." if len(last_text) > 150 else "")

    for rl in remote_links:
        url = (rl.get("object") or {}).get("url", "")
        pr_links.extend(_extract_pr_links(url, gh_org))

    pr_links = list(set(pr_links))

    linked_github_prs = []
    for url in pr_links:
        m = re.search(r"/pull/(\d+)$", url)
        if m and int(m.group(1)) in gh_pr_numbers:
            linked_github_prs.append(int(m.group(1)))

    desc_text = _adf_to_text(fields.get("description") or {})
    has_description = len(desc_text.strip()) > 50
    has_ac, ac_uses_full_phrase = _detect_ac(desc_text) if has_description else (False, False)

    pr_linked_via_automation = any(_PR_URL_RE.search(u) for u in dev_pr_urls)

    resolved_at = ""
    for t in transitions:
        if status_map.get(t.to_status, {}).get("category") == "done":
            resolved_at = t.timestamp
            break

    assigned_at = _find_assigned_at(changelog, assignee_account_id) if assignee_account_id else ""

    return JiraTicket(
        key=key,
        summary=fields.get("summary", ""),
        status=status_name,
        status_category=status_cat,
        components=components,
        has_implementer=has_implementer,
        is_blocked=is_blocked,
        block_reason=block_reason,
        time_in_status=time_in_status,
        transitions=transitions,
        pr_links=pr_links,
        linked_github_prs=linked_github_prs,
        created_at=created_at,
        updated_at=fields.get("updated", ""),
        has_ac=has_ac,
        has_description=has_description,
        ac_uses_full_phrase=ac_uses_full_phrase,
        pr_linked_via_automation=pr_linked_via_automation,
        last_comment_preview=last_comment_preview,
        issue_type=issue_type,
        resolved_at=resolved_at,
        assigned_at=assigned_at,
    )


def _fetch_tickets(
    client: JiraClient,
    jql: str,
    fields: list[str],
    flagged_field_id: Optional[str],
    impl_field_id: Optional[str],
    status_map: dict[str, dict],
    gh_pr_numbers: set[int],
    gh_org: str,
    label: str,
    assignee_account_id: Optional[str] = None,
) -> list[JiraTicket]:
    issues = client.paginate_jql(jql, fields=fields)
    log(f"    {len(issues)} {label} — fetching changelogs...")
    result: list[JiraTicket] = []
    for issue in issues:
        key = issue.get("key", "?")
        try:
            changelog  = client.get_changelog(key)
            remote_links = client.get_remote_links(key)
            dev_pr_urls  = client.get_dev_prs(issue.get("id", ""))
            ticket = _process_ticket(
                issue, changelog,
                flagged_field_id, impl_field_id,
                status_map, gh_pr_numbers, gh_org,
                remote_links, dev_pr_urls,
                assignee_account_id=assignee_account_id,
            )
            if ticket:
                result.append(ticket)
        except Exception as e:
            log(f"    Warning: could not process {key}: {e}")
    return result


def _evaluate_red_flags(
    ticket: JiraTicket,
    in_progress_names: list[str],
    status_map: dict[str, dict],
    stuck_threshold_hours: float,
) -> list[JiraRedFlag]:
    flags: list[JiraRedFlag] = []

    hours_in_progress = _hours_in_progress_since(
        ticket.transitions, ticket.status, ticket.created_at,
        in_progress_names, ticket.assigned_at,
    )
    is_in_review = status_map.get(ticket.status, {}).get("is_review", False)
    ever_reached_review = any(
        status_map.get(t.to_status, {}).get("is_review")
        for t in ticket.transitions
    )
    returned_from_review = (
        ticket.status_category == "indeterminate"
        and not is_in_review
        and ever_reached_review
    )
    pr_expected = is_in_review or ticket.status_category == "done" or returned_from_review
    likely_reviewer = is_in_review and not ticket.has_implementer

    if (
        hours_in_progress > stuck_threshold_hours
        and not ever_reached_review
        and ticket.status_category != "done"
    ):
        flags.append(JiraRedFlag(
            ticket_key=ticket.key,
            kind="stuck_in_progress",
            detail=f"{hours_in_progress:.0f}h in progress without reaching code review",
        ))

    if ticket.is_blocked:
        detail = f"Blocked ({ticket.block_reason})"
        if ticket.last_comment_preview:
            detail += f" — {ticket.last_comment_preview}"
        flags.append(JiraRedFlag(
            ticket_key=ticket.key,
            kind="blocked",
            detail=detail,
        ))

    if not ticket.components:
        flags.append(JiraRedFlag(
            ticket_key=ticket.key,
            kind="missing_components",
            detail="Components field empty",
        ))

    if not ticket.has_implementer and pr_expected and not likely_reviewer:
        flags.append(JiraRedFlag(
            ticket_key=ticket.key,
            kind="missing_implementer",
            detail="Implementer field not set",
        ))

    status_lower = ticket.status.lower()
    closed_without_code = any(k in status_lower for k in _CLOSED_WITHOUT_CODE_KEYWORDS)
    if pr_expected and not ticket.pr_links and not closed_without_code and not likely_reviewer:
        if not ticket.pr_linked_via_automation:
            flags.append(JiraRedFlag(
                ticket_key=ticket.key,
                kind="no_pr_linked",
                detail=f"No GitHub PR linked (status: {ticket.status})",
            ))

    return flags


def _evaluate_created_flags(ticket: JiraTicket) -> list[JiraRedFlag]:
    flags: list[JiraRedFlag] = []

    if not ticket.has_description:
        flags.append(JiraRedFlag(
            ticket_key=ticket.key,
            kind="missing_description",
            detail="Empty or very short description",
        ))

    if not ticket.has_ac and ticket.issue_type.lower() not in ("bug", "defect"):
        flags.append(JiraRedFlag(
            ticket_key=ticket.key,
            kind="missing_ac",
            detail="No Acceptance Criteria section",
        ))

    if not ticket.components:
        flags.append(JiraRedFlag(
            ticket_key=ticket.key,
            kind="missing_components",
            detail="Components field empty",
        ))

    return flags


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def collect_jira_metrics(
    config: dict,
    user: "UserData",
) -> Optional["JiraUserData"]:
    """Collect Jira metrics for the single configured user. Returns None if Jira is not configured."""
    jira_cfg = config.get("jira") or {}
    if not jira_cfg or jira_cfg.get("enabled") is False:
        return None

    base_url    = os.environ.get("JIRA_URL", "")
    email       = os.environ.get("JIRA_EMAIL", "")
    token       = os.environ.get("JIRA_TOKEN", "")
    project_key = os.environ.get("JIRA_PROJECT_KEY", "")

    if not all([base_url, email, token, project_key]):
        log("  Jira config incomplete (missing JIRA_URL/JIRA_EMAIL/JIRA_TOKEN/JIRA_PROJECT_KEY) — skipping Jira")
        return None

    client = create_jira_client(base_url, email, token)

    dr = config.get("date_range", {})
    start_date = dr.get("start")
    end_date   = dr.get("end")
    stuck_hours = float(STUCK_IN_PROGRESS_HOURS)
    jira_email = jira_cfg.get("email", "")
    gh_org = os.environ.get("GITHUB_ORG", "")

    if not jira_email:
        log("  No Jira email configured — skipping Jira")
        return None

    field_map        = _discover_custom_fields(client)
    impl_field_id    = field_map.get("implementer")
    flagged_field_id = field_map.get("flagged")

    if not impl_field_id:
        log("    Warning: 'implementer' field not found in Jira — has_implementer will always be False")

    status_map = _detect_project_statuses(client, project_key)

    in_progress_names = [
        s for s, info in status_map.items()
        if info.get("category") == "indeterminate"
        and not info.get("is_review")
        and not info.get("is_paused")
    ]

    fields_to_fetch = ["summary", "status", "issuetype", "components", "comment", "created", "updated", "resolution"]
    if impl_field_id:
        fields_to_fetch.append(impl_field_id)
    if flagged_field_id:
        fields_to_fetch.append(flagged_field_id)
    fields_created = fields_to_fetch + ["description"]

    login = user.login
    log(f"\n  Jira @{login} ({jira_email})...")

    account_id, display_name = _resolve_account_id(client, jira_email)
    if not account_id:
        raise RuntimeError(f"Could not resolve Jira accountId for {jira_email} (@{login})")

    gh_pr_numbers = {s.number for s in user.pr_samples}
    shared = dict(
        client=client,
        flagged_field_id=flagged_field_id,
        impl_field_id=impl_field_id,
        status_map=status_map,
        gh_pr_numbers=gh_pr_numbers,
        gh_org=gh_org,
    )

    log(f"    Fetching assigned tickets...")
    tickets = _fetch_tickets(
        **shared,
        jql=_build_jql(project_key, "assignee", account_id, start_date, end_date=end_date),
        fields=fields_to_fetch,
        label="assigned tickets",
        assignee_account_id=account_id,
    )

    log(f"    Fetching created tickets...")
    created_tickets = _fetch_tickets(
        **shared,
        jql=_build_jql(project_key, "reporter", account_id, start_date,
                       end_date=end_date, date_field="created"),
        fields=fields_created,
        label="created tickets",
    )

    monthly_counter: Counter[str] = Counter()
    for t in tickets:
        if t.resolved_at:
            monthly_counter[t.resolved_at[:7]] += 1

    jira_ud = JiraUserData(
        jira_account_id=account_id,
        jira_display_name=display_name or jira_email,
        tickets=tickets,
        total_tickets=len(tickets),
        tickets_with_components=sum(1 for t in tickets if t.components),
        tickets_with_implementer=sum(1 for t in tickets if t.has_implementer),
        tickets_blocked=sum(1 for t in tickets if t.is_blocked),
        tickets_with_pr_linked=sum(1 for t in tickets if t.pr_links),
        created_tickets=created_tickets,
        total_created=len(created_tickets),
        created_with_components=sum(1 for t in created_tickets if t.components),
        created_with_ac=sum(1 for t in created_tickets if t.has_ac),
        monthly_resolved=dict(sorted(monthly_counter.items())),
    )

    for ticket in tickets:
        jira_ud.red_flags.extend(
            _evaluate_red_flags(ticket, in_progress_names, status_map, stuck_hours)
        )
    for ticket in created_tickets:
        jira_ud.red_flags.extend(_evaluate_created_flags(ticket))

    seen: set[tuple] = set()
    jira_ud.red_flags = [
        rf for rf in jira_ud.red_flags
        if (rf.ticket_key, rf.kind) not in seen
        and not seen.add((rf.ticket_key, rf.kind))  # type: ignore[func-returns-value]
    ]

    return jira_ud
