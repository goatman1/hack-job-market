# hack-job-market

A local Codex skill and MCP server that finds LinkedIn hiring posts, verifies
whether their authors are US-based managers hiring for their own teams, matches
opportunities against local resume variants, and tracks results without repeatedly
showing the same leads. The initial query presets cover research-focused tech and
quant full-time roles suitable for junior candidates and new PhDs.

Codex performs semantic analysis and drafts outreach. Python handles browser
collection, validation, SQLite persistence, and Excel exports. No separate model
API key or hosted service is needed. Outreach is **draft only**; the server exposes
no messaging, connection-request, or application-submission operations.

## Setup

Requirements: Python 3.12–3.14, Git, a supported local browser environment, Codex,
and a LinkedIn account you can sign into manually. Run commands from this checkout.

Using `uv`:

```powershell
uv sync --extra dev
uv run patchright install chromium
uv run job-market init --resume-dir "../resumes"
uv run job-market configure-codex
```

Without `uv`, create a virtual environment and use its executables:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m patchright install chromium
.venv\Scripts\job-market init --resume-dir "../resumes"
.venv\Scripts\job-market configure-codex
```

`init` initializes local state and personal configuration. `configure-codex`
registers the local `job-market` stdio MCP in this project's `.codex/config.toml`,
using the current Python environment. It does not install a global server or
modify other projects. Open this repository as your Codex project, trust it if
prompted, and reload/reopen the project if the tools or skill are not yet visible.
The skill is stored in `.agents/skills/job-search/SKILL.md`.

Sign in once through the dedicated local browser profile:

```powershell
uv run job-market login
```

Complete login and any security checks yourself. Session state remains local.
LinkedIn prohibits third-party website automation; manual login and conservative
limits do not remove the possibility of an account restriction. The collector
stops on detected challenges and rate limits rather than bypassing them. See
[LinkedIn's automation policy](https://www.linkedin.com/help/linkedin/answer/a1341543).

## Run in Codex

Invoke the repository skill:

```text
$job-search Find 100 new tech and quant hiring posts from the past week,
sorted by relevance. Match against my resumes and update the workbook.
```

The coordinator collects posts and profile evidence through a single browser
owner. Up to three workers analyze separate batches of at most ten records. The
coordinator validates their results and writes the database. A worker never
controls the shared browser or sends messages.

The user-facing result is a local Excel workbook, backed by SQLite. The skill
reports its actual output path and run counts. It also reports shortfalls: 100
requested candidates do not guarantee 100 available posts or 100 qualified leads.

## Configuration

Copy or adapt `config.example.toml` into the ignored `config.local.toml`. Settings
include query strings and tracks, candidate count, sort order, date filter,
collection budgets, resume directory, shortlist threshold, and semantic checks.

| Setting | Default | Meaning |
|---|---|---|
| `target_count` | 100 | New unique candidate posts across both tracks |
| `sort_by` | `relevance` | `date_posted` requests latest first |
| `date_posted` | `past_week` | Also `past_24_hours`, `past_month`, `all_time` |
| `max_inspected` | 500 | Distinct posts inspected before stopping |
| `max_loading_actions` | 50 | Maximum page/scroll loading actions |
| `max_minutes` | 30 | Collection time budget |
| `no_progress_limit` | 3 | Consecutive loading actions with no new posts |
| `shortlist_threshold` | 60 | Minimum fit score for verified leads |

Semantic filter modes are `off`, `flag`, and `enforce`. Job location and title
relevance default to enforced; skills/experience, start date, and sponsorship
default to advisory flags. Set candidate availability and sponsorship truthfully
in local configuration. Missing information is unknown, not an invented match.

Core checks always cover active hiring, full-time employment, entry-level
suitability, the author's personal US location, management responsibility, current
company affiliation, and own-team hiring. Company headquarters are not evidence
of the author's location. A manager title alone does not prove hiring authority.
Uncertain evidence goes to Needs Review; confirmed mismatches are excluded.

Matching DOCX/PDF filenames represent one resume variant, with DOCX preferred.
Claims remain tied to their source. Scores are role alignment (30), skills (30),
experience (25), and education/seniority (15), not probabilities of an interview.
Shortlisted roles receive a supported 70–120 word LinkedIn DM draft.

## Tools and command line

| MCP operation | Purpose |
|---|---|
| `start_run` | Validate configuration and create or resume a run |
| `collect_posts` | Collect a bounded batch, returning IDs and continuation state |
| `get_author_evidence` | Retrieve profile/company evidence sequentially |
| `get_analysis_batch` | Retrieve up to ten records with relevant resume evidence |
| `save_assessments` | Validate and persist role-level checks, scores, and drafts |
| `list_leads` / `update_lead` | Review leads and maintain status/notes |
| `sync_workbook` | Import supported edits and export current workbook views |
| `get_run_status` | Report progress, stopping conditions, and errors |

Useful CLI commands:

```powershell
uv run job-market start --config config.local.toml --target-count 30
uv run job-market collect RUN_ID --batch-size 10
uv run job-market status RUN_ID
uv run job-market leads --bucket Shortlist
uv run job-market export
```

Replace `RUN_ID` with the identifier returned by `start`. Global `--data-dir`
overrides the default repository-local `.local` directory; place it before the
subcommand. `serve` starts the stdio MCP server for clients. CLI collection alone
does not perform semantic analysis: use the Codex skill for the full workflow.

## Tracking and workbook edits

Workbook tabs are Shortlist, Needs Review, Candidates, Excluded, and Runs.
Candidates is the complete audit view, so a lead can also appear in a more focused
tab. Stable lead IDs connect these views to one database record.

The leading columns are role, posted date, original content, poster name, poster
position, match score, and reach-out message. Additional columns retain source
links, verification evidence, selected resume, eligibility flags, tracking status,
and notes. Long posts retain their full text in local sidecar files rather than
being silently truncated to fit Excel.

Edit **status and notes** only to update tracking. Supported statuses are `new`,
`saved`, `contacted`, `applied`, `dismissed`, and `closed`. `sync_workbook` imports
supported changes before export. If the same lead is edited inconsistently in
different tabs, resolve that conflict rather than expecting a tab to take silent
precedence. Contacted/applied/dismissed/closed leads do not appear as new shortlist
recommendations. Editing scores or verification cells does not override evidence.

Canonical post IDs deduplicate repeated searches and runs; author/content
fingerprints provide a fallback. Edited posts update existing records. Confirmed
reposts of the same job requisition are grouped while retaining their sources.
Distinct roles within one post remain separate opportunities. Progress survives
interrupted runs. A locked workbook leaves database state intact and produces a
timestamped export instead of overwriting the open file.

## Validation and limits

```powershell
uv run pytest
uv run ruff check src tests
```

Automated tests use synthetic evidence and fixture collectors. Live LinkedIn
behavior depends on the authenticated UI and can change. A successful fixture
test does not establish that current live pagination or selectors work. Run the
30-candidate smoke test after login, confirm loading beyond the initial results
when available, and manually audit ten assessments. Keep the measured result in
local run records; do not claim a live check succeeded without observing it.

This is an on-demand research tool. It does not promise exhaustive LinkedIn
coverage, independently schedule runs, or guarantee that a matching role is open
or suitable. The workbook retains evidence so you can review those conclusions.

## Data and attribution

Do not commit resumes, browser profiles, cookies, personal configuration, collected
content, databases, or workbooks. The default `.local` directory and
`config.local.toml` are ignored. Keep runtime data in these ignored locations or
outside the checkout; do not copy it into source fixtures.

The LinkedIn adapter uses a pinned
[LinkedIn Research Agent MCP](https://github.com/raaaaaif/linkedin-research-agent-mcp)
dependency. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for attribution.
Our application code uses the repository's MIT license; dependencies retain their
own licenses.

## Planned alternate collector

A future Codex-browser workflow will use Codex's browser tools for discovery and
profile inspection while retaining this package's SQLite tracking, assessment
validation, and workbook exports. It is not implemented in v1. The current
collector uses its own Chromium profile through the pinned browser dependency.

## Validation snapshot — November 10, 2026

- 74 automated tests passed, including MCP protocol calls, URL aliases,
  partial collection recovery, evidence validation, and Excel edit round-tripping.
- A live authenticated run collected 30 unique posts and stopped at its target.
- Ten posts were manually audited into 11 role assessments: six excluded and five
  needing review. No reviewed role met every shortlist gate; the other 20 posts
  remain unassessed candidates. Repeating assessment submission added no duplicates.
- The actual project MCP startup command exposed all nine tools over stdio and
  successfully saved the audited assessments and exported the workbook.

This is a smoke test, not a measured guarantee of search recall or classification
accuracy. Open this checkout as a trusted Codex project to load its local skill
and MCP settings; they are deliberately not installed globally.
