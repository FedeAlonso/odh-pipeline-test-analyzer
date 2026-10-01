#!/usr/bin/env python3
"""
CI Entry Point for Pipeline Test Analyzer.

Orchestrates the full analysis workflow inside a container:
  1. Validates required environment variables
  2. Clones odh-dashboard if FRONTEND_REPO_PATH is not set
  3. Runs comprehensive_analysis.py (automated analysis)
  4. Optionally runs Claude Code agent for deep analysis + Slack/Jira posting

Usage:
  python scripts/ci_entrypoint.py

Environment variables:
  BUILD_NUMBER                Jenkins build number or "latest" (required)
  PRODUCT                     "rhoai" or "odh" (required)
  JENKINS_URL                 Jenkins server URL (required)
  JENKINS_USER                Jenkins username (required)
  JENKINS_TOKEN               Jenkins API token (required)
  FRONTEND_REPO_PATH          Path to odh-dashboard clone (optional, auto-cloned if missing)
  SKIP_DEEP_ANALYSIS          Set to "true" to skip Claude Code agent step (runs by default)
  SKIP_RERUN                  Set to "true" to skip test reruns
  SKIP_SLACK                  Set to "true" to skip Slack posting
  SKIP_JIRA                   Set to "true" to skip Jira operations
  CLUSTER_USERNAME            Cluster admin username (optional, from odhcluster test-variables.yml)
  CLUSTER_PASSWORD            Cluster admin password (optional, from odhcluster test-variables.yml)
  CLUSTER_API_URL             Override cluster API URL (optional — auto-extracted from build console)

Claude API auth (one of the following, unless SKIP_DEEP_ANALYSIS=true):
  Option A — Direct Anthropic API:
    ANTHROPIC_API_KEY          Anthropic API key

  Option B — Google Vertex AI:
    CLAUDE_CODE_USE_VERTEX     Set to "1"
    ANTHROPIC_VERTEX_PROJECT_ID  GCP project ID
    CLOUD_ML_REGION            GCP region (e.g. us-east5)
    + Google Cloud credentials via one of:
      - GOOGLE_APPLICATION_CREDENTIALS pointing to a service account JSON key
      - Mounted gcloud config (~/.config/gcloud)
      - GKE Workload Identity (automatic)
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE = Path("/workspace")
DASHBOARD_REPO_URL = "https://github.com/opendatahub-io/odh-dashboard.git"


def log(msg: str):
    print(f"[ci] {msg}", flush=True)


def is_true(env_var: str) -> bool:
    return os.getenv(env_var, "").lower() == "true"


def is_false(env_var: str) -> bool:
    return os.getenv(env_var, "").lower() == "false"


def has_claude_auth() -> bool:
    """Check if Claude API authentication is configured (direct or Vertex)."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return True
    if os.getenv("CLAUDE_CODE_USE_VERTEX", "").lower() in ("1", "true"):
        if os.getenv("ANTHROPIC_VERTEX_PROJECT_ID") and os.getenv("CLOUD_ML_REGION"):
            return True
    return False


def configure_mcp_servers():
    """Write MCP server config to ~/.claude.json with actual token values from env."""
    home = Path(os.environ.get("HOME", "/opt/app-root/src"))
    claude_json_path = home / ".claude.json"

    config = {"projects": {"/app": {"hasTrustDialogAccepted": True}}, "mcpServers": {}}

    # Slack MCP — configured when SLACK_XOXC_TOKEN and SLACK_XOXD_TOKEN are set.
    # Session tokens (xoxc/xoxd) expire every 2-4 weeks; refresh in Vault when needed.
    xoxc = os.getenv("SLACK_XOXC_TOKEN", "")
    xoxd = os.getenv("SLACK_XOXD_TOKEN", "")
    if xoxc and xoxd:
        config["mcpServers"]["slack"] = {
            "command": sys.executable,
            "args": ["/opt/slack-mcp/slack_mcp_server.py"],
            "env": {
                "SLACK_XOXC_TOKEN": xoxc,
                "SLACK_XOXD_TOKEN": xoxd,
                "MCP_TRANSPORT": "stdio",
            },
        }
        log("MCP: Slack server configured")
    else:
        log("MCP: Slack server skipped (no xoxc/xoxd tokens)")

    # Kubernetes MCP — needs KUBECONFIG or ~/.kube/config
    kubeconfig = os.getenv("KUBECONFIG", str(home / ".kube" / "config"))
    if Path(kubeconfig).exists():
        config["mcpServers"]["kubernetes-mcp-server"] = {
            "command": "kubernetes-mcp-server",
            "args": [],
            "env": {"KUBECONFIG": kubeconfig},
        }
        log(f"MCP: Kubernetes server configured (kubeconfig: {kubeconfig})")
    else:
        log(f"MCP: Kubernetes server skipped (no kubeconfig at {kubeconfig})")

    claude_json_path.write_text(json.dumps(config))
    log(f"MCP: Config written to {claude_json_path}")


def validate_env():
    """Check required environment variables and return any missing ones."""
    required = {
        "BUILD_NUMBER": "Jenkins build number or 'latest'",
        "PRODUCT": "'rhoai' or 'odh'",
        "JENKINS_URL": "Jenkins server URL",
        "JENKINS_USER": "Jenkins username",
        "JENKINS_TOKEN": "Jenkins API token",
    }

    missing = []
    for var, desc in required.items():
        if not os.getenv(var):
            missing.append(f"  {var} — {desc}")

    if not is_true("SKIP_DEEP_ANALYSIS") and not has_claude_auth():
        missing.append(
            "  Claude auth — set ANTHROPIC_API_KEY or "
            "(CLAUDE_CODE_USE_VERTEX=1 + ANTHROPIC_VERTEX_PROJECT_ID + CLOUD_ML_REGION), "
            "or set SKIP_DEEP_ANALYSIS=true"
        )

    product = os.getenv("PRODUCT", "").lower()
    if product and product not in ("rhoai", "odh"):
        missing.append(f"  PRODUCT — must be 'rhoai' or 'odh', got '{product}'")

    return missing


def setup_tracer():
    """Configure tracer Quay auth if QUAY_TRACER_TOKEN is set."""
    token = os.getenv("QUAY_TRACER_TOKEN")
    if not token:
        log("Tracer: QUAY_TRACER_TOKEN not set — tracer will run without Quay auth")
        return

    home = Path(os.environ.get("HOME", "/opt/app-root/src"))
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    token_file = ssh_dir / ".rhoai_quay_ro_token"
    token_file.write_text(token)
    log("Tracer: Quay auth token written")

    tracer = os.getenv("TRACER_PATH", "/usr/local/bin/tracer.sh")
    if Path(tracer).exists():
        home = Path(os.environ.get("HOME", "/opt/app-root/src"))
        auth_file = home / ".config" / "containers" / "auth.json"
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("REGISTRY_AUTH_FILE", str(auth_file))
        result = subprocess.run(
            ["bash", tracer, "configure"],
            capture_output=True, text=True,
        )
        if "Login Succeeded" in (result.stdout + result.stderr):
            log("Tracer: skopeo login succeeded")
        else:
            log(f"Tracer: skopeo login may have failed — {result.stdout.strip()} {result.stderr.strip()}")


def extract_cluster_api_url(build_number: str) -> str:
    """Extract the cluster API URL from the build's console log via Jenkins API."""
    jenkins_url = os.getenv("JENKINS_URL", "").strip().rstrip("/")
    jenkins_user = os.getenv("JENKINS_USER", "").strip()
    jenkins_token = os.getenv("JENKINS_TOKEN", "").strip()

    if not all([jenkins_url, jenkins_user, jenkins_token]):
        return ""

    try:
        import httpx

        job_path = "components/dashboard/dashboard-e2e-tests"
        api_path = "/job/".join(job_path.split("/"))
        url = f"{jenkins_url}/job/{api_path}/{build_number}/consoleText"
        ssl_verify = os.getenv("SSL_VERIFY", "true").lower() == "true"
        resp = httpx.get(
            url,
            auth=(jenkins_user, jenkins_token),
            timeout=30,
            verify=ssl_verify,
            headers={"Range": "bytes=0-50000"},
        )
        if resp.status_code == 401:
            resp = httpx.get(
                url, timeout=30, verify=ssl_verify,
                headers={"Range": "bytes=0-50000"},
            )
        if resp.status_code not in (200, 206):
            return ""

        text = resp.text
        match = re.search(r"Cluster API URL:\s*(https://api\.\S+:\d+)", text)
        if match:
            return match.group(1)
        match = re.search(r"oc login\s+.*\s+(https://api\.\S+:\d+)", text)
        if match:
            return match.group(1)
    except Exception:
        pass

    return ""


def setup_cluster_access(product: str, build_number: str) -> bool:
    """Login to test cluster if credentials are available. Creates ~/.kube/config for K8s MCP.

    API URL is auto-extracted from the build's console log. Username and password
    come from Vault (same credentials for all clusters — odhcluster/test-variables.yml).
    """
    username = os.getenv("CLUSTER_USERNAME", "").strip()
    password = os.getenv("CLUSTER_PASSWORD", "").strip()

    if not all([username, password]):
        log("Cluster: No credentials provided — cluster inspection will be unavailable")
        return False

    api_url = os.getenv("CLUSTER_API_URL", "").strip()
    if not api_url:
        log("Cluster: Extracting API URL from build console log...")
        api_url = extract_cluster_api_url(build_number)

    if not api_url:
        log("Cluster: Could not determine API URL — cluster inspection will be unavailable")
        return False

    os.environ["CLUSTER_API_URL"] = api_url

    # Override KUBECONFIG — the e2e pipeline sets it to a workspace path that may not
    # be writable inside the TFA container (different HOME, permission denied)
    home = Path(os.environ.get("HOME", "/opt/app-root/src"))
    kubeconfig = home / ".kube" / "config"
    kubeconfig.parent.mkdir(parents=True, exist_ok=True)
    os.environ["KUBECONFIG"] = str(kubeconfig)

    ssl_verify = os.getenv("SSL_VERIFY", "true").lower() == "true"
    cmd = ["oc", "login", "-u", username, "--server", api_url]
    if not ssl_verify:
        cmd.append("--insecure-skip-tls-verify=true")

    result = subprocess.run(
        cmd, input=password + "\n", capture_output=True, text=True,
    )
    if result.returncode == 0:
        log(f"Cluster: Logged in to {api_url}")
        prefix = product.upper()
        os.environ[f"{prefix}_API_SERVER"] = api_url
        os.environ[f"{prefix}_USERNAME"] = username
        os.environ[f"{prefix}_PASSWORD"] = password
        return True
    else:
        sanitized = result.stderr.strip()
        if password and password in sanitized:
            sanitized = sanitized.replace(password, "[REDACTED]")
        log(f"Cluster: Login failed — {sanitized}")
        return False


def check_dsc_status() -> dict | None:
    """Check DataScienceCluster status. Returns dict with health info or None if unavailable."""
    try:
        result = subprocess.run(
            ["oc", "get", "datasciencecluster", "-A", "-o", "json"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log("DSC check: could not retrieve DataScienceCluster")
            return None

        data = json.loads(result.stdout)
        items = data.get("items", [])
        if not items:
            log("DSC check: no DataScienceCluster found")
            return {"healthy": False, "error": "No DataScienceCluster resource exists", "components": {}}

        dsc = items[0]
        phase = dsc.get("status", {}).get("phase", "Unknown")
        conditions = {c["type"]: c for c in dsc.get("status", {}).get("conditions", [])}

        # Component readiness from conditions (e.g. DashboardReady, KserveReady)
        # Conditions ending in "Ready" with status != "True" and reason != "Removed" are failing
        components = {}
        failing = []
        for ctype, c in conditions.items():
            if ctype.endswith("Ready") and ctype != "Ready":
                comp_name = ctype.replace("Ready", "")
                is_removed = c.get("reason", "") == "Removed"
                is_ready = c.get("status") == "True" or is_removed
                components[comp_name] = {"ready": is_ready, "removed": is_removed, "reason": c.get("reason", ""), "message": c.get("message", "")}
                if not is_ready:
                    failing.append(comp_name)

        healthy = phase == "Ready" and not failing

        status = {
            "healthy": healthy,
            "phase": phase,
            "conditions": {t: {"status": c.get("status"), "reason": c.get("reason", ""), "message": c.get("message", "")} for t, c in conditions.items()},
            "components": components,
            "failing_components": failing,
        }

        managed_count = sum(1 for c in components.values() if not c["removed"])
        if healthy:
            log(f"DSC check: healthy (phase={phase}, {managed_count} managed components)")
        else:
            fail_details = ", ".join(f"{c} ({components[c]['reason']}: {components[c]['message'][:80]})" for c in failing)
            log(f"DSC check: UNHEALTHY (phase={phase}) — {fail_details}")

        return status

    except Exception as e:
        log(f"DSC check: error — {e}")
        return None


def setup_frontend_repo():
    """Clone odh-dashboard if FRONTEND_REPO_PATH is not set."""
    frontend_path = os.getenv("FRONTEND_REPO_PATH")
    if frontend_path and Path(frontend_path).exists():
        log(f"Using existing odh-dashboard at {frontend_path}")
        return frontend_path

    clone_dir = WORKSPACE / "odh-dashboard"
    if clone_dir.exists():
        log(f"Updating existing clone at {clone_dir}")
        subprocess.run(
            ["git", "-C", str(clone_dir), "fetch", "--all", "--prune"],
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "checkout", "main"],
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(clone_dir), "pull", "--ff-only"],
            check=False,
        )
    else:
        log(f"Cloning odh-dashboard to {clone_dir}")
        subprocess.run(
            ["git", "clone", "--depth", "50", DASHBOARD_REPO_URL, str(clone_dir)],
            check=True,
        )

    os.environ["FRONTEND_REPO_PATH"] = str(clone_dir)
    return str(clone_dir)


def run_analysis(build_number: str, product: str, skip_slack: bool = True) -> int:
    """Run comprehensive_analysis.py and return the exit code."""
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "comprehensive_analysis.py"),
        build_number,
        product,
        "-y",
        "--enable-trend",
    ]

    if is_true("SKIP_RERUN"):
        cmd.append("--skip-rerun")
    if skip_slack:
        cmd.append("--skip-slack")
    if is_true("SKIP_JIRA"):
        cmd.append("--skip-jira")

    log(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode


def load_team_ownership() -> dict | None:
    """Load team-ownership.json from odh-dashboard repo."""
    candidates = []
    frontend = os.getenv("FRONTEND_REPO_PATH", "")
    if frontend:
        candidates.append(
            Path(frontend) / "packages/cypress/cypress/tests/e2e/team-ownership.json"
        )
    candidates.extend([
        PROJECT_ROOT.parent / "odh-dashboard/packages/cypress/cypress/tests/e2e/team-ownership.json",
        Path("/workspace/odh-dashboard/packages/cypress/cypress/tests/e2e/team-ownership.json"),
    ])
    for p in candidates:
        if p.exists():
            return json.loads(p.read_text())
    return None


def resolve_team_mention(file_path: str, ownership: dict | None) -> str:
    """Map a test file path to a Slack team @-mention string."""
    if not ownership:
        return ""
    match = re.search(r"e2e/(.+/)", file_path)
    default_name = ownership.get("default_team", "")
    teams = ownership.get("teams", [])

    target_team = None
    if match:
        subpath = match.group(1)
        for team in teams:
            for pattern in team.get("path_patterns", []):
                if subpath.startswith(pattern):
                    target_team = team
                    break
            if target_team:
                break

    if not target_team and default_name:
        for t in teams:
            if t["name"] == default_name:
                target_team = t
                break

    if not target_team:
        return ""
    gid = target_team.get("slack_group_id", "")
    handle = target_team.get("slack_handle", "")
    if gid and handle:
        return f"<!subteam^{gid}|{handle}>"
    return handle or ""


def extract_phase1_context(build_number: str, product: str) -> dict:
    """Extract key findings from Phase 1 MD report for the agent prompt."""
    name = product.upper()
    md_path = PROJECT_ROOT / "reports" / "current" / name / f"latest-build-{build_number}.md"

    context = {"failures": [], "flaky": [], "jira_ticket": "", "team_mentions": {}}

    if not md_path.exists():
        return context

    content = md_path.read_text()
    ownership = load_team_ownership()

    test_pattern = re.compile(
        r"^### \d+\.\s+(\S+\.cy\.ts)\s*(⚠️\s*\*\(passed on retry\)\*)?",
        re.MULTILINE,
    )
    file_pattern = re.compile(r"\*\*📁 File:\*\*\s*`([^`]+)`")

    for m in test_pattern.finditer(content):
        test_name = m.group(1).replace(".cy.ts", "")
        is_flaky = bool(m.group(2))
        if is_flaky:
            context["flaky"].append(test_name)
        else:
            context["failures"].append(test_name)
            chunk = content[m.end():m.end() + 500]
            fm = file_pattern.search(chunk)
            file_path = fm.group(1) if fm else ""
            mention = resolve_team_mention(file_path, ownership)
            if mention:
                context["team_mentions"][test_name] = mention

    # Extract Jira lock ticket from file (written by comprehensive_analysis.py)
    ticket_file = Path("/app/jira-ticket.txt")
    if ticket_file.exists():
        context["jira_ticket"] = ticket_file.read_text().strip()

    return context


def find_previous_build_ticket(build_number: str, product: str) -> str:
    """Search Jira for a recent analysis ticket from a previous build for trend context."""
    jira_url = os.getenv("JIRA_URL", "").strip()
    jira_user = os.getenv("JIRA_USER", "").strip()
    jira_token = os.getenv("JIRA_TOKEN", "").strip()

    if not all([jira_url, jira_user, jira_token]):
        return ""

    name = product.upper()
    try:
        import httpx

        jql = (
            f'project = RHOAIENG AND summary ~ "Nightly Analysis" '
            f'AND summary ~ "{name}" '
            f"ORDER BY created DESC"
        )
        resp = httpx.get(
            f"{jira_url}/rest/api/3/search",
            params={"jql": jql, "maxResults": 5, "fields": "key,summary"},
            auth=(jira_user, jira_token),
            timeout=15,
            verify=os.getenv("SSL_VERIFY", "true").lower() == "true",
        )
        if resp.status_code != 200:
            return ""

        issues = resp.json().get("issues", [])
        # Find the first ticket that is NOT for the current build
        for issue in issues:
            summary = issue.get("fields", {}).get("summary", "")
            if f"-{build_number}-" not in summary:
                return issue["key"]
    except Exception:
        pass

    return ""


DEEP_ANALYSIS_OUTPUT = Path("/tmp/deep_analysis.md")


def build_deep_analysis_prompt(build_number: str, product: str, skip_slack: bool = False, dsc_status: dict | None = None) -> str:
    """Build a prescriptive prompt with exact commands for each step."""
    name = product.upper()
    context = extract_phase1_context(build_number, product)
    jira_ticket = context.get("jira_ticket", "")
    cluster_url = os.getenv("CLUSTER_API_URL", "").strip()
    prev_ticket = find_previous_build_ticket(build_number, product)

    md_report = f"reports/current/{name}/latest-build-{build_number}.md"
    html_report = f"reports/current/{name}/latest-build-{build_number}.html"
    screenshots_dir = f"reports/current/{name}/screenshots"
    videos_dir = f"reports/current/{name}/videos"

    failures_list = ", ".join(context["failures"]) if context["failures"] else "none"
    flaky_list = ", ".join(context["flaky"]) if context["flaky"] else "none"

    team_mentions = context.get("team_mentions", {})
    team_lines = ""
    if team_mentions:
        team_lines = "\n- Team ownership (include these @-mentions next to each real failure in the Slack message):"
        for test, mention in team_mentions.items():
            team_lines += f"\n    {test} → {mention}"

    jenkins_url = os.getenv("JENKINS_URL", "").strip().rstrip("/")
    report_artifact_url = ""
    if jenkins_url:
        report_artifact_url = (
            f"{jenkins_url}/job/components/job/dashboard"
            f"/job/dashboard-e2e-tests/{build_number}/TFA_20Analysis/"
        )

    # DSC status context
    dsc_lines = ""
    dsc_unhealthy = False
    if dsc_status is None:
        dsc_lines = "\n- DSC status: unavailable (no cluster access)"
    elif dsc_status.get("healthy"):
        components = dsc_status.get("components", {})
        managed_count = sum(1 for c in components.values() if not c.get("removed"))
        dsc_lines = f"\n- DSC status: healthy (phase={dsc_status.get('phase')}, {managed_count} managed components ready)"
    else:
        dsc_unhealthy = True
        failing = dsc_status.get("failing_components", [])
        components = dsc_status.get("components", {})
        comp_lines = []
        for c, info in components.items():
            if info.get("removed"):
                continue
            status = "ready" if info.get("ready") else f"NOT READY ({info.get('reason', '')}: {info.get('message', '')[:100]})"
            comp_lines.append(f"{c}={status}")
        dsc_lines = f"\n- DSC status: UNHEALTHY (phase={dsc_status.get('phase')}) — failing: {', '.join(failing)}"
        dsc_lines += f"\n    Components: {', '.join(comp_lines)}"

    prompt = f"""Nightly analysis for build {build_number} ({name}). Run every step below in order. Do NOT ask for confirmation. Do NOT skip any step.

CONTEXT:
- MD report: {md_report}
- HTML report: {html_report}
- Screenshots: {screenshots_dir}/
- Videos: {videos_dir}/
- Real failures: {failures_list}
- Flaky tests: {flaky_list}
- Lock ticket: {jira_ticket or 'none'}
- Previous build ticket: {prev_ticket or 'none'}
- Cluster: {cluster_url or 'not configured'}{dsc_lines}{team_lines}
- TFA report link: {report_artifact_url or 'not available'}
{"" if not dsc_unhealthy else """
DSC ALERT — The DataScienceCluster is UNHEALTHY. This likely explains test failures.
In both the Slack message AND the deep analysis report, add a prominent banner at the TOP (before any test analysis):
- Use :rotating_light: emoji and *bold* to make it unmissable
- List which components are NOT READY and any condition errors
- Explain that tests depending on these components are expected to fail
- Check operator logs (oc logs deployment/rhods-operator -n redhat-ods-operator --tail=100) for root cause
- Link to any Jira tickets about the operator/DSC issue
"""}
STEP 1 — INVESTIGATE EACH REAL FAILURE
For each real failure listed above, do ALL of the following (skip nothing):
1a. Read the MD report section for this test — note error message and failure category.
1b. Look at screenshots in {screenshots_dir}/ for this test — describe what the UI shows.
1c. Check {videos_dir}/ for a recording — describe the failure sequence if present.
1d. Grep the Jenkins console log for the test name — find the exact error and stack trace.
1e. Use K8s MCP tools to check pod health in redhat-ods-applications and redhat-ods-operator.
1f. Check operator age/version — use tracer output or `oc get csv` in redhat-ods-operator.
1g. Search Jira: read {prev_ticket or 'the previous build ticket'} comments for this same failure.
1h. Search RHOAIENG for open bugs mentioning this test name or error. Note key, status, assignee.
1i. Search odh-dashboard repo for recent PRs touching the test file or related components. Use `gh pr list` and `gh api repos/opendatahub-io/odh-dashboard/commits`.
1j. Read the test source in the odh-dashboard repo: frontend/src/__tests__/cypress/cypress/tests/e2e/

STEP 2 — WRITE DEEP ANALYSIS
Write findings to {DEEP_ANALYSIS_OUTPUT} under a `## Deep Analysis` heading.
For each failure cluster, include: root cause, evidence, related PRs (with status), related Jira tickets (with status), trend vs previous builds, recommended action.

STEP 3 — INJECT INTO REPORTS AND UPLOAD TO JIRA
Run this exact command:
python scripts/inject_deep_analysis.py {html_report} {DEEP_ANALYSIS_OUTPUT} --update-md {md_report}{f' --jira-ticket {jira_ticket}' if jira_ticket else ''}
Verify: read the last 20 lines of {md_report} to confirm the deep analysis section was appended.

STEP 4 — POST ANALYSIS SUMMARY TO JIRA
Read the MD report to extract test counts (total, passed, failed, flaky).
Run scripts/post_analysis_summaries.py jira with the correct values:
python scripts/post_analysis_summaries.py jira --ticket {jira_ticket} --build {build_number} --platform {name} --total <N> --passed <N> --failed <N> --real-failures '<name:error:jira_key,...>' --flaky '<name1,name2>' --extra-notes '<key observation>'
"""

    slack_analysis_path = f"reports/current/{name}/slack-analysis.txt"
    if not skip_slack:
        prompt += f"""
STEP 5 — WRITE SLACK ANALYSIS (do NOT post — Jenkins posts it via webhook as the bot)
This is the most important step. The Slack message must be a FULL ANALYSIS, not a summary.

5a. Search for the Jenkins Bot message:
    Use mcp__slack__search_messages with query "dashboard-e2e-tests/{build_number}" to find the bot's build notification.

5b. Get historical context:
    Use mcp__slack__search_messages to find the 5 previous builds' bot messages.
    Use mcp__slack__get_thread on each to read thread replies.

5c. Enrich with external context:
    For each Jira ticket referenced in threads, fetch current status via Jira API.
    For each PR referenced, check if it's merged/open/closed.

5d. Write the Slack analysis to {slack_analysis_path} (the CI pipeline will post it via webhook).
    Do NOT use mcp__slack__post_message — that posts as the user, not the bot.
    The message MUST include all of these sections:
    - Header: "*NOTE: _This is an Agentic-AI generated message_*" then Jira link (use :jira: emoji, NOT :jira2:), stats (total/passed/failed/flaky), cluster health.
    - TFA report link: include "{report_artifact_url}" if available.
    - Deployment info: operator SHA, build date, RHOAI version, dashboard commit.
    - Failure clusters with root cause analysis — explain WHY, not just what. For each real failure, include the team @-mention from the CONTEXT above (copy the `<!subteam^...|...>` exactly as given).
    - Related PRs with status (merged/open) and whether the fix is in this build's image.
    - Related Jira tickets with current status and assignee.
    - Trend analysis: compare vs previous builds using thread data. Show trajectory.
    - Recovery notes: tests that were previously failing but now pass.
    Use Slack mrkdwn formatting (NOT markdown):
    - Bold: *text* (single asterisks). NEVER use **text** (double asterisks — Slack renders them as literal **).
    - Italic: _text_ (underscores).
    - Code: `text` (backticks).
    - Headings: Slack has NO heading syntax. Do NOT use # or ## or ###. Use *Bold Text* on its own line instead.
    - Bullet points: use • or - (both work).
    - Links: <url|text>.
    - Emojis: :emoji_name:.
    Keep under 39000 characters (Slack message limit is 40000).

5e. Verify: read {slack_analysis_path} and confirm it exists and has content.
"""
    else:
        prompt += "\nSTEP 5 — SLACK: Skipped (no tokens configured).\n"

    return prompt


def run_deep_analysis(build_number: str, product: str, skip_slack: bool = False, dsc_status: dict | None = None) -> int:
    """Run Claude Code agent for the full workflow. Returns 0 on success."""
    prompt = build_deep_analysis_prompt(build_number, product, skip_slack=skip_slack, dsc_status=dsc_status)

    if DEEP_ANALYSIS_OUTPUT.exists():
        DEEP_ANALYSIS_OUTPUT.unlink()

    cmd = [
        "claude",
        "-p", prompt,
        "--dangerously-skip-permissions",
    ]

    use_vertex = os.getenv("CLAUDE_CODE_USE_VERTEX", "").lower() in ("1", "true")
    if use_vertex:
        os.environ["CLAUDE_CODE_USE_VERTEX"] = "1"
    auth_mode = "Vertex AI" if use_vertex else "Anthropic API"
    log(f"Running Claude Code agent for deep analysis (auth: {auth_mode})...")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    if result.returncode != 0:
        log(f"Claude Code agent exited with code {result.returncode}")
        return result.returncode

    if not DEEP_ANALYSIS_OUTPUT.exists():
        log(f"Deep analysis: FAILED — agent did not write {DEEP_ANALYSIS_OUTPUT}")
        return 1

    size = DEEP_ANALYSIS_OUTPUT.stat().st_size
    if size < 200:
        log(f"Deep analysis: FAILED — output too small ({size} bytes), likely incomplete")
        return 1

    log(f"Deep analysis: agent wrote {size} bytes to {DEEP_ANALYSIS_OUTPUT}")
    return 0


def postprocess_slack_analysis(build_number: str, product: str):
    """Convert any leftover markdown syntax in slack-analysis.txt to Slack mrkdwn."""
    name = product.upper()
    path = PROJECT_ROOT / "reports" / "current" / name / "slack-analysis.txt"
    if not path.exists():
        return
    text = path.read_text()
    original = text
    # **bold** → *bold* (but not inside code blocks)
    text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
    # ### heading / ## heading / # heading → *heading* on its own line
    text = re.sub(r'^#{1,6}\s+(.+)$', r'*\1*', text, flags=re.MULTILINE)
    # :jira2: → :jira:
    text = text.replace(':jira2:', ':jira:')
    if text != original:
        path.write_text(text)
        log("Slack analysis: converted markdown → Slack mrkdwn")


def inject_deep_analysis(build_number: str, product: str, jira_ticket: str) -> int:
    """Inject deep analysis into reports and optionally upload to Jira."""
    name = product.upper()
    html_path = PROJECT_ROOT / "reports" / "current" / name / f"latest-build-{build_number}.html"
    md_path = PROJECT_ROOT / "reports" / "current" / name / f"latest-build-{build_number}.md"

    if not html_path.exists():
        log(f"Report injection: HTML report not found at {html_path}")
        return 1

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "inject_deep_analysis.py"),
        str(html_path),
        str(DEEP_ANALYSIS_OUTPUT),
    ]

    if md_path.exists():
        cmd.extend(["--update-md", str(md_path)])

    if jira_ticket and not is_true("SKIP_JIRA"):
        cmd.extend(["--jira-ticket", jira_ticket])

    log("Injecting deep analysis into reports...")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))

    if result.returncode == 0:
        log("Report injection: OK")
        if jira_ticket and not is_true("SKIP_JIRA"):
            log(f"Jira upload: OK ({jira_ticket})")
    else:
        log(f"Report injection: FAILED ({result.returncode})")

    return result.returncode


def main():
    log("Pipeline Test Analyzer — CI Entry Point")
    log("=" * 50)

    # Validate
    missing = validate_env()
    if missing:
        log("Missing required environment variables:")
        for m in missing:
            print(m, flush=True)
        sys.exit(1)

    build_number = os.getenv("BUILD_NUMBER")
    product = os.getenv("PRODUCT").lower()
    skip_deep = is_true("SKIP_DEEP_ANALYSIS")

    has_slack_tokens = bool(os.getenv("SLACK_XOXC_TOKEN")) and bool(os.getenv("SLACK_XOXD_TOKEN"))
    skip_slack = not is_false("SKIP_SLACK") and not has_slack_tokens

    log(f"Build: {build_number}")
    log(f"Product: {product}")
    log(f"Deep analysis: {'disabled' if skip_deep else 'enabled'}")
    log(f"Slack: {'disabled' if skip_slack else 'enabled (tokens present)'}")

    # Setup
    setup_tracer()
    setup_cluster_access(product, build_number)
    dsc_status = check_dsc_status()
    setup_frontend_repo()

    # Phase 1: Automated analysis
    log("")
    log("Phase 1: Automated analysis")
    log("-" * 40)
    analysis_rc = run_analysis(build_number, product, skip_slack=skip_slack)

    if analysis_rc != 0:
        log(f"Automated analysis exited with code {analysis_rc}")
        log("Skipping deep analysis — Phase 1 failed (missing artifacts or fatal error)")
        skip_deep = True

    # Configure MCP servers for Claude Code (Slack, K8s)
    if not skip_deep:
        configure_mcp_servers()

    # Phase 2: Claude Code agent — full workflow (deep analysis, Jira, Slack)
    deep_rc = 0
    if not skip_deep:
        log("")
        log("Phase 2: Agent workflow (Claude Code)")
        log("-" * 40)
        deep_rc = run_deep_analysis(build_number, product, skip_slack=skip_slack, dsc_status=dsc_status)

        postprocess_slack_analysis(build_number, product)

        # Fallback: if agent wrote findings but didn't inject them, do it here
        if deep_rc == 0 and DEEP_ANALYSIS_OUTPUT.exists():
            name = product.upper()
            html_path = PROJECT_ROOT / "reports" / "current" / name / f"latest-build-{build_number}.html"
            html_has_analysis = False
            if html_path.exists():
                html_has_analysis = "Deep Analysis" in html_path.read_text()[:50000]
            if not html_has_analysis:
                log("Agent did not inject analysis into reports — running fallback injection")
                context = extract_phase1_context(build_number, product)
                jira_ticket = context.get("jira_ticket", "")
                inject_deep_analysis(build_number, product, jira_ticket)
        elif deep_rc != 0:
            log("Agent workflow failed — reports will not contain deep analysis")
    else:
        log("")
        log("Phase 2: Skipped (SKIP_DEEP_ANALYSIS=true)")

    # Summary
    log("")
    log("=" * 50)
    log(f"Automated analysis: {'OK' if analysis_rc == 0 else f'FAILED ({analysis_rc})'}")
    if not skip_deep:
        agent_ok = deep_rc == 0
        log(f"Deep analysis:      {'OK' if agent_ok else 'FAILED — no analysis in report'}")

    # Exit with analysis exit code (deep analysis failures are non-fatal)
    sys.exit(analysis_rc)


if __name__ == "__main__":
    main()
