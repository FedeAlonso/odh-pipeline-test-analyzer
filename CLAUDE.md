# Dashboard Build Analyzer

Automated analysis tool for RHOAI/ODH Cypress E2E nightly test builds from Jenkins. Fetches test results, reruns flaky tests, inspects OpenShift cluster health, correlates failures with Jira issues, and generates detailed HTML/Markdown reports.

## Quick Commands

```bash
# Run analysis
venv/bin/python scripts/comprehensive_analysis.py <build_number|latest> [odh|rhoai] [options]

# Options:
#   -y, --yes                Auto-accept prompts (also auto-detected when stdin is not a TTY)
#   --skip-rerun               Skip test reruns
#   --skip-slack               Skip Slack message posting
#   --skip-jira              Skip Jira lock ticket and result publishing
#   --no-artifacts-download  Skip downloading screenshots/videos from Jenkins
#   --enable-trend           Enable trend analysis (for nightly automation)

# Examples
venv/bin/python scripts/comprehensive_analysis.py latest rhoai
venv/bin/python scripts/comprehensive_analysis.py 3695 odh
venv/bin/python scripts/comprehensive_analysis.py 492 rhoai --skip-rerun --skip-jira
venv/bin/python scripts/comprehensive_analysis.py latest rhoai -y   # non-interactive

# Install dependencies
venv/bin/pip install -r requirements.txt
```

There is no test suite. Verify changes by running the analyzer against a real build and reviewing the generated report.

## Architecture

### Entry Points
- `scripts/comprehensive_analysis.py` — Primary 11-step analysis pipeline (~3700 lines)
- `scripts/analyze_job.py` — Generic Jenkins job analyzer (simpler, works with any job)
- `scripts/nightly_analyzer.py` — Scheduled orchestrator (cron-like)
- `mcp/server.py` — FastMCP server exposing Jenkins tools for AI agents

### Core Modules (`analyzer/`)
| Module | Purpose |
|--------|---------|
| `config.py` | Env var configuration, `.env` loader |
| `jenkins_client.py` | Async Jenkins HTTP API wrapper (httpx) |
| `artifact_parser.py` | JUnit XML, Cypress JSON, console log parsing |
| `cluster_inspector.py` | Read-only OpenShift cluster inspection via `oc` CLI |
| `failure_analyzer.py` | Failure categorization, recommendations, test reruns |
| `jira_client.py` | Jira search (Atlassian Cloud, API v3, Basic Auth) |
| `jira_lock.py` | Jira lock tickets to prevent duplicate analysis runs |
| `jira_search_patterns.py` | 30+ pattern definitions mapping test names to Jira queries |
| `report_generator.py` | Markdown/HTML report generation |
| `slack_helper.py` | Slack message composition, failure classification (repeated vs new), Jenkins Bot thread parsing |

### External Services
- **Jenkins** — `JENKINS_URL` + `JENKINS_USER`/`JENKINS_TOKEN` (Basic Auth)
- **Jira** — `JIRA_URL` + `JIRA_USER`/`JIRA_TOKEN` (Atlassian Cloud, Basic Auth, API v3)
- **OpenShift** — `RHOAI_API_SERVER`/`ODH_API_SERVER` + credentials (read-only `oc` CLI)
- **Test Variables** — `RHOAI_TEST_VARIABLES`/`ODH_TEST_VARIABLES` (absolute path to `test-variables.yml` per cluster). Falls back to `<frontend_repo>/packages/cypress/test-variables.yml` if not set.
- **GitLab** — `GITLAB_URL` + `GITLAB_TOKEN` (commit tracking)
- **Tracer** — `TRACER_PATH` (optional, image metadata extraction)
- **Slack** — [redhat-community-ai-tools/slack-mcp](https://github.com/redhat-community-ai-tools/slack-mcp) MCP server. Read channel history and send analysis summaries.

## Code Conventions

- **Async throughout**: all clients use `async/await` with `httpx.AsyncClient`. Entry point is `asyncio.run(main())`.
- **Graceful degradation**: missing credentials (Jira, GitLab, cluster, tracer) must never block the analysis pipeline. Wrap external calls in try/except, print a warning, continue.
- **Config via env vars**: all secrets and URLs in `analyzer/config.py` via `os.getenv()`. Never hardcode credentials.
- **Cluster safety**: `cluster_inspector.py` validates `oc` commands against a whitelist (get, describe, logs only). Never add write operations.
- **Reports**: generated as self-contained HTML with base64-embedded images. Saved to `reports/current/{RHOAI|ODH}/` and `reports/historical/`.
- **No formal tests**: verify manually by running against a real Jenkins build number.

## Style

- snake_case for functions/variables, PascalCase for classes, UPPER_SNAKE_CASE for constants
- Dataclasses for structured data (`TestFailure`, `TestResult`, `ClusterConfig`)
- Minimal comments — only for non-obvious logic
- Print progress with emoji prefixes during analysis steps (e.g., `[1/11] 📥 Fetching...`)

## Key Patterns

### Jenkins job path format
`"components/dashboard/dashboard-e2e-tests"` — split on `/` and join with `/job/` for API URLs.

### Build variant detection
ODH vs RHOAI is determined by the build description containing `dash-e2e-odh` or `dash-e2e-rhoai`. The `variant` CLI arg selects which to look for.

### Failure categories
`timeout`, `assertion`, `element_not_found`, `network`, `auth`, `resource`, `unknown` — defined in `failure_analyzer.py:categorize_failure()`.

### Pipeline stages
Classified as `infra` (cluster setup), `test` (Cypress execution), or `post-build` (cleanup). Stage lists are constants in `comprehensive_analysis.py`.

### Test rerun strategy
When >5 failures, groups by exception type and reruns one per group. Otherwise reruns all. Results distinguish "passed on retry" (flaky) vs "failed on retry" (real bug).

Reruns are **deterministic**: they checkout the exact downstream commit (`red-hat-data-services/odh-dashboard`) used by the nightly build, extracted from the fbc_fragment tracer metadata. The downstream repo is added as a git remote (`downstream`) in the local `odh-dashboard` checkout and fetched on demand. This ensures the test code matches what the nightly actually ran, not whatever is on `main` at analysis time. The test-variables config is read from the frontend repo at `packages/cypress/test-variables.yml`.

## Agent Workflow

When asked to run a nightly analysis, follow these steps in order:

### 1. Determine build and platform
Ask the user for the build number (or use `latest`) and platform (`rhoai` or `odh`). If not specified, default to `latest rhoai`.

### 2. Run the analysis
```bash
venv/bin/python scripts/comprehensive_analysis.py <build_number|latest> <odh|rhoai> -y
```
Always use `-y` (auto-accept prompts) to avoid blocking on interactive input. Always download artifacts (do NOT use `--no-artifacts-download`). The HTML report embeds screenshots and videos from test failures, which are essential for debugging.
The script handles:
- **Jira lock check** — creates a lock ticket (`Nightly Analysis: {build}-{platform}-{date}`) in RHOAIENG to prevent duplicate runs. If the ticket already exists, prompts the user.
- **11-step analysis** — fetches build data, parses test results, inspects cluster, searches Jira, reruns failing tests, downloads artifacts.
- **Report generation** — saves markdown + HTML reports locally.
- **Jira publish** — posts a summary comment and attaches the .md and .html reports to the lock ticket.

### 3. Deliver the outputs to the user
Once the analysis completes, three files are generated:

| Output | Path | Purpose |
|--------|------|---------|
| HTML report | `reports/current/{RHOAI\|ODH}/latest-build-{N}.html` | Self-contained report with embedded screenshots/videos. **Primary deliverable** — open it for the user. |
| Markdown report | `reports/current/{RHOAI\|ODH}/latest-build-{N}.md` | Same content in markdown. Share the full content with the user. |
| Historical copy | `reports/historical/{date}-{variant}-build-{N}-v2.md` | Timestamped archive copy. No action needed. |

The script also automatically publishes to Jira (if a lock ticket was created):
- A structured summary comment with test counts, failures, flaky tests, and pipeline errors
- The .md and .html report files as attachments

**After the script finishes:**
1. **Open the HTML report** for the user: `open reports/current/{RHOAI|ODH}/latest-build-{N}.html`
2. **Read and present the Markdown report** content so the user can see the results directly in the conversation
3. **Summarize the console output** — highlight any warnings, lock ticket creation, pipeline failures, or test rerun results
4. **Share the Jira ticket link** so the user can access the published results

### 4. Analyze and recommend
After presenting the report:
- Highlight the most critical failures and whether they are flaky (passed on retry) or real bugs
- Cross-reference with Jira issues found by the analyzer
- If there are infrastructure/deployment failures, call those out first — they may have prevented tests from running
- Consult the architecture-context repo if component relationships are relevant to understanding a failure
- Consult the odh-dashboard repo to look at the actual test code for any confusing failures

### 5. Post agent analysis summary to Jira
After analyzing the results, post a structured **agent analysis summary** as a comment on the lock ticket using `scripts/post_analysis_summaries.py jira`. Pass real failures as `name:error:jira_key` comma-separated, cluster pods as `total:running:failed`, and pipeline failure as `step:exception`. Use `--extra-notes` for key observations from the analysis.

```bash
venv/bin/python scripts/post_analysis_summaries.py jira \
    --ticket RHOAIENG-59395 --build 366 --platform RHOAI \
    --total 120 --passed 112 --failed 8 \
    --real-failures 'pipelines:DSPA timeout:RHOAIENG-58177,testPerformanceFiltersAvailable:timeout:RHOAIENG-58910' \
    --flaky 'testRayJobProjectAccessPermissions,testProjectAccessPermissions' \
    --cluster-pods 17:17:0 \
    --pipeline-failure 'dashboardPostBuild:NullPointerException' \
    --extra-notes 'Pipeline tests are the top concern — 3 of 8 failures.'
```

### 6. Post analysis summary to Slack (threaded on Jenkins Bot message)
After posting to Jira, post a threaded reply on the matching "RHOAI Jenkins Bot" message in `#team-openshift-ai-dashboard` using the [redhat-community-ai-tools/slack-mcp](https://github.com/redhat-community-ai-tools/slack-mcp) MCP tools and the `analyzer.slack_helper` module. If Slack MCP is not configured, skip silently.

#### 6a. Gather data via MCP tools and enrich context
1. Use `mcp__slack__search_messages` with query `from:"RHOAI Jenkins Bot" in:team-openshift-ai-dashboard` (limit ~10)
2. Use `slack_helper.build_historical_context(search_results, build_number, platform)` to identify the 5 previous builds' `thread_ts` values
3. Use `mcp__slack__get_thread` on each `thread_ts` (including the current build's) to fetch thread replies
4. **Enrich with external context** — for each repeated failure, actively investigate the current status:
   - **Jira tickets**: When a thread references a Jira ticket (e.g., RHOAIENG-58177), fetch the ticket via the Jira API to check its current status, read the latest comments, and determine if a fix has been merged or is still in progress. Include the ticket status in the Slack message.
   - **Slack cross-references**: When thread messages link to other Slack threads or channels, follow those links using `mcp__slack__get_thread` to gather additional context (e.g., DevOps discussions, build notification threads).
   - **PR status**: When GitHub PRs are referenced, note whether they are open, merged, or closed. If a fix PR was merged, check if it was backported to the relevant branch.
   - The goal is to provide a complete, up-to-date picture of each failure — not just echo what was said in previous threads, but report the *current state* of each investigation.

#### 6b. Save MCP data and generate the Slack message
Save the `search_results` and `thread_data` collected via MCP tools to a JSON file, then run the script to generate the message. The script automatically fetches Jira statuses for all referenced tickets and enriches the output.

```bash
# Save MCP data to JSON (search_results + thread_data collected in step 6a)
# Then generate the Slack message:
venv/bin/python scripts/post_analysis_summaries.py slack \
    --data /tmp/slack_data.json --build 366 --platform RHOAI \
    --total 120 --passed 112 --failed 8 \
    --real-failures 'pipelines,testPerformanceFiltersAvailable,testSchedulePipeline,...' \
    --flaky 'testRayJobProjectAccessPermissions,testProjectAccessPermissions,...' \
    --rerun-passed 'testFoo,testBar' \
    --rerun-failed 'testGenAi,testDeployOCIModel' \
    --ticket RHOAIENG-59395 \
    --ticket-url https://redhat.atlassian.net/browse/RHOAIENG-59395 \
    --cluster-pods 17:17:0 \
    --pipeline-failure 'dashboardPostBuild:NullPointerException' \
    --output /tmp/slack_message.json
```

The output JSON contains `{"thread_ts": "...", "message": "..."}`. Post using `mcp__slack__post_message` with the `thread_ts` and `message` values.

The generated message already starts with the required disclaimer: `*NOTE: _This is an Agentic-AI generated message. This feature is still WIP_*`. Do not remove or modify this line.

#### Slack MCP tool reference
| Tool | Purpose |
|------|---------|
| `mcp__slack__search_messages` | Search for Jenkins Bot messages |
| `mcp__slack__get_thread` | Fetch thread replies for historical context |
| `mcp__slack__post_message` | Post threaded reply (use `thread_ts` param) |
| `mcp__slack__get_channel_history` | Browse channel history with date filters |
| `mcp__slack__send_dm` | Send DMs if needed |

## Context Repositories

When analyzing failures or understanding component behavior, consult these external repos:

- **[opendatahub-io/architecture-context](https://github.com/opendatahub-io/architecture-context)** — Auto-generated architecture documentation for RHOAI/ODH platforms. Contains per-component architecture summaries (`architecture/rhoai-3.4/*.md`), a synthesized platform overview (`PLATFORM.md`), component dependency graphs, network topology, RBAC requirements, and Mermaid diagrams. Use this to understand how components relate, what namespaces they deploy to, and what integration points exist when investigating test failures.
- **[opendatahub-io/odh-dashboard](https://github.com/opendatahub-io/odh-dashboard)** — The dashboard frontend being tested. Contains the Cypress E2E tests under `frontend/src/__tests__/cypress/`, React components, API routes, and test fixtures. Reference this repo to understand test code, locate spec files (`*.cy.ts`), check recent commits that may have caused failures, and trace UI components involved in test assertions.

## Skill Repositories

- **[antowaddle/Red-Hat-Quality-Tiger-Team](https://github.com/antowaddle/Red-Hat-Quality-Tiger-Team)** — Quality engineering skills and shared utilities. The key directory is [`.claude/skills/shared/`](https://github.com/antowaddle/Red-Hat-Quality-Tiger-Team/tree/main/.claude/skills/shared) which provides Jira integration utilities:
  - `jira_utils.py` — Jira Cloud API v3 operations: `create_issue`, `get_issue`, `add_comment`, `add_labels`, `remove_labels`, `update_issue`, markdown-to-ADF conversion. Uses Basic Auth (`JIRA_SERVER`/`JIRA_USER`/`JIRA_TOKEN`).
  - `fingerprint_utils.py` — Skill execution tracking via Jira tickets: `record_skill_execution`, `find_or_create_tracking_issue`, `search_issues`. Creates per-repo tracking issues with labels and rich comments.

## Don't

- Don't commit or push unless explicitly asked
- Don't add write operations to `cluster_inspector.py`
- Don't hardcode Jenkins/Jira/cluster URLs or credentials
- Don't break the graceful degradation pattern — every external service must be optional
- Don't add dependencies without updating `requirements.txt`
