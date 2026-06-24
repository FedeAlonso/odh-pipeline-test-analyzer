"""
Jira Lock - Create/search analysis lock tickets to prevent duplicate runs.

Uses the same auth as jira_client.py: Basic Auth (JIRA_USER + JIRA_TOKEN).
"""
import os
import sys
from datetime import datetime
from typing import Optional, Dict, Any, Tuple

import httpx

from .config import Config

_GIT_PR_NOT_APPLICABLE = {
    "type": "doc", "version": 1,
    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Not Applicable"}]}],
}


def _auth():
    if Config.JIRA_USER:
        return httpx.BasicAuth(Config.JIRA_USER, Config.JIRA_TOKEN)
    return None


def _headers():
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if not Config.JIRA_USER:
        headers["Authorization"] = f"Bearer {Config.JIRA_TOKEN}"
    return headers


async def search_lock_ticket(
    build_num: int,
    platform: str,
    project: str = "RHOAIENG",
) -> Optional[Dict[str, Any]]:
    base_url = Config.JIRA_URL.rstrip('/')
    expected_prefix = f"Nightly Analysis: {build_num}-{platform}"
    jql = f'project = {project} AND summary ~ "Nightly Analysis" ORDER BY created DESC'

    async with httpx.AsyncClient(verify=Config.SSL_VERIFY, timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/rest/api/3/search/jql",
            headers=_headers(),
            auth=_auth(),
            json={"jql": jql, "maxResults": 10, "fields": ["summary", "status", "created"]},
        )
        response.raise_for_status()
        issues = response.json().get("issues", [])

        for issue in issues:
            summary = issue["fields"]["summary"]
            if summary.startswith(expected_prefix):
                return {
                    "key": issue["key"],
                    "summary": summary,
                    "url": f"{base_url}/browse/{issue['key']}",
                }
        return None


async def create_lock_ticket(
    build_num: int,
    platform: str,
    build_date_str: str,
    project: str = "RHOAIENG",
) -> Optional[Dict[str, Any]]:
    base_url = Config.JIRA_URL.rstrip('/')
    summary = f"Nightly Analysis: {build_num}-{platform}-{build_date_str}"
    description_text = (
        f"Automated nightly analysis started by "
        f"{os.getenv('USER', 'unknown')} on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.\n\n"
        f"Build: #{build_num}\nPlatform: {platform}"
    )

    payload = {
        "fields": {
            "project": {"key": project},
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [{"type": "paragraph", "content": [{"type": "text", "text": description_text}]}],
            },
            "issuetype": {"name": "Task"},
            "labels": ["nightly-analysis"],
            "customfield_10875": _GIT_PR_NOT_APPLICABLE,
        }
    }

    async with httpx.AsyncClient(verify=Config.SSL_VERIFY, timeout=30.0) as client:
        response = await client.post(
            f"{base_url}/rest/api/3/issue",
            headers=_headers(),
            auth=_auth(),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "key": data["key"],
            "url": f"{base_url}/browse/{data['key']}",
            "summary": summary,
        }


async def _close_ticket(issue_key: str) -> bool:
    base_url = Config.JIRA_URL.rstrip('/')
    async with httpx.AsyncClient(verify=Config.SSL_VERIFY, timeout=30.0) as client:
        await client.put(
            f"{base_url}/rest/api/3/issue/{issue_key}",
            headers=_headers(),
            auth=_auth(),
            json={"fields": {"customfield_10875": _GIT_PR_NOT_APPLICABLE}},
        )

        resp = await client.get(
            f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
            headers=_headers(),
            auth=_auth(),
        )
        resp.raise_for_status()
        transitions = resp.json().get("transitions", [])

        close_id = None
        for t in transitions:
            if t["name"].lower() in ("done", "closed", "resolved"):
                close_id = t["id"]
                break
        if not close_id:
            return False

        resp = await client.post(
            f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
            headers=_headers(),
            auth=_auth(),
            json={"transition": {"id": close_id}},
        )
        resp.raise_for_status()
        return True


async def check_or_create_lock(
    build_num: int,
    platform: str,
    build_date_str: str,
    auto_yes: bool = False,
) -> Tuple[bool, Optional[str]]:
    if not Config.JIRA_TOKEN:
        return (True, None)

    project = Config.JIRA_LOCK_PROJECT

    existing = await search_lock_ticket(build_num, platform, project)
    if existing:
        print(f"\n⚠️  Analysis ticket already exists: {existing['key']}")
        print(f"   {existing['summary']}")
        print(f"   {existing['url']}")
        print(f"   Someone may already be running this analysis.\n")
        if auto_yes or not sys.stdin.isatty():
            print("Auto-accepting existing ticket (non-interactive mode)")
        else:
            try:
                answer = input("Continue and publish to existing ticket? (y/n): ").strip().lower()
            except EOFError:
                answer = "y"
            if answer != "y":
                print("Cancelled.")
                sys.exit(0)
        return (True, existing["key"])

    created = await create_lock_ticket(build_num, platform, build_date_str, project)
    if created:
        try:
            await _close_ticket(created["key"])
        except Exception:
            pass
        print(f"✅ Created analysis lock ticket: {created['key']} ({created['url']})")
        return (True, created["key"])

    print("⚠️  Could not create lock ticket. Continuing without Jira lock.")
    return (True, None)


def _markdown_to_adf(markdown: str) -> Dict[str, Any]:
    """Convert markdown text to minimal ADF for Jira comments."""
    content = []
    for line in markdown.split('\n'):
        if not line.strip():
            continue
        if line.startswith('# '):
            content.append({"type": "heading", "attrs": {"level": 1},
                            "content": [{"type": "text", "text": line[2:].strip()}]})
        elif line.startswith('## '):
            content.append({"type": "heading", "attrs": {"level": 2},
                            "content": [{"type": "text", "text": line[3:].strip()}]})
        elif line.startswith('### '):
            content.append({"type": "heading", "attrs": {"level": 3},
                            "content": [{"type": "text", "text": line[4:].strip()}]})
        elif line.startswith('- ') or line.startswith('* '):
            content.append({"type": "bulletList", "content": [
                {"type": "listItem", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": line[2:].strip()}]}
                ]}
            ]})
        else:
            content.append({"type": "paragraph",
                            "content": [{"type": "text", "text": line}]})
    return {"type": "doc", "version": 1, "content": content or [
        {"type": "paragraph", "content": [{"type": "text", "text": "(empty)"}]}
    ]}


async def publish_results(
    issue_key: str,
    build_num: int,
    platform: str,
    total_tests: int,
    passed_tests: int,
    failed_tests: int,
    num_retries_passed: int,
    pipeline_failure: Dict[str, Any],
    failure_names: list,
    md_report_path: str,
    html_report_path: str,
    version_mismatch: Dict[str, Any] = None,
) -> bool:
    """Publish analysis results to the Jira lock ticket: comment + attachments."""
    base_url = Config.JIRA_URL.rstrip('/')

    # Build summary comment
    status_icon = "✅" if failed_tests == 0 else "❌"
    lines = [
        f"## {status_icon} Analysis Complete — Build #{build_num} ({platform})",
        "",
        f"- **Total tests:** {total_tests}",
        f"- **Passed:** {passed_tests}",
        f"- **Failed:** {failed_tests}",
    ]
    if num_retries_passed > 0:
        lines.append(f"- **Flaky (passed on retry):** {num_retries_passed}")

    if pipeline_failure.get('is_deployment_failure'):
        lines.extend([
            "",
            f"### Pipeline Failure",
            f"- **Failed step:** {pipeline_failure.get('failed_step', 'Unknown')}",
            f"- **Error:** {(pipeline_failure.get('exception_message') or pipeline_failure.get('error_text') or 'N/A')[:200]}",
        ])

    if version_mismatch and version_mismatch.get('has_mismatch'):
        lines.extend([
            "",
            f"### 🚨 Version Mismatch",
            f"- **Expected (FBC fragment):** {version_mismatch['expected_version']}",
            f"- **Installed (operator CSV):** {version_mismatch['installed_version']}",
        ])

    if failure_names:
        lines.extend(["", "### Failed Tests"])
        for name in failure_names[:20]:
            lines.append(f"- {name}")
        if len(failure_names) > 20:
            lines.append(f"- ... and {len(failure_names) - 20} more")

    comment_adf = _markdown_to_adf("\n".join(lines))

    try:
        async with httpx.AsyncClient(verify=Config.SSL_VERIFY, timeout=60.0) as client:
            # Post comment
            resp = await client.post(
                f"{base_url}/rest/api/3/issue/{issue_key}/comment",
                headers=_headers(),
                auth=_auth(),
                json={"body": comment_adf},
            )
            if resp.status_code in (200, 201):
                print(f"   ✅ Summary comment posted to {issue_key}")
            else:
                print(f"   ⚠️  Comment failed: {resp.status_code}")

            # Attach files
            for filepath in [md_report_path, html_report_path]:
                if not os.path.exists(filepath):
                    continue
                filename = os.path.basename(filepath)
                with open(filepath, 'rb') as f:
                    resp = await client.post(
                        f"{base_url}/rest/api/3/issue/{issue_key}/attachments",
                        auth=_auth(),
                        headers={"X-Atlassian-Token": "no-check", "Accept": "application/json"},
                        files={"file": (filename, f)},
                    )
                if resp.status_code in (200, 201):
                    print(f"   ✅ Attached {filename}")
                else:
                    print(f"   ⚠️  Attach {filename} failed: {resp.status_code}")

        return True
    except Exception as e:
        print(f"   ⚠️  Failed to publish results to Jira: {e}")
        return False


async def fetch_jira_statuses(keys: list) -> Dict[str, Dict[str, Any]]:
    """Fetch current status and latest comment for a list of Jira ticket keys.

    Returns:
        {key: {"status": str, "summary": str, "latest_comment": str}}
    """
    if not keys or not Config.JIRA_TOKEN:
        return {}

    base_url = Config.JIRA_URL.rstrip('/')
    result = {}
    try:
        async with httpx.AsyncClient(verify=Config.SSL_VERIFY, timeout=30.0) as client:
            for key in keys:
                try:
                    resp = await client.get(
                        f"{base_url}/rest/api/3/issue/{key}",
                        headers=_headers(),
                        auth=_auth(),
                        params={"fields": "summary,status,comment"},
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    status = data["fields"]["status"]["name"]
                    summary = data["fields"]["summary"]
                    comments = data["fields"].get("comment", {}).get("comments", [])
                    latest = ""
                    if comments:
                        body = comments[-1].get("body", {})
                        content = body.get("content", [])
                        if content and content[0].get("content"):
                            latest = content[0]["content"][0].get("text", "")[:200]
                    pr_links = []
                    try:
                        rl_resp = await client.get(
                            f"{base_url}/rest/api/3/issue/{key}/remotelink",
                            headers=_headers(),
                            auth=_auth(),
                        )
                        if rl_resp.status_code == 200:
                            for link in rl_resp.json():
                                url = link.get("object", {}).get("url", "")
                                if "github.com/" in url and "/pull/" in url:
                                    pr_links.append(url)
                    except Exception:
                        pass
                    result[key] = {
                        "status": status,
                        "summary": summary,
                        "latest_comment": latest,
                        "pr_links": pr_links,
                    }
                except Exception:
                    continue
    except Exception as e:
        print(f"   ⚠️  Failed to fetch Jira statuses: {e}")
    return result


def compose_agent_summary(
    build_num: int,
    platform: str,
    total_tests: int,
    passed_tests: int,
    failed_tests: int,
    real_failures: list,
    flaky_tests: list,
    cluster_health: Dict[str, Any],
    pipeline_failure: Dict[str, Any],
) -> str:
    """Generate a structured agent analysis summary in markdown."""
    pct = f"{passed_tests / total_tests * 100:.1f}" if total_tests else "0"
    lines = [
        f"## Agent Analysis Summary — Build #{build_num} ({platform})",
        "",
        "### Overall Stats",
        f"- **Total tests:** {total_tests}",
        f"- **Passed:** {passed_tests} ({pct}%)",
        f"- **Failed:** {failed_tests} (real failures)",
        f"- **Flaky (passed on retry):** {len(flaky_tests)}",
    ]

    if cluster_health:
        tp = cluster_health.get("total_pods", 0)
        rn = cluster_health.get("running", 0)
        fl = cluster_health.get("failed", 0)
        status = "Healthy" if fl == 0 else f"Degraded ({fl} failed pods)"
        lines.append(f"- **Cluster:** {status} ({rn}/{tp} pods running)")

    if pipeline_failure:
        step = pipeline_failure.get("failed_step", "unknown")
        err = pipeline_failure.get("exception_type", "unknown")
        lines.append(f"- **Pipeline failure:** {step} — {err} (post-build, did not affect test execution)")

    lines.extend(["", "### Real Failures", ""])
    for f in real_failures:
        name = f.get("name", "unknown")
        error = f.get("error", "")
        category = f.get("category", "")
        jira = f.get("jira", "")
        notes = f.get("notes", "")
        line = f"- **{name}**"
        if error:
            line += f" — {error}"
        if category:
            line += f" [{category}]"
        lines.append(line)
        if jira:
            lines.append(f"  - Jira: {jira}")
        if notes:
            lines.append(f"  - {notes}")

    if flaky_tests:
        lines.extend(["", "### Flaky Tests (passed on retry)"])
        lines.append(", ".join(flaky_tests))

    return "\n".join(lines)


async def post_agent_summary(issue_key: str, markdown: str) -> bool:
    """Post an agent analysis summary comment to a Jira ticket."""
    base_url = Config.JIRA_URL.rstrip('/')
    try:
        async with httpx.AsyncClient(verify=Config.SSL_VERIFY, timeout=60.0) as client:
            resp = await client.post(
                f"{base_url}/rest/api/3/issue/{issue_key}/comment",
                headers=_headers(),
                auth=_auth(),
                json={"body": _markdown_to_adf(markdown)},
            )
            if resp.status_code in (200, 201):
                print(f"   ✅ Agent analysis summary posted to {issue_key}")
                return True
            print(f"   ⚠️  Comment failed: {resp.status_code}")
            return False
    except Exception as e:
        print(f"   ⚠️  Failed to post agent summary: {e}")
        return False
