#!/usr/bin/env python3
"""
Generate tfa-summary.txt for Jenkins Bot threaded replies.

Reads the MD report (Phase 1) and deep analysis findings (Phase 2, if present),
resolves scrum team ownership for each real failure, and writes a Slack-formatted
summary that the Jenkins pipeline posts as a bot-authored thread reply.

Usage:
    python scripts/generate_tfa_summary.py <build_number> <product>
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.config import Config
from analyzer.scrum_teams import format_slack_mention, resolve_team


def parse_report(report_path: str) -> dict:
    """Parse the MD report to extract test counts, failures, and file paths."""
    with open(report_path, "r") as f:
        content = f.read()

    result = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "failures": [],
        "deep_analysis": {},
    }

    # Extract counts from Quick Status Overview
    m = re.search(r"\*\*Total Tests:\*\*\s*(\d+)", content)
    if m:
        result["total"] = int(m.group(1))
    m = re.search(r"\*\*Passed:\*\*\s*(\d+)", content)
    if m:
        result["passed"] = int(m.group(1))
    m = re.search(r"\*\*Failed:\*\*\s*(\d+)", content)
    if m:
        result["failed"] = int(m.group(1))

    # Parse the detailed failure sections
    # Pattern: ### N. testName.cy.ts [⚠️ *(passed on retry)*]
    sections = re.split(r"### \d+\.\s+", content)
    for section in sections[1:]:
        lines = section.strip().split("\n")
        header = lines[0]

        # Extract test file name from header
        test_file_match = re.match(r"(\S+\.cy\.ts)", header)
        if not test_file_match:
            continue

        test_name = test_file_match.group(1)
        is_flaky = "passed on retry" in header

        # Extract file path
        file_path = ""
        file_match = re.search(
            r"\*\*📁 File:\*\*\s*`([^`]+)`", section
        )
        if file_match:
            file_path = file_match.group(1)

        # Extract error message (first line of error block)
        error_msg = ""
        error_match = re.search(
            r"\*\*❌ Original Error:\*\*\s*```\s*\n(.+?)```",
            section,
            re.DOTALL,
        )
        if error_match:
            error_lines = error_match.group(1).strip().split("\n")
            for line in error_lines:
                line = line.strip()
                if not line or line.startswith("at ") or len(line) <= 10:
                    continue
                # Skip "Test steps were:" dumps — not a useful root cause
                if line.startswith("Test steps were:"):
                    continue
                error_msg = line
                break

        if not is_flaky:
            result["failures"].append(
                {
                    "test_name": test_name,
                    "file_path": file_path,
                    "error_msg": error_msg,
                }
            )

    # Parse deep analysis section if present
    deep_match = re.search(
        r"## 🔬 Deep Analysis(.*?)(?=\n## |\Z)", content, re.DOTALL
    )
    if deep_match:
        deep_content = deep_match.group(1)
        # Extract root causes per test/cluster
        # Pattern: ### testName or ### Failure Cluster: ...
        deep_sections = re.split(r"###\s+", deep_content)
        for ds in deep_sections[1:]:
            ds_lines = ds.strip().split("\n")
            ds_header = ds_lines[0].strip()

            root_cause = ""
            rc_match = re.search(
                r"\*\*Root [Cc]ause\*\*[:\s—–-]*(.+?)(?:\n\n|\n\*\*|\Z)",
                ds,
                re.DOTALL,
            )
            if rc_match:
                root_cause = rc_match.group(1).strip()
                # Collapse to single line
                root_cause = re.sub(r"\s+", " ", root_cause)
                # Trim to ~200 chars
                if len(root_cause) > 200:
                    root_cause = root_cause[:197] + "..."

            # Map deep analysis header to test names
            for failure in result["failures"]:
                test_base = failure["test_name"].replace(".cy.ts", "")
                if test_base.lower() in ds_header.lower():
                    result["deep_analysis"][failure["test_name"]] = root_cause

    return result


def build_deep_analysis_url(build_number: str) -> str:
    """Construct the Jenkins artifact URL for the TFA analysis report."""
    jenkins_url = Config.JENKINS_URL
    if not jenkins_url:
        return "(no Jenkins URL configured)"
    jenkins_url = jenkins_url.rstrip("/")
    job_path = Config.DASHBOARD_TESTS_JOB_PATH.replace("/", "/job/")
    return f"{jenkins_url}/job/{job_path}/{build_number}/TFA_20Analysis/"


def generate_summary(build_number: str, product: str) -> str:
    """Generate the tfa-summary.txt content."""
    platform_dir = product.upper()
    report_dir = Path(Config.REPORT_OUTPUT_DIR) / "current" / platform_dir
    report_path = report_dir / f"latest-build-{build_number}.md"

    if not report_path.exists():
        print(f"Report not found: {report_path}")
        return ""

    data = parse_report(str(report_path))
    deep_url = build_deep_analysis_url(build_number)

    lines = []
    lines.append(f"Pass {data['passed']}")
    lines.append(f"Fail {data['failed']}")
    lines.append(f"Skipping {data['skipped']}")
    lines.append("")

    if not data["failures"]:
        lines.append("No real failures (all passed on retry or no test failures).")
        return "\n".join(lines)

    for failure in data["failures"]:
        file_path = failure["file_path"] or failure["test_name"]
        team = resolve_team(file_path)
        mention = format_slack_mention(team)

        root_cause = data["deep_analysis"].get(failure["test_name"], "")
        if not root_cause and failure["error_msg"]:
            root_cause = failure["error_msg"]
        if not root_cause:
            root_cause = "(analysis pending)"
        if len(root_cause) > 200:
            root_cause = root_cause[:197] + "..."

        test_display = failure["test_name"].replace(".cy.ts", "")
        lines.append(f"{test_display} - {mention}")
        lines.append(f"Deep Analysis - {deep_url}")
        lines.append(f"Root Cause: {root_cause}")
        lines.append("")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <build_number> <product>")
        sys.exit(1)

    build_number = sys.argv[1]
    product = sys.argv[2].lower()

    summary = generate_summary(build_number, product)
    if not summary:
        print("No summary generated (report not found or empty)")
        sys.exit(1)

    platform_dir = product.upper()
    output_dir = Path(Config.REPORT_OUTPUT_DIR) / "current" / platform_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "tfa-summary.txt"

    with open(output_path, "w") as f:
        f.write(summary)

    print(f"TFA summary written to {output_path}")
    print("---")
    print(summary)


if __name__ == "__main__":
    main()
