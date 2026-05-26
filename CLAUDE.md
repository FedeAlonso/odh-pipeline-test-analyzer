# Dashboard Build Analyzer

Automated analysis tool for RHOAI/ODH Cypress E2E nightly test builds from Jenkins. Fetches test results, reruns flaky tests, inspects OpenShift cluster health, correlates failures with Jira issues, and generates detailed HTML/Markdown reports.

## Quick Commands

```bash
# Run analysis
venv/bin/python scripts/comprehensive_analysis.py <build_number|latest> [odh|rhoai] [--enable-trend] [--no-artifacts-download]

# Examples
venv/bin/python scripts/comprehensive_analysis.py latest rhoai
venv/bin/python scripts/comprehensive_analysis.py 3695 odh

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
- **OpenShift** — `RHOAI_API_SERVER`/`ODH_API_SERVER` + credentials (read-only `oc` CLI). Also accessible via the Kubernetes MCP server (see below).
- **GitLab** — `GITLAB_URL` + `GITLAB_TOKEN` (commit tracking)
- **Tracer** — `TRACER_PATH` (optional, image metadata extraction)
- **Slack** — [redhat-community-ai-tools/slack-mcp](https://github.com/redhat-community-ai-tools/slack-mcp) MCP server. Read channel history and send analysis summaries.
- **Kubernetes/OpenShift** — [kubernetes-mcp-server](https://github.com/openshift/openshift-mcp-server) MCP server (full read-write). Provides direct access to pods, logs, events, namespaces, resource metrics, and OpenShift projects without needing `oc login`. Also supports write operations: create/update/delete resources, exec into pods, scale deployments. Uses `~/.kube/config`.

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

## Agent Workflow

When asked to run a nightly analysis, follow these steps in order:

### 1. Determine build and platform
Ask the user for the build number (or use `latest`) and platform (`rhoai` or `odh`). If not specified, default to `latest rhoai`.

### 2. Run the analysis
```bash
venv/bin/python scripts/comprehensive_analysis.py <build_number|latest> <odh|rhoai>
```
Always download artifacts (do NOT use `--no-artifacts-download`). The HTML report embeds screenshots and videos from test failures, which are essential for debugging.
The script handles:
- **Jira lock check** — creates a lock ticket (`Nightly Analysis: {build}-{platform}-{date}`) in RHOAIENG to prevent duplicate runs. If the ticket already exists, prompts the user.
- **11-step analysis** — fetches build data, parses test results, inspects cluster, searches Jira, reruns failing tests, downloads artifacts.
- **Report generation** — saves markdown + HTML reports locally.
- **Jira publish** — posts a summary comment and attaches the .md and .html reports to the lock ticket.

Monitor the console output for prompts that need user input (e.g., old build warnings, variant mismatches, lock ticket conflicts).

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

### 4b. Deep analysis of uninvestigated failures
After the initial analysis, check each real failure (not flaky) for existing investigation context: a known Jira bug, a prior deep analysis in a previous thread, or a clear root cause from the automated report. For any failure that **lacks** this context, automatically perform a deeper investigation:
- Read the test source code in odh-dashboard to understand what it does and where it fails
- Search for recent PRs that touch the relevant code paths (model serving, model registry, MLflow, etc.)
- Check cluster state via K8s MCP tools (pod health, events, resource pressure, stuck resources)
- Search Jira for related tickets
- Look at screenshots/videos from the failure artifacts

**Only do this if fewer than 15 tests need deep investigation.** If 15+, post a summary and ask the user which failures to prioritize.

Post results as **one Slack message per failure cluster**, threaded on the Jenkins Bot message. Group tests that share the same root cause into a single message (e.g., 8 model serving tests all broken by the same PR, 3 MLflow tests caused by the same DSC config issue). Each message should be self-contained with full context.

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

#### Kubernetes MCP tool reference
See the full tool table in the [Cluster Investigation](#cluster-investigation) section. Key tools for nightly analysis:
- `mcp__kubernetes-mcp-server__pods_list_in_namespace` — check pod health in `redhat-ods-operator` / `redhat-ods-applications`
- `mcp__kubernetes-mcp-server__pods_log` — fetch operator/component logs
- `mcp__kubernetes-mcp-server__events_list` — find warnings and errors
- `mcp__kubernetes-mcp-server__resources_get` — inspect CSVs, DSCIs, DSCs, and custom resources

## Cluster Investigation

When asked to investigate cluster issues (stuck namespaces, operator failures, deployment problems), **prefer the Kubernetes MCP tools** over `oc` CLI for read-only operations. The MCP server connects via `~/.kube/config` and avoids the need to `oc login` each time. Fall back to `oc` CLI only when MCP tools don't cover the operation (e.g., `oc exec`, `oc patch`, custom resource queries with complex JSONPath).

### Kubernetes MCP tool reference

| Tool | Purpose | Replaces |
|------|---------|----------|
| `mcp__kubernetes-mcp-server__pods_list` | List pods across all namespaces | `oc get pods -A` |
| `mcp__kubernetes-mcp-server__pods_list_in_namespace` | List pods in a specific namespace | `oc get pods -n <ns>` |
| `mcp__kubernetes-mcp-server__pods_get` | Get pod details (status, containers, conditions) | `oc get pod <name> -o json` |
| `mcp__kubernetes-mcp-server__pods_log` | Get pod logs (with container/tail/previous options) | `oc logs <pod>` |
| `mcp__kubernetes-mcp-server__pods_top` | Pod CPU/memory metrics | `oc adm top pods` |
| `mcp__kubernetes-mcp-server__nodes_top` | Node CPU/memory metrics | `oc adm top nodes` |
| `mcp__kubernetes-mcp-server__nodes_log` | Node system logs (kubelet, kube-proxy) | SSH + journalctl |
| `mcp__kubernetes-mcp-server__nodes_stats_summary` | Detailed node stats (CPU, memory, filesystem, PSI) | `oc describe node` |
| `mcp__kubernetes-mcp-server__events_list` | Cluster events (warnings, errors) | `oc get events -A` |
| `mcp__kubernetes-mcp-server__namespaces_list` | List namespaces | `oc get namespaces` |
| `mcp__kubernetes-mcp-server__projects_list` | List OpenShift projects | `oc get projects` |
| `mcp__kubernetes-mcp-server__resources_get` | Get any resource by apiVersion/kind/name | `oc get <resource> <name> -o json` |
| `mcp__kubernetes-mcp-server__resources_list` | List any resource type with label/field selectors | `oc get <resource> -A` |
| `mcp__kubernetes-mcp-server__resources_create_or_update` | Create or update any resource from YAML/JSON | `oc apply -f` |
| `mcp__kubernetes-mcp-server__resources_delete` | Delete any resource | `oc delete <resource> <name>` |
| `mcp__kubernetes-mcp-server__resources_scale` | Get or update replica count | `oc scale deployment` |
| `mcp__kubernetes-mcp-server__pods_delete` | Delete a pod | `oc delete pod <name>` |
| `mcp__kubernetes-mcp-server__pods_exec` | Execute commands in a pod | `oc exec <pod> -- <cmd>` |
| `mcp__kubernetes-mcp-server__pods_run` | Run a container image as a pod | `oc run` |
| `mcp__kubernetes-mcp-server__configuration_contexts_list` | List kubeconfig contexts | `oc config get-contexts` |

Use the `context` parameter to target a specific cluster without switching contexts. Use `labelSelector` and `fieldSelector` parameters to filter results.

**Write operations** (create, update, delete, scale, exec) are available but should be used with caution. Always confirm destructive operations with the user before executing.

### Login to clusters

Use `oc login` only when MCP tools are unavailable or when write operations are needed. The MCP server reads from `~/.kube/config` which already has contexts for all clusters after login.

```bash
# RHOAI cluster
source .env && oc login "$RHOAI_API_SERVER" -u "$RHOAI_USERNAME" -p "$RHOAI_PASSWORD" --insecure-skip-tls-verify

# ODH cluster
source .env && oc login "$ODH_API_SERVER" -u "$ODH_USERNAME" -p "$ODH_PASSWORD" --insecure-skip-tls-verify
```

### Namespace stuck in Terminating

```python
# Via MCP tools (preferred for steps 1-3):
# 1. Find stuck namespaces — list all projects and check for Terminating phase
mcp__kubernetes-mcp-server__projects_list()

# 2. Check conditions and finalizers on a specific namespace
mcp__kubernetes-mcp-server__resources_get(apiVersion="v1", kind="Namespace", name="<namespace>")

# 3. Find resources with stuck finalizers
mcp__kubernetes-mcp-server__resources_list(apiVersion="apps/v1", kind="Deployment", namespace="<namespace>")

# 4. Check events for clues
mcp__kubernetes-mcp-server__events_list(namespace="<namespace>")
```

```python
# Via MCP tools (for write operations — confirm with user first):
# 4. Fix: delete stuck pod to force reschedule
mcp__kubernetes-mcp-server__pods_delete(name="<pod>", namespace="<namespace>")

# 4b. Or remove stuck finalizer by updating the resource
mcp__kubernetes-mcp-server__resources_create_or_update(...)  # patch the resource with finalizers: null
```

```bash
# Via oc CLI (fallback for patches):
oc patch deployment <name> -n <namespace> -p '{"metadata":{"finalizers":null}}' --type=merge
```

### Operator health check (RHOAI/ODH)

Prefer MCP tools for the read-only checks. Fall back to `oc` for operations MCP doesn't cover (exec, complex JSONPath).

```python
# Via MCP tools (preferred):
# Check operator CSV and version
mcp__kubernetes-mcp-server__resources_list(apiVersion="operators.coreos.com/v1alpha1", kind="ClusterServiceVersion", namespace="redhat-ods-operator")

# Check operator pods
mcp__kubernetes-mcp-server__pods_list_in_namespace(namespace="redhat-ods-operator")

# Check DSCI and DSC status
mcp__kubernetes-mcp-server__resources_list(apiVersion="dscinitialization.opendatahub.io/v1", kind="DSCInitialization")
mcp__kubernetes-mcp-server__resources_list(apiVersion="datasciencecluster.opendatahub.io/v1", kind="DataScienceCluster")

# Check operator logs
mcp__kubernetes-mcp-server__pods_log(name="<operator-pod>", namespace="redhat-ods-operator", tail=50)

# Check pods in applications namespace
mcp__kubernetes-mcp-server__pods_list_in_namespace(namespace="redhat-ods-applications")

# Check events for errors
mcp__kubernetes-mcp-server__events_list(namespace="redhat-ods-operator")

# Check any custom resource
mcp__kubernetes-mcp-server__resources_get(apiVersion="services.platform.opendatahub.io/v1alpha1", kind="Auth", name="<name>", namespace="redhat-ods-operator")
```

```bash
# Via oc CLI (fallback — for exec and complex queries):
# Check operator image and verify manifest contents
oc get csv <csv-name> -n redhat-ods-operator -o jsonpath='{.spec.install.spec.deployments[0].spec.template.spec.containers[0].image}'
oc exec deployment/rhods-operator -n redhat-ods-operator -- ls /opt/manifests/
```

### Build failure diagnosis

When a build fails (especially at infra stages like "Deploy RHOAI operator"), follow this diagnostic sequence:

```bash
# 1. Fetch build info and identify the failure stage
source .env && curl -s -u "$JENKINS_USER:$JENKINS_TOKEN" \
    "$JENKINS_URL/job/components/job/dashboard/job/dashboard-e2e-tests/<build>/api/json?tree=result,description,timestamp,duration,building"

# 2. Get console output and find the error
source .env && curl -s -u "$JENKINS_USER:$JENKINS_TOKEN" \
    "$JENKINS_URL/job/components/job/dashboard/job/dashboard-e2e-tests/<build>/consoleText" > /tmp/console_<build>.txt

# 3. Identify which cluster/product
grep -E '(PRODUCT|CLUSTER_NAME)' /tmp/console_<build>.txt | head -3

# 4. List pipeline stages
grep -E '\{ \(' /tmp/console_<build>.txt

# 5. Find the failure
grep -B5 -A10 'FAIL\|ERROR\|failed after retrying\|skipped due to earlier' /tmp/console_<build>.txt | head -40

# 6. Login to the affected cluster and check state
source .env && oc login "$RHOAI_API_SERVER" -u "$RHOAI_USERNAME" -p "$RHOAI_PASSWORD" --insecure-skip-tls-verify
# or for ODH:
source .env && oc login "$ODH_API_SERVER" -u "$ODH_USERNAME" -p "$ODH_PASSWORD" --insecure-skip-tls-verify
```

### Known operator issues and fixes

**Segment.io manifests missing from operator image (`/opt/manifests/segment`)**
- Symptom: DSCI stuck in `Progressing`/`ReconcileInit`, Auth CR never created, build fails with "Auth does not exist"
- Operator logs show: `lstat /opt/manifests/segment: no such file or directory`
- Root cause: Operator image doesn't include segment manifests despite code expecting them (PR #3420 removed, PR #3519 re-added, but image wasn't rebuilt)
- Fix: Create the resources manually, then restart the operator:
```bash
oc create configmap odh-segment-key-config --from-literal=segmentKeyEnabled="true" -n redhat-ods-applications
oc create secret generic odh-segment-key --from-literal=segmentKey="$(echo 'S1JVaG9CSUVwV2xHdXo0c1dpeGFlMXZBWEtLR2xENUs=' | base64 -d)" -n redhat-ods-applications
oc rollout restart deployment/rhods-operator -n redhat-ods-operator
oc rollout status deployment/rhods-operator -n redhat-ods-operator --timeout=120s
# Verify:
sleep 30 && oc get dsci default-dsci -o jsonpath='{.status.conditions[?(@.type=="ReconcileComplete")].status}'
# Should return "True"
oc get auths.services.platform.opendatahub.io -A
# Should show auth with READY=True
```
- Note: This fix is wiped every time the cleanup stage deletes `redhat-ods-applications` namespace. Must be re-applied after each cleanup until the operator image is rebuilt with the segment manifests included.

**maas-controller finalizer deadlock (`maas.opendatahub.io/cleanup`)**
- Symptom: `redhat-ods-applications` namespace stuck in `Terminating` indefinitely
- Root cause: `LifecycleReconciler` (PR #870) adds a self-referencing finalizer to the maas-controller Deployment. When namespace is deleted, the controller pod dies before removing its own finalizer.
- Diagnose via MCP: `mcp__kubernetes-mcp-server__resources_get(apiVersion="apps/v1", kind="Deployment", name="maas-controller", namespace="redhat-ods-applications")` — check `metadata.finalizers`
- Fix (confirm with user first):
```bash
oc patch deployment maas-controller -n redhat-ods-applications -p '{"metadata":{"finalizers":null}}' --type=merge
```

### Leftover test projects

Test namespaces matching `*-NNNNN` pattern are only cleaned up at the START of the next build (`cleanupCypressTestNamespaces()` in `runDashboardTestStages.groovy` line 50), not after test completion. Projects remain until the next nightly runs.

```python
# Via MCP tools (preferred):
mcp__kubernetes-mcp-server__projects_list()
# Then filter results for names matching *-NNNNN pattern
```

```bash
# Via oc CLI (fallback):
oc get projects -o name | grep -E '\-[0-9]{5,}$'
```

### GitHub investigation for operator/component issues

```bash
# Search for code references
gh search code "<search-term>" --repo opendatahub-io/<repo> --limit 20

# Find commits touching a file
gh api "repos/opendatahub-io/<repo>/commits?path=<file-path>&per_page=10" \
    --jq '.[] | "\(.sha[0:8]) \(.commit.author.date[0:10]) \(.commit.message | split("\n")[0])"'

# Get PR details
gh api repos/opendatahub-io/<repo>/pulls/<number> \
    --jq '{title: .title, created: .created_at, merged: .merged_at, author: .user.login, body: .body[0:500]}'

# Check if a commit is in a tag/release
gh api "repos/opendatahub-io/<repo>/compare/<commit>...<tag>" \
    --jq '{status: .status, ahead_by: .ahead_by}'

# Get files changed in a PR
gh api "repos/opendatahub-io/<repo>/pulls/<number>/files" --jq '.[].filename'
```

### Key repos for operator issues
- **[opendatahub-io/opendatahub-operator](https://github.com/opendatahub-io/opendatahub-operator)** — RHOAI/ODH operator. DSCI controller, component reconcilers, monitoring, Dockerfiles.
- **[opendatahub-io/models-as-a-service](https://github.com/opendatahub-io/models-as-a-service)** — MaaS controller (maas-controller). Tenant CRs, LifecycleReconciler, finalizers.

### Jenkins shared library (GitLab)

The Jenkins pipeline scripts live on GitLab (project ID 222109, `tguzik/jenkins`):
- `dashboardPostBuild.groovy` — Post-build steps (report portal, etc.)
- `dashboardHelper.groovy` — `cleanupCypressTestNamespaces()` at line 1116
- `runDashboardTestStages.groovy` — Calls cleanup at line 50 during "Verify Cluster is Ready"

```bash
# Fetch a file from GitLab
source .env && curl -s --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
    "$GITLAB_URL/api/v4/projects/222109/repository/files/<path>/raw?ref=master"
```

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
- Don't ask the user to run commands during analysis — investigate autonomously using grep, Read, and shell tools on console logs, test source code, and external APIs
