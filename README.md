# Self-Reporting

Generate an honest self-assessment of your own engineering work. It pulls your GitHub and Jira activity for a period, builds the metrics, and has Claude write the narrative analysis (PR quality, how you handle review feedback, your reviewing, code-quality patterns, Jira hygiene).

It runs on one person: you. The GitHub user is detected from your token, and Jira is queried by you as assignee and reporter.

## Requirements

Run this from [Claude Code](https://www.anthropic.com/claude-code). It authenticates the AI analysis for you and runs it on Opus, so there is no API key to manage and running it elsewhere is not supported.

You also need Python 3.11+, a GitHub token (scope `repo`), and optionally a Jira API token.

## Setup

```bash
make install                 # create the venv and install dependencies
cp .env.example .env         # then fill in your tokens (see Configuration)
```

## Usage

Each `report-*` command runs the full pipeline (collect, analyze, render) and writes both a Markdown report and a styled HTML view.

```bash
make report-2w               # last 2 weeks
make report-monthly          # current month
make report-last-month       # last full month
make report-yearly           # year to date
make report-historic         # all time
make report-custom START=2026-01-01 END=2026-03-31
```

By default the report is in English. Add `REPORT_LANG=es` for Latin American Spanish:

```bash
make report-2w REPORT_LANG=es
```

## Recovery commands

Collection and analysis are split, so a failed analysis never forces you to re-pull the data. Each verb takes the same periods as `report-*` (`-2w`, `-monthly`, `-last-month`, `-yearly`, `-historic`, `-custom`).

```bash
make collect-2w              # pull GitHub + Jira data only, no analysis
make analyze-2w              # re-run the analysis on already-collected data
make html-2w                 # re-render the existing report to HTML
```

If an analysis fails, fix the cause and re-run `make analyze-<period>`; the collected data is reused.

## Configuration

Everything lives in `.env`:

```text
GITHUB_TOKEN=ghp_xxx
GITHUB_ORG=your-org
JIRA_URL=https://company.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=           # optional; empty queries every project you touch
```

Notes:

- No Anthropic API key. Claude Code handles that.
- Jira is queried by person, not by project. Leave `JIRA_PROJECT_KEY` empty to span every project where you have tickets, or set it to scope to one.
- "Reports to" is detected automatically from the lead of your most active Jira project.

## Output

Files land in `output/`, named `self-report-<period>-<date>`, and are overwritten when regenerated the same day.

| File                                    | Content                            |
| --------------------------------------- | ---------------------------------- |
| `output/data/data-self-report-*.json`   | Raw collected metrics              |
| `output/data/metrics-self-report-*.md`  | Script-generated tables            |
| `output/self-report-*.md`               | Final report with the AI analysis  |
| `output/self-report-*.html`             | Styled HTML view with charts       |

## Housekeeping

```bash
make clean                   # delete output/ and logs/
make uninstall               # remove the venv
```

On Windows, use `run.bat <command>` (for example `run report-2w`) instead of `make`.
