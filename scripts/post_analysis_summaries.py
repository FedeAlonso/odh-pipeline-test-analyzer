"""
Post analysis summaries to Jira and generate Slack messages.

Usage:
    # Post agent summary to Jira
    venv/bin/python scripts/post_analysis_summaries.py jira \
        --ticket RHOAIENG-59395 --build 366 --platform RHOAI \
        --total 120 --passed 112 --failed 8 \
        --real-failures 'pipelines:DSPA timeout:RHOAIENG-58177,testPerformanceFiltersAvailable:timeout:RHOAIENG-58910' \
        --flaky 'testRayJobProjectAccessPermissions,testProjectAccessPermissions' \
        --cluster-pods 17:17:0 \
        --pipeline-failure 'dashboardPostBuild:NullPointerException'

    # Generate Slack message from thread data JSON
    venv/bin/python scripts/post_analysis_summaries.py slack \
        --data /tmp/slack_data.json --build 366 --platform RHOAI \
        --real-failures 'pipelines,testPerformanceFiltersAvailable,...' \
        --flaky 'testRayJobProjectAccessPermissions,...' \
        --total 120 --passed 112 --failed 8 \
        --ticket RHOAIENG-59395 \
        --ticket-url https://redhat.atlassian.net/browse/RHOAIENG-59395
"""
import argparse
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.jira_lock import (
    compose_agent_summary,
    post_agent_summary,
    fetch_jira_statuses,
)
from analyzer.slack_helper import (
    prepare_slack_message,
    collect_jira_keys,
)


def parse_real_failures(raw: str) -> list:
    """Parse 'name:error:jira,name2:error2' into list of dicts."""
    if not raw:
        return []
    failures = []
    for entry in raw.split(","):
        parts = entry.strip().split(":")
        f = {"name": parts[0]}
        if len(parts) > 1:
            f["error"] = parts[1]
        if len(parts) > 2:
            f["jira"] = parts[2]
        failures.append(f)
    return failures


def parse_cluster_pods(raw: str) -> dict:
    if not raw:
        return {}
    parts = raw.split(":")
    return {
        "total_pods": int(parts[0]),
        "running": int(parts[1]) if len(parts) > 1 else int(parts[0]),
        "failed": int(parts[2]) if len(parts) > 2 else 0,
    }


def parse_pipeline_failure(raw: str) -> dict:
    if not raw:
        return {}
    parts = raw.split(":")
    return {
        "failed_step": parts[0],
        "exception_type": parts[1] if len(parts) > 1 else "unknown",
    }


async def cmd_jira(args):
    real_failures = parse_real_failures(args.real_failures)
    flaky = [t.strip() for t in args.flaky.split(",")] if args.flaky else []
    cluster = parse_cluster_pods(args.cluster_pods)
    pipeline = parse_pipeline_failure(args.pipeline_failure)

    markdown = compose_agent_summary(
        build_num=args.build,
        platform=args.platform,
        total_tests=args.total,
        passed_tests=args.passed,
        failed_tests=args.failed,
        real_failures=real_failures,
        flaky_tests=flaky,
        cluster_health=cluster,
        pipeline_failure=pipeline,
    )

    if args.extra_notes:
        markdown += f"\n\n### Key Observations\n{args.extra_notes}"

    success = await post_agent_summary(args.ticket, markdown)
    sys.exit(0 if success else 1)


def _normalize_slack_data(data):
    """Normalize slack data: convert dict-format messages to '[ts] text' strings."""
    search_results = data["search_results"]
    thread_data = data["thread_data"]

    if search_results and isinstance(search_results[0], dict):
        search_results = [f"[{m['ts']}] {m.get('text', '')}" for m in search_results]
    normalized_threads = {}
    for ts, msgs in thread_data.items():
        if msgs and isinstance(msgs[0], dict):
            normalized_threads[ts] = [f"[{m['ts']}] {m.get('text', '')}" for m in msgs]
        else:
            normalized_threads[ts] = msgs
    return search_results, normalized_threads


async def cmd_slack(args):
    with open(args.data) as f:
        data = json.load(f)

    search_results, thread_data = _normalize_slack_data(data)

    real_failures = [t.strip() for t in args.real_failures.split(",")] if args.real_failures else []
    flaky = [t.strip() for t in args.flaky.split(",")] if args.flaky else []

    rerun_results = []
    if args.rerun_passed:
        for t in args.rerun_passed.split(","):
            if t.strip():
                rerun_results.append({"test_name": t.strip(), "passed": True})
    if args.rerun_failed:
        for t in args.rerun_failed.split(","):
            if t.strip():
                rerun_results.append({"test_name": t.strip(), "passed": False})

    jira_keys = collect_jira_keys(thread_data)
    jira_statuses = await fetch_jira_statuses(jira_keys)

    cluster = parse_cluster_pods(args.cluster_pods) if args.cluster_pods else {}
    pipeline = parse_pipeline_failure(args.pipeline_failure) if args.pipeline_failure else {}

    vm = {}
    if args.version_mismatch:
        parts = args.version_mismatch.split(":")
        if len(parts) == 2:
            vm = {"has_mismatch": True, "expected_version": parts[0], "installed_version": parts[1],
                  "message": f"FBC fragment targets {parts[0]} but operator installed is {parts[1]}"}

    image_ages = []
    if args.image_ages:
        for entry in args.image_ages.split(","):
            parts = entry.strip().split(":")
            if len(parts) >= 2:
                name, age = parts[0], parts[1]
                try:
                    days = int(age.rstrip('d'))
                    image_ages.append({"component": name, "age_days": days,
                                       "age_str": f"{days}d", "build_date": "", "commit": ""})
                except ValueError:
                    pass

    analysis = {
        "total_tests": args.total,
        "passed_tests": args.passed,
        "failed_tests": args.failed,
        "jira_ticket_key": args.ticket or "",
        "jira_ticket_url": args.ticket_url or "",
        "cluster_health": cluster,
        "pipeline_failure": pipeline,
        "version_mismatch": vm,
        "cluster_image_ages": image_ages,
    }

    result = prepare_slack_message(
        search_results=search_results,
        thread_data=thread_data,
        build_number=args.build,
        platform=args.platform,
        current_failures=real_failures,
        flaky_tests=flaky,
        analysis=analysis,
        jira_statuses=jira_statuses,
        rerun_results=rerun_results,
    )

    output = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"✅ Slack message written to {args.output}")
    else:
        print(output)


def main():
    parser = argparse.ArgumentParser(description="Post analysis summaries")
    sub = parser.add_subparsers(dest="command", required=True)

    # Jira subcommand
    jira_p = sub.add_parser("jira", help="Post agent summary to Jira")
    jira_p.add_argument("--ticket", required=True)
    jira_p.add_argument("--build", type=int, required=True)
    jira_p.add_argument("--platform", required=True)
    jira_p.add_argument("--total", type=int, required=True)
    jira_p.add_argument("--passed", type=int, required=True)
    jira_p.add_argument("--failed", type=int, required=True)
    jira_p.add_argument("--real-failures", default="")
    jira_p.add_argument("--flaky", default="")
    jira_p.add_argument("--cluster-pods", default="")
    jira_p.add_argument("--pipeline-failure", default="")
    jira_p.add_argument("--extra-notes", default="")

    # Slack subcommand
    slack_p = sub.add_parser("slack", help="Generate Slack message from thread data")
    slack_p.add_argument("--data", required=True, help="JSON file with search_results and thread_data")
    slack_p.add_argument("--build", type=int, required=True)
    slack_p.add_argument("--platform", required=True)
    slack_p.add_argument("--total", type=int, required=True)
    slack_p.add_argument("--passed", type=int, required=True)
    slack_p.add_argument("--failed", type=int, required=True)
    slack_p.add_argument("--real-failures", default="")
    slack_p.add_argument("--flaky", default="")
    slack_p.add_argument("--ticket", default="")
    slack_p.add_argument("--ticket-url", default="")
    slack_p.add_argument("--cluster-pods", default="")
    slack_p.add_argument("--pipeline-failure", default="")
    slack_p.add_argument("--rerun-passed", default="", help="Comma-separated test names that passed on rerun")
    slack_p.add_argument("--rerun-failed", default="", help="Comma-separated test names that failed on rerun")
    slack_p.add_argument("--version-mismatch", default="", help="expected:installed (e.g. '3.5:3.4-ea1')")
    slack_p.add_argument("--image-ages", default="", help="Comma-separated component:age_days (e.g. 'dspo:19,feast:14')")
    slack_p.add_argument("--output", default="", help="Output file (default: stdout)")

    args = parser.parse_args()
    if args.command == "jira":
        asyncio.run(cmd_jira(args))
    elif args.command == "slack":
        asyncio.run(cmd_slack(args))


if __name__ == "__main__":
    main()
