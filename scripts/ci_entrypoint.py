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
  SKIP_DEEP_ANALYSIS          Set to "true" to skip Claude Code agent step
  SKIP_RERUN                  Set to "true" to skip test reruns
  SKIP_SLACK                  Set to "true" to skip Slack posting
  SKIP_JIRA                   Set to "true" to skip Jira operations

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
    if os.getenv("CLAUDE_CODE_USE_VERTEX") == "1":
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


def run_deep_analysis(build_number: str, product: str) -> int:
    """Run Claude Code agent for deep analysis, Jira posting, and Slack thread."""
    skip_slack = not is_false("SKIP_SLACK")
    skip_jira = is_true("SKIP_JIRA")

    slack_instruction = "" if skip_slack else " Post analysis to Slack thread."
    jira_instruction = "" if skip_jira else " Post findings to Jira lock ticket."

    prompt = (
        f"Run nightly analysis for build {build_number} {product}. "
        f"Deep analysis of uninvestigated failures."
        f"{jira_instruction}{slack_instruction} "
        f"Do NOT ask for confirmation — run everything autonomously."
    )

    cmd = [
        "claude",
        "-p", prompt,
        "--dangerously-skip-permissions",
    ]

    auth_mode = "Vertex AI" if os.getenv("CLAUDE_CODE_USE_VERTEX") == "1" else "Anthropic API"
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
    setup_frontend_repo()

    # Phase 1: Automated analysis
    log("")
    log("Phase 1: Automated analysis")
    log("-" * 40)
    analysis_rc = run_analysis(build_number, product)

    if analysis_rc != 0:
        log(f"Automated analysis exited with code {analysis_rc}")
        log("Continuing to deep analysis despite errors...")

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
