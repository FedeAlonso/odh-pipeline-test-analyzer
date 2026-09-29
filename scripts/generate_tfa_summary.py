#!/usr/bin/env python3
"""
Generate tfa-summary.txt for Slack posting.

Reads the Phase 1/2 MD report and team-ownership.json, produces a compact
Slack-formatted summary with team @-mentions for each real failure.

Usage:
    python scripts/generate_tfa_summary.py <build_number> <product>
"""
import json
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def log(msg: str):
    print(f"[tfa-summary] {msg}", flush=True)


def load_team_ownership() -> dict | None:
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
            log(f"Loaded team-ownership.json from {p}")
            return json.loads(p.read_text())
    log("team-ownership.json not found — all failures will be Unassigned")
    return None


def resolve_team(file_path: str, ownership: dict | None) -> dict:
    default = {
        "name": "Unassigned",
        "slack_handle": "@openshift-ai-dashboard-qe",
        "slack_group_id": "S08AZ980ER0",
        "emoji": ":question:",
    }
    if not ownership:
        return default

    match = re.search(r"e2e/(.+/)", file_path)
    if not match:
        default_name = ownership.get("default_team", "Unassigned")
        for t in ownership.get("teams", []):
            if t["name"] == default_name:
                return t
        return default

    subpath = match.group(1)
    for team in ownership.get("teams", []):
        for pattern in team.get("path_patterns", []):
            if subpath.startswith(pattern):
                return team

    default_name = ownership.get("default_team", "Unassigned")
    for t in ownership.get("teams", []):
        if t["name"] == default_name:
            return t
    return default


def format_mention(team: dict) -> str:
    gid = team.get("slack_group_id", "")
    handle = team.get("slack_handle", "")
    if gid and handle:
        return f"<!subteam^{gid}|{handle}>"
    return handle or team.get("name", "Unknown")


def parse_report(md_path: Path) -> dict:
    content = md_path.read_text()
    result = {
        "total": 0, "passed": 0, "failed": 0, "skipped": 0,
        "failures": [],
        "flaky": [],
        "root_causes": {},
    }

    m = re.search(r"\*\*Total Tests:\*\*\s*(\d+)", content)
    if m:
        result["total"] = int(m.group(1))
    m = re.search(r"\*\*Passed:\*\*\s*(\d+)", content)
    if m:
        result["passed"] = int(m.group(1))
    m = re.search(r"\*\*Failed:\*\*\s*(\d+)", content)
    if m:
        result["failed"] = int(m.group(1))
    m = re.search(r"\*\*Skipped:\*\*\s*(\d+)", content)
    if m:
        result["skipped"] = int(m.group(1))

    test_header = re.compile(
        r"^### \d+\.\s+(\S+\.cy\.ts)\s*(⚠️\s*\*\(passed on retry\)\*)?",
        re.MULTILINE,
    )
    file_line = re.compile(
        r"\*\*📁 File:\*\*\s*`([^`]+)`",
    )

    current_test = None
    for line in content.split("\n"):
        hm = test_header.match(line)
        if hm:
            test_name = hm.group(1)
            is_flaky = bool(hm.group(2))
            current_test = test_name
            if is_flaky:
                result["flaky"].append(test_name)
            continue

        fm = file_line.search(line)
        if fm and current_test:
            file_path = fm.group(1)
            if current_test not in result["flaky"]:
                result["failures"].append({
                    "name": current_test,
                    "file_path": file_path,
                })
            current_test = None

    # If a test header had no file line following it (edge case), add it without path
    # Re-scan to catch tests that are real failures but we missed
    all_test_names = {f["name"] for f in result["failures"]}
    for hm in test_header.finditer(content):
        test_name = hm.group(1)
        is_flaky = bool(hm.group(2))
        if not is_flaky and test_name not in all_test_names:
            # Find the file path after this header
            pos = hm.end()
            chunk = content[pos:pos + 500]
            fm2 = file_line.search(chunk)
            file_path = fm2.group(1) if fm2 else ""
            result["failures"].append({"name": test_name, "file_path": file_path})

    cluster_header = re.compile(
        r"^### Failure Cluster \d+:\s*(\S+)\s*—",
        re.MULTILINE,
    )
    root_cause_line = re.compile(r"^\*\*Root cause:\*\*\s*(.+)", re.MULTILINE)
    for chm in cluster_header.finditer(content):
        test_key = chm.group(1).strip()
        after = content[chm.end():chm.end() + 2000]
        rcm = root_cause_line.search(after)
        if rcm:
            root_cause = rcm.group(1).strip()
            first_sentence = re.split(r"(?<=[.!])\s", root_cause, maxsplit=1)[0]
            if len(first_sentence) > 200:
                first_sentence = first_sentence[:197] + "..."
            result["root_causes"][test_key] = first_sentence

    return result


def find_root_cause(test_name: str, root_causes: dict) -> str:
    base = test_name.replace(".cy.ts", "")
    for key, cause in root_causes.items():
        if base.lower() in key.lower() or key.lower() in base.lower():
            return cause
    return "(analysis pending)"


def build_summary(build_number: str, product: str, report: dict, ownership: dict | None) -> str:
    name = product.upper()
    jenkins_url = os.getenv("JENKINS_URL", "").strip().rstrip("/")
    artifact_url = ""
    if jenkins_url:
        artifact_url = (
            f"{jenkins_url}/job/components/job/dashboard"
            f"/job/dashboard-e2e-tests/{build_number}/TFA_20Analysis/"
        )

    lines = [
        f":test_tube: TFA Summary — Build {build_number} ({name})",
        f"Pass {report['passed']} | Fail {report['failed']}"
        + (f" | Skipped {report['skipped']}" if report['skipped'] else "")
        + (f" | Flaky {len(report['flaky'])}" if report['flaky'] else ""),
    ]

    if artifact_url:
        lines.append(f":page_facing_up: Full Report: {artifact_url}")

    if not report["failures"]:
        lines.append("")
        lines.append(":white_check_mark: No real failures!")
        return "\n".join(lines)

    lines.append("")
    lines.append(":red_circle: Real Failures:")

    for failure in report["failures"]:
        test_display = failure["name"].replace(".cy.ts", "")
        team = resolve_team(failure.get("file_path", ""), ownership)
        mention = format_mention(team)
        root_cause = find_root_cause(failure["name"], report["root_causes"])

        lines.append("")
        lines.append(f"{test_display} — {mention}")
        lines.append(f"Root Cause: {root_cause}")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <build_number> <product>", file=sys.stderr)
        sys.exit(1)

    build_number = sys.argv[1]
    product = sys.argv[2].lower()
    name = product.upper()

    log(f"Generating TFA summary for build {build_number} ({name})")

    md_path = PROJECT_ROOT / "reports" / "current" / name / f"latest-build-{build_number}.md"
    if not md_path.exists():
        log(f"Report not found: {md_path}")
        sys.exit(1)

    ownership = load_team_ownership()
    report = parse_report(md_path)

    log(f"Parsed: {report['passed']} passed, {report['failed']} failed, "
        f"{len(report['flaky'])} flaky, {len(report['failures'])} real failures")

    summary = build_summary(build_number, product, report, ownership)

    output_dir = PROJECT_ROOT / "reports" / "current" / name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "tfa-summary.txt"
    output_path.write_text(summary)

    log(f"Written to {output_path}")
    print("---")
    print(summary)
    print("---")


if __name__ == "__main__":
    main()
