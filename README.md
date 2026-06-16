# Self-Reporting

Performance self-assessment tool for developers. Collects your GitHub and Jira activity, generates metrics, and produces an AI-powered narrative analysis of your work patterns.

## Quick Start

```bash
# 1. Install dependencies
make install

# 2. Configure credentials
cp .env.example .env
# Edit .env with your tokens and settings

# 3. Generate your self-report
make report-2w           # Last 2 weeks
make report-monthly      # Current month
make report-last-month   # Last complete month
```

## Language

Reports are in English by default. Override per-run:

```bash
make report-2w LANG=es        # Latin American Spanish
make report-monthly LANG=en   # English
```

## Retry on Failure

If the AI analysis fails but data collection succeeded, re-run only the analysis:

```bash
make analyze-2w          # Re-analyze last 2 weeks
make analyze-monthly     # Re-analyze current month
make analyze-last-month  # Re-analyze last month
```

## Data Collection Only

To collect data without running AI analysis:

```bash
make collect-2w
make collect-monthly
make collect-last-month
```

## Configuration

All configuration lives in `.env`:

```text
GITHUB_TOKEN=ghp_xxx
GITHUB_ORG=your-org
JIRA_URL=https://company.atlassian.net
JIRA_EMAIL=you@company.com
JIRA_TOKEN=your_jira_api_token
JIRA_PROJECT_KEY=PROJ
ANTHROPIC_API_KEY=sk-ant-xxx
```

Your GitHub username is detected automatically from the token. No need to configure it manually.

## Output

Reports use the naming pattern `self-report-<period>-<date>.md` and overwrite if regenerated on the same day.

| File                                   | Content                       |
| -------------------------------------- | ----------------------------- |
| `output/data/data-self-report-*.json`  | Raw metrics data              |
| `output/data/metrics-self-report-*.md` | Script-generated tables       |
| `output/self-report-*.md`              | Final report with AI analysis |

## Requirements

- Python 3.11+
- GitHub personal access token (scope: `repo`)
- Jira API token (optional, for Jira integration)
- Anthropic API key (for AI analysis; not needed in Claude Code)
