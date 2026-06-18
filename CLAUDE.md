# Self-Reporting: Claude Instructions

## What This Is

A self-assessment tool that generates performance reports for a single developer based on their GitHub and Jira activity. The developer runs it on themselves to get an objective view of their work patterns.

## Commands

| Command                  | Description                                             |
| ------------------------ | ------------------------------------------------------- |
| `make report-2w`         | Full report: last 2 weeks (GitHub + Jira + AI + HTML)   |
| `make report-monthly`    | Full report: current month                              |
| `make report-last-month` | Full report: last complete month                        |
| `make report-yearly`     | Full report: year to date                               |
| `make report-historic`   | Full report: all time                                   |
| `make report-custom`     | Full report: `START=YYYY-MM-DD END=YYYY-MM-DD`          |
| `make collect-*`         | Data collection only (no AI analysis)                   |
| `make analyze-*`         | Re-run AI analysis on existing data (retry-friendly)    |
| `make html-*`            | Re-render existing report to standalone HTML            |

Every `report-*` run also writes `output/self-report-*.html` (styled view, Mermaid charts).

Language override: `make report-2w REPORT_LANG=es`

## Interaction Flow

When the user starts a conversation (greeting, generic message, or asks for help), use `AskUserQuestion` with `multiSelect: false` to present a menu:

| Label                       | Description                                         |
| --------------------------- | --------------------------------------------------- |
| Self-Report (2 weeks)       | Generate your self-assessment for the last 2 weeks  |
| Self-Report (current month) | Generate your self-assessment for the current month |
| Self-Report (last month)    | Generate your self-assessment for last month        |
| Self-Report (year to date)  | Generate your self-assessment for the current year  |
| Clean reports and logs      | Delete generated outputs and logs                   |

For all-time (`report-historic`) or arbitrary ranges (`report-custom START=YYYY-MM-DD END=YYYY-MM-DD`), run the command directly when the user asks.

After selection:

- If **Self-Report** (any period) -> ask for language with `AskUserQuestion`:

  | Label   | Description                      |
  | ------- | -------------------------------- |
  | English | Report in English (default)      |
  | Spanish | Report in Latin American Spanish |

  Then run the corresponding `make report-*` command with `REPORT_LANG=es` if Spanish was chosen.

- If **Clean reports and logs** -> run `make clean`

## Authentication

This tool runs inside Claude Code, which authenticates the AI analysis automatically and runs it on Opus. No `ANTHROPIC_API_KEY` is needed, and running outside Claude Code is not supported. Proceed with `make report-*` directly; do not prompt the user for an API key.

## After Running a Command

1. Read the log file in `logs/`
2. If the log contains `Error` -> report it and ask if the user wants to retry
3. If no errors -> show BOTH output files as clickable markdown links: the Markdown report `[filename.md](file:///absolute/path/to/file.md)` and the HTML view `[filename.html](file:///absolute/path/to/file.html)`. Do NOT use backticks or plain text for paths.

## Retry Logic

If the AI analysis fails, the data collection that ran before it does not need to repeat. Use `make analyze-*` to re-run only the analysis (it also regenerates the HTML). Always suggest this when analysis fails.

The `collect-*` and `analyze-*` commands exist as recovery tools. They are not shown in the main menu; only suggest them when a failure occurs.

## Project Structure

| File                   | Purpose                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `main.py`              | Unified entry point: `report`, `collect`, `analyze`, `html` subcommands |
| `.env`                 | All configuration: tokens, org, optional Jira project key               |
| `prompt.md`            | Analysis instructions for Claude                                        |
| `src/metrics.py`       | GitHub API data collection                                              |
| `src/jira_metrics.py`  | Jira API data collection + reports-to auto-detection                    |
| `src/renderer.py`      | Markdown report generation (tables, monthly trend chart)                |
| `src/html_renderer.py` | Markdown -> standalone styled HTML (Mermaid charts)                     |
| `src/analyze.py`       | Claude AI narrative analysis                                            |
| `src/client.py`        | GitHub REST + GraphQL client                                            |
| `src/jira_client.py`   | Jira Cloud REST client                                                  |
| `src/utils.py`         | Period resolution, helpers                                              |
| `src/logging.py`       | Timestamped logging                                                     |

## Environment-Based Configuration

All configuration lives in `.env`. No config.yaml needed.

- `GITHUB_TOKEN` -> GitHub API auth + auto-detect username via `GET /user`
- `GITHUB_ORG` -> GitHub organization to scan for PRs
- `JIRA_URL` + `JIRA_TOKEN` -> Jira API authentication
- `JIRA_EMAIL` -> Jira account email for ticket queries
- `JIRA_PROJECT_KEY` -> **optional** Jira project key. Empty queries by person across all projects

AI analysis authenticates through Claude Code (no `ANTHROPIC_API_KEY`). Jira tickets are queried by person (assignee/reporter), not scoped to a project. "Reports to" is auto-detected from the lead of the user's most-active Jira project, so no env var is needed.
