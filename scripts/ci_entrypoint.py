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

    # Slack MCP — disabled in CI. Session tokens (xoxc/xoxd) expire frequently
    # and can't be refreshed programmatically. Slack posting is handled by the
    # interactive Claude Code agent when running locally.

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


def run_analysis(build_number: str, product: str) -> int:
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
    if not is_false("SKIP_SLACK"):
        cmd.append("--skip-slack")
    if is_true("SKIP_JIRA"):
        cmd.append("--skip-jira")

    log(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode


def extract_phase1_context(build_number: str, product: str) -> dict:
    """Extract key findings from Phase 1 MD report for the agent prompt."""
    name = product.upper()
    md_path = PROJECT_ROOT / "reports" / "current" / name / f"latest-build-{build_number}.md"

    context = {"failures": [], "flaky": [], "jira_ticket": ""}

    if not md_path.exists():
        return context

    content = md_path.read_text()

    # Extract test failures from section headers: ### N. testName.cy.ts [⚠️ *(passed on retry)*]
    test_pattern = re.compile(
        r"^### \d+\.\s+(\S+\.cy\.ts)\s*(⚠️\s*\*\(passed on retry\)\*)?",
        re.MULTILINE,
    )
    for m in test_pattern.finditer(content):
        test_name = m.group(1).replace(".cy.ts", "")
        if m.group(2):
            context["flaky"].append(test_name)
        else:
            context["failures"].append(test_name)

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


def build_deep_analysis_prompt(build_number: str, product: str) -> str:
    """Build a context-rich prompt with Phase 1 findings. Investigation steps are in CLAUDE.md."""
    name = product.upper()
    context = extract_phase1_context(build_number, product)

    parts = [
        f"Run deep analysis for build {build_number} {name}.",
        f"Phase 1 reports: reports/current/{name}/latest-build-{build_number}.md and "
        f"reports/current/{name}/latest-build-{build_number}.html.",
    ]

    if context["failures"]:
        parts.append(
            f"Real failures to investigate: {', '.join(context['failures'])}."
        )

    if context["flaky"]:
        parts.append(
            f"Flaky tests (passed on retry, lower priority): {', '.join(context['flaky'])}."
        )

    cluster_url = os.getenv("CLUSTER_API_URL", "").strip()
    if cluster_url:
        parts.append(
            f"Cluster access is configured ({cluster_url}). "
            f"Use K8s MCP tools to verify pod health, check operator logs, "
            f"inspect ServingRuntime CRs, and confirm root causes with cluster evidence."
        )

    prev_ticket = find_previous_build_ticket(build_number, product)
    if prev_ticket:
        parts.append(
            f"Previous build analysis: {prev_ticket}. "
            f"Read its comments for trend comparison."
        )

    if context["jira_ticket"]:
        parts.append(
            f"This build's lock ticket: {context['jira_ticket']}."
        )

    parts.append(
        "Execute steps 4b, 4c, 4d, and 5 from the Agent Workflow in CLAUDE.md. "
        "Use scripts/inject_deep_analysis.py to update the reports. "
        "Do NOT ask for confirmation — run everything autonomously."
    )

    if is_true("SKIP_JIRA"):
        parts.append("Skip Jira posting.")

    return " ".join(parts)


def run_deep_analysis(build_number: str, product: str) -> int:
    """Run Claude Code agent for deep analysis, Jira posting, and Slack thread."""
    prompt = build_deep_analysis_prompt(build_number, product)

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

    skip_slack = not is_false("SKIP_SLACK")

    log(f"Build: {build_number}")
    log(f"Product: {product}")
    log(f"Deep analysis: {'disabled' if skip_deep else 'enabled'}")
    log(f"Slack: {'disabled (default in CI — set SKIP_SLACK=false to enable)' if skip_slack else 'enabled'}")

    # Setup
    setup_tracer()
    setup_cluster_access(product, build_number)
    setup_frontend_repo()

    # Phase 1: Automated analysis
    log("")
    log("Phase 1: Automated analysis")
    log("-" * 40)
    analysis_rc = run_analysis(build_number, product)

    if analysis_rc != 0:
        log(f"Automated analysis exited with code {analysis_rc}")
        log("Skipping deep analysis — Phase 1 failed (missing artifacts or fatal error)")
        skip_deep = True

    # Configure MCP servers for Claude Code (Slack, K8s)
    if not skip_deep:
        configure_mcp_servers()

    # Phase 2: Claude Code deep analysis
    if not skip_deep:
        log("")
        log("Phase 2: Deep analysis (Claude Code agent)")
        log("-" * 40)
        deep_rc = run_deep_analysis(build_number, product)
        if deep_rc != 0:
            log(f"Deep analysis exited with code {deep_rc}")
    else:
        log("")
        log("Phase 2: Skipped (SKIP_DEEP_ANALYSIS=true)")
        deep_rc = 0

    # Summary
    log("")
    log("=" * 50)
    log(f"Automated analysis: {'OK' if analysis_rc == 0 else f'FAILED ({analysis_rc})'}")
    if not skip_deep:
        log(f"Deep analysis:      {'OK' if deep_rc == 0 else f'FAILED ({deep_rc})'}")

    # Exit with analysis exit code (deep analysis failures are non-fatal)
    sys.exit(analysis_rc)


if __name__ == "__main__":
    main()
