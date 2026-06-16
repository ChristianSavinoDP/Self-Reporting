# Self-Reporting — Claude Instructions

## What This Is

A self-assessment tool that generates performance reports for a single developer based on their GitHub and Jira activity. The developer runs it on themselves to get an objective view of their work patterns.

## Commands

| Command                  | Description                                             |
| ------------------------ | ------------------------------------------------------- |
| `make report-2w`         | Full report: last 2 weeks (GitHub + Jira + AI analysis) |
| `make report-monthly`    | Full report: current month                              |
| `make report-last-month` | Full report: last complete month                        |
| `make collect-*`         | Data collection only (no AI analysis)                   |
| `make analyze-*`         | Re-run AI analysis on existing data (retry-friendly)    |

Language override: `make report-2w LANG=es`

## Interaction Flow

When the user starts a conversation (greeting, generic message, or asks for help), use `AskUserQuestion` with `multiSelect: false` to present a menu:

| Label                       | Description                                         |
| --------------------------- | --------------------------------------------------- |
| Self-Report (2 weeks)       | Generate your self-assessment for the last 2 weeks  |
| Self-Report (current month) | Generate your self-assessment for the current month |
| Self-Report (last month)    | Generate your self-assessment for last month        |
| Clean reports and logs      | Delete generated outputs and logs                   |

After selection:

- If **Self-Report** (any period) -> ask for language with `AskUserQuestion`:

  | Label   | Description                      |
  | ------- | -------------------------------- |
  | English | Report in English (default)      |
  | Spanish | Report in Latin American Spanish |

  Then run the corresponding `make report-*` command with the `LANG` flag if applicable.

- If **Clean reports and logs** -> run `make clean`

## ANTHROPIC_API_KEY Requirement

Before running any `make report-*` command, check if `ANTHROPIC_API_KEY` is set in `.env`.

- If the key **is not set** and you are running inside **Claude Code**: the Anthropic SDK authenticates automatically, no key is needed. Proceed normally.
- If the key **is not set** and you are **NOT** running inside Claude Code: **stop the process**. Tell the user they need to either:
  1. Set `ANTHROPIC_API_KEY` in `.env` (get one from [Anthropic Console](https://console.anthropic.com/api-keys))
  2. Run from Claude Code, which authenticates automatically

Do NOT proceed with `make report-*` if the analysis step will fail due to missing authentication.

## After Running a Command

1. Read the log file in `logs/`
2. If the log contains `Error` -> report it and ask if the user wants to retry
3. If no errors -> show the output file path as a clickable markdown link: `[filename.md](file:///absolute/path/to/file.md)`. Do NOT use backticks or plain text for paths.

## Retry Logic

If the AI analysis fails (step 2/2), the data collection (step 1/2) does not need to repeat. Use `make analyze-*` to re-run only the analysis. Always suggest this when analysis fails.

The `collect-*` and `analyze-*` commands exist as recovery tools. They are not shown in the main menu; only suggest them when a failure occurs.

## Project Structure

| File                  | Purpose                                                             |
| --------------------- | ------------------------------------------------------------------- |
| `main.py`             | Unified entry point with `report`, `collect`, `analyze` subcommands |
| `.env`                | All configuration: tokens, org, Jira project key                    |
| `prompt.md`           | Analysis instructions for Claude                                    |
| `src/metrics.py`      | GitHub API data collection                                          |
| `src/jira_metrics.py` | Jira API data collection                                            |
| `src/renderer.py`     | Markdown report generation (tables)                                 |
| `src/analyze.py`      | Claude AI narrative analysis                                        |
| `src/client.py`       | GitHub REST + GraphQL client                                        |
| `src/jira_client.py`  | Jira Cloud REST client                                              |
| `src/utils.py`        | Period resolution, helpers                                          |
| `src/logging.py`      | Timestamped logging                                                 |

## Environment-Based Configuration

All configuration lives in `.env`. No config.yaml needed.

- `GITHUB_TOKEN` -> GitHub API auth + auto-detect username via `GET /user`
- `GITHUB_ORG` -> GitHub organization to scan for PRs
- `JIRA_URL` + `JIRA_TOKEN` -> Jira API authentication
- `JIRA_EMAIL` -> Jira account email for ticket queries
- `JIRA_PROJECT_KEY` -> Jira project key (e.g. DBI)
- `ANTHROPIC_API_KEY` -> Claude API key (not needed in Claude Code)
