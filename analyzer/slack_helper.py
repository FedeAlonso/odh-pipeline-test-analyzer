"""
Slack Helper - Data processing and message composition for Slack analysis summaries.

Handles failure classification (repeated vs new), message formatting,
and Jenkins Bot thread parsing. Does NOT make Slack API calls —
the agent uses Slack MCP tools for communication.
"""
import json
import re
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional


DISCLAIMER = "*NOTE: _This is an Agentic-AI generated message. This feature is still WIP_*"
MAX_HISTORICAL_BUILDS = 5
SLACK_CHAR_LIMIT = 40000
TARGET_MESSAGE_LENGTH = 4000


def _normalize_test_name(name: str) -> str:
    name = name.strip().strip('`').strip()
    if '/' in name:
        name = name.rsplit('/', 1)[-1]
    name = re.sub(r'\.cy\.ts$', '', name)
    return name


def _test_root(name: str) -> str:
    """Extract the area keyword from a test name for fuzzy group matching.

    e.g. testSchedulePipeline -> pipeline, pipelines -> pipeline,
    createRunDeletePipelineCustomPipMirror -> pipeline
    """
    n = re.sub(r'^(test|create|verify)', '', name, flags=re.IGNORECASE)
    n = re.sub(r's$', '', n.lower())
    return n


def _test_keywords(name: str) -> List[str]:
    """Split a camelCase/PascalCase test name into lowercase keywords.

    e.g. createRunDeletePipelineCustomPipMirror -> [pipeline, custom, mirror, ...]
    Filters out short/generic words.
    """
    stripped = re.sub(r'\.cy\.ts$', '', name)
    parts = re.findall(r'[A-Z][a-z]+|[a-z]+', stripped)
    skip = {'test', 'create', 'run', 'delete', 'verify', 'the', 'and', 'with', 'for', 'can', 'a'}
    return [p.lower() for p in parts if len(p) >= 4 and p.lower() not in skip]


def _ts_to_datetime(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def parse_jenkins_bot_message(message_text: str) -> Optional[Dict[str, Any]]:
    """Parse a Jenkins Bot message to extract build number and platform."""
    match = re.search(r'(RHOAI|ODH)[^#]*#(\d+)', message_text)
    if not match:
        return None
    return {
        "platform": match.group(1),
        "build_number": int(match.group(2)),
    }


def match_jenkins_bot_message(
    messages: List[str],
    build_number: int,
    platform: str,
) -> Optional[Dict[str, Any]]:
    """Find the Jenkins Bot message matching a specific build and platform."""
    for msg in messages:
        ts_match = re.match(r'\[(\d+\.\d+)\]', msg)
        if not ts_match:
            continue
        parsed = parse_jenkins_bot_message(msg)
        if parsed and parsed["build_number"] == build_number and parsed["platform"].upper() == platform.upper():
            return {
                "thread_ts": ts_match.group(1),
                "build_number": build_number,
                "platform": platform,
                "text": msg,
            }
    return None


def build_historical_context(
    messages: List[str],
    current_build_number: int,
    platform: str,
    max_builds: int = MAX_HISTORICAL_BUILDS,
) -> List[Dict[str, Any]]:
    """Identify previous Jenkins Bot messages for historical comparison."""
    builds = []
    for msg in messages:
        ts_match = re.match(r'\[(\d+\.\d+)\]', msg)
        if not ts_match:
            continue
        parsed = parse_jenkins_bot_message(msg)
        if not parsed:
            continue
        if parsed["platform"].upper() != platform.upper():
            continue
        if parsed["build_number"] >= current_build_number:
            continue
        builds.append({
            "build_number": parsed["build_number"],
            "platform": parsed["platform"],
            "thread_ts": ts_match.group(1),
        })

    builds.sort(key=lambda b: b["build_number"], reverse=True)
    return builds[:max_builds]


def parse_thread_failures(thread_messages: List[str]) -> List[str]:
    """Extract test failure names from thread reply messages."""
    failures = set()
    cy_pattern = re.compile(r'`?(\w+\.cy\.ts)`?')
    bullet_pattern = re.compile(r'[•◦\-\*]\s+`?(\w+(?:\.cy\.ts)?)`?')
    bare_test_pattern = re.compile(r'(?:^|\s|`)(test[A-Z]\w+|create\w+Pipeline\w+|pipelines)(?:\s|`|$|\b)')

    for msg in thread_messages:
        for match in cy_pattern.finditer(msg):
            failures.add(_normalize_test_name(match.group(1)))
        for match in bullet_pattern.finditer(msg):
            name = match.group(1)
            if re.match(r'^test[A-Z]|^create|^pipeline|^workbench|^storage', name):
                failures.add(_normalize_test_name(name))
        for match in bare_test_pattern.finditer(msg):
            failures.add(_normalize_test_name(match.group(1)))

    return sorted(failures)


def extract_failure_durations(thread_messages: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Extract "broken N days" annotations per test from thread messages.

    Returns:
        {normalized_test_name: {"broken_days": int, "message_ts": float}}
    """
    durations = {}
    # Match patterns like "testName broken 5 days" or "testName.cy.ts broken 4 days"
    # Require test-like prefix to avoid matching random words before "broken"
    broken_pattern = re.compile(
        r'[`]?(test\w+(?:\.cy\.ts)?)[`]?\s+.*?broken\s+(\d+)\s+days?',
        re.IGNORECASE,
    )
    ts_pattern = re.compile(r'\[(\d+\.\d+)\]')

    for msg in thread_messages:
        ts_match = ts_pattern.match(msg)
        if not ts_match:
            continue
        msg_ts = float(ts_match.group(1))
        for match in broken_pattern.finditer(msg):
            name = _normalize_test_name(match.group(1))
            days = int(match.group(2))
            if name not in durations or days > durations[name]["broken_days"]:
                durations[name] = {"broken_days": days, "message_ts": msg_ts}

    return durations


def _is_bulk_failure_list(text: str) -> bool:
    """Return True if the message is just a list of test names, not investigation context."""
    cy_count = len(re.findall(r'\w+\.cy\.ts', text))
    if cy_count >= 3:
        return True
    if re.match(r'(?:failing|failed)\s+tests?\s*:', text, re.IGNORECASE):
        return True
    return False


def extract_investigation_context(
    thread_messages: List[str],
) -> List[Dict[str, Any]]:
    """Extract investigation notes, PR links, and Jira references from thread replies."""
    context = []
    author_pattern = re.compile(r'\[\d+\.\d+\]\s+@([^:]+):\s*(.*)', re.DOTALL)
    jira_pattern = re.compile(r'(RHOAIENG-\d+)', re.IGNORECASE)
    pr_pattern = re.compile(r'(github\.com/[^\s>|]+/pull/\d+)', re.IGNORECASE)
    relevant_patterns = [
        pr_pattern,
        jira_pattern,
        re.compile(r'\b(fix|PR|cherry.?pick|backport|investigating|broken|failing)\b', re.IGNORECASE),
    ]

    for msg in thread_messages:
        match = author_pattern.match(msg)
        if not match:
            continue
        author = match.group(1).strip()
        text = match.group(2).strip()
        if _is_bulk_failure_list(text):
            continue
        if re.search(r'Full analysis report attached to\s+RHOAIENG-', text, re.IGNORECASE):
            continue
        if re.match(r'^\*?NOTE:\s*_This is an Agentic-AI generated', text):
            continue
        if any(p.search(text) for p in relevant_patterns):
            jira_refs = jira_pattern.findall(text)
            pr_refs = [f"https://{u}" if not u.startswith("http") else u
                       for u in pr_pattern.findall(text)]
            context.append({
                "author": author,
                "text": text,
                "jira_refs": jira_refs,
                "pr_refs": pr_refs,
            })

    return context


def classify_failures(
    current_failures: List[str],
    historical_builds: List[Dict[str, Any]],
    jira_issues: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """
    Classify current failures as repeated or new.

    Args:
        current_failures: Test names failing in current build.
        historical_builds: List of {"build_number": int, "failed_test_names": list[str],
                           "investigation_notes": list[dict],
                           "failure_durations": dict (from extract_failure_durations),
                           "thread_ts": str} ordered most-recent-first.
        jira_issues: Mapping of test_name -> list of Jira issue dicts.

    Returns:
        {"repeated": [...], "new": [...]}
    """
    jira_issues = jira_issues or {}
    normalized_current = {_normalize_test_name(f): f for f in current_failures}
    now = datetime.now(tz=timezone.utc)

    history_by_build = []
    for build in historical_builds:
        normalized = {_normalize_test_name(f) for f in build.get("failed_test_names", [])}
        history_by_build.append({
            "build_number": build["build_number"],
            "failures": normalized,
            "investigation_notes": build.get("investigation_notes", []),
            "failure_durations": build.get("failure_durations", {}),
            "thread_ts": build.get("thread_ts", ""),
        })

    repeated = []
    new = []

    def _matches_failure(norm, root, failures):
        if norm in failures:
            return True
        if len(root) >= 4:
            return any(root in _test_root(f) or _test_root(f) in root for f in failures)
        return False

    for norm_name, orig_name in normalized_current.items():
        current_root = _test_root(norm_name)

        # Classify as "repeated" if the test failed in 2+ of the historical
        # builds, even if there are gaps (e.g. fixed in one build then broke again).
        matching_builds = [
            h for h in history_by_build
            if _matches_failure(norm_name, current_root, h["failures"])
        ]

        if len(matching_builds) < 2:
            new.append({
                "test_name": orig_name,
                "jira_issues": jira_issues.get(orig_name, []),
            })
            continue

        consecutive_builds = matching_builds

        # Calculate days broken from the oldest build in the consecutive streak
        days_broken = None
        for h in reversed(consecutive_builds):
            durations = h.get("failure_durations", {})
            d = durations.get(norm_name)
            if not d and len(current_root) >= 4:
                for dk, dv in durations.items():
                    dk_root = _test_root(dk)
                    if current_root in dk_root or dk_root in current_root:
                        d = dv
                        break
            if d:
                thread_date = _ts_to_datetime(str(d["message_ts"]))
                elapsed = (now - thread_date).days
                days_broken = d["broken_days"] + elapsed
                break

        if days_broken is None:
            oldest = consecutive_builds[-1]
            if oldest.get("thread_ts"):
                thread_date = _ts_to_datetime(oldest["thread_ts"])
                days_broken = (now - thread_date).days

        if days_broken is not None and days_broken < 1:
            days_broken = 1

        notes = []
        search_terms = {norm_name.lower()}
        search_terms.update(_test_keywords(norm_name))

        for h in consecutive_builds:
            for note in h.get("investigation_notes", []):
                text_lower = note.get("text", "").lower()
                if norm_name.lower() in text_lower:
                    notes.append({**note, "_match_type": "exact"})

        if not notes:
            for h in consecutive_builds:
                for note in h.get("investigation_notes", []):
                    text_lower = note.get("text", "").lower()
                    if any(t in text_lower for t in search_terms):
                        notes.append({**note, "_match_type": "keyword"})

        repeated.append({
            "test_name": orig_name,
            "days_broken": days_broken,
            "seen_in_builds": [h["build_number"] for h in consecutive_builds],
            "jira_issues": jira_issues.get(orig_name, []),
            "investigation_notes": notes,
        })

    repeated.sort(key=lambda r: r.get("days_broken") or 0, reverse=True)
    new.sort(key=lambda n: n["test_name"])

    build_thread_map = {h["build_number"]: h["thread_ts"] for h in history_by_build if h.get("thread_ts")}

    return {"repeated": repeated, "new": new, "_build_thread_map": build_thread_map}


_pr_title_cache: Dict[str, Optional[str]] = {}


def _fetch_pr_title(pr_url: str) -> Optional[str]:
    """Fetch PR title from GitHub API via gh CLI. Returns None on failure."""
    if pr_url in _pr_title_cache:
        return _pr_title_cache[pr_url]
    match = re.match(r'https?://github\.com/([^/]+/[^/]+)/pull/(\d+)', pr_url)
    if not match:
        _pr_title_cache[pr_url] = None
        return None
    repo, number = match.group(1), match.group(2)
    try:
        result = subprocess.run(
            ['gh', 'api', f'repos/{repo}/pulls/{number}', '--jq', '.title'],
            capture_output=True, text=True, timeout=10,
        )
        title = result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        title = None
    _pr_title_cache[pr_url] = title
    return title


def _is_pr_related_to_test(pr_url: str, test_name: str) -> bool:
    """Check if a PR is related to a test by matching the PR title against test keywords."""
    title = _fetch_pr_title(pr_url)
    if title is None:
        return True
    title_lower = title.lower()
    if test_name.lower() in title_lower:
        return True
    keywords = _test_keywords(test_name)
    return any(k in title_lower for k in keywords)


def _synthesize_notes(
    test_name: str,
    notes: List[Dict[str, Any]],
    jira_statuses: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    all_jira_refs: List[str] = []
    all_pr_refs: List[str] = []
    context_parts: List[str] = []

    for note in notes:
        text = note.get("text", "")
        text = re.sub(
            r'^(?:Test[:\s]+)?[`]?' + re.escape(test_name) + r'[`]?\s*[:\-—.]?\s*',
            '', text, flags=re.IGNORECASE,
        ).strip()
        if re.match(r'^same\s+(?:issues?|errors?|failures?)\s+as\s+(?:yesterday|before|last)', text, re.IGNORECASE):
            continue
        text = re.sub(r'(?:PR\s+)?https?://\S+(?:\s+(?:might|should|could|will|would)\s+\w+\s+it)?', '', text).strip()
        text = re.sub(r'RHOAIENG-\d+\s*(?:created|tracks?\s+(?:the\s+)?(?:fix|issue|bug))?\.?\s*', '', text).strip()
        text = re.sub(r'\b(?:See|see|check|Check)\s+for\s+(?:details|more\s+info|context)\.?', '', text).strip()
        text = re.sub(r'\s{2,}', ' ', text).strip()
        text = re.sub(r'[\s.,;:—\-]+$', '', text).strip()

        if text and len(text) > 15:
            context_parts.append((note.get("author", ""), text))

        all_jira_refs.extend(note.get("jira_refs", []))
        if note.get("_match_type") != "keyword":
            all_pr_refs.extend(note.get("pr_refs", []))

    seen_jiras: set = set()
    unique_jiras: List[str] = []
    for j in all_jira_refs:
        if j not in seen_jiras:
            seen_jiras.add(j)
            unique_jiras.append(j)
    seen_prs: set = set()
    unique_prs: List[str] = []
    for p in all_pr_refs:
        if p not in seen_prs:
            seen_prs.add(p)
            if _is_pr_related_to_test(p, test_name):
                unique_prs.append(p)

    for jref in unique_jiras:
        jstatus = jira_statuses.get(jref, {})
        for pr in jstatus.get("pr_links", []):
            if pr not in seen_prs:
                seen_prs.add(pr)
                unique_prs.append(pr)

    summary = ""
    if context_parts:
        author, text = max(context_parts, key=lambda x: len(x[1]))
        text = text[:200] + "..." if len(text) > 200 else text
        summary = f"_{author}: {text}_" if author else text

    return {"summary": summary, "jira_refs": unique_jiras, "pr_refs": unique_prs}


def _slack_thread_link(channel_id: str, thread_ts: str) -> str:
    """Build a Slack message permalink from channel ID and thread timestamp."""
    ts_no_dot = thread_ts.replace(".", "")
    return f"https://redhat-internal.slack.com/archives/{channel_id}/p{ts_no_dot}"


def compose_slack_message(
    analysis: Dict[str, Any],
    classified: Dict[str, Any],
    flaky_tests: Optional[List[str]] = None,
    jira_statuses: Optional[Dict[str, Dict[str, Any]]] = None,
    image_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
    channel_id: Optional[str] = None,
) -> str:
    """
    Compose a Slack mrkdwn-formatted message for the Jenkins Bot thread.

    Args:
        analysis: Build analysis results with keys: build_number, platform,
                  total_tests, passed_tests, failed_tests, jira_ticket_key,
                  jira_ticket_url, cluster_health, pipeline_failure,
                  rhoai_build_notification_url (optional).
        classified: Output from classify_failures().
        flaky_tests: List of test names that passed on retry.
        jira_statuses: {jira_key: {"status": str, "summary": str, "latest_comment": str}}
        image_metadata: Tracer metadata per image type (operator_bundle, dashboard, fbc_fragment, iib).
        channel_id: Slack channel ID for building message permalinks to previous build threads.
    """
    flaky_tests = flaky_tests or []
    jira_statuses = jira_statuses or {}
    lines = [DISCLAIMER, ""]

    ticket_key = analysis.get("jira_ticket_key")
    ticket_url = analysis.get("jira_ticket_url")
    if ticket_key and ticket_url:
        lines.append(f":jira: Full analysis report and artifacts attached to <{ticket_url}|{ticket_key}>")
    elif ticket_key:
        url = f"https://redhat.atlassian.net/browse/{ticket_key}"
        lines.append(f":jira: Full analysis report and artifacts attached to <{url}|{ticket_key}>")
    lines.append("")

    vm = analysis.get("version_mismatch")
    if vm and vm.get("has_mismatch"):
        lines.append(f":rotating_light::rotating_light: *VERSION MISMATCH — TEST RESULTS MAY BE UNRELIABLE* :rotating_light::rotating_light:")
        lines.append(f"Expected `{vm['expected_version']}` (FBC fragment) but operator installed is `{vm['installed_version']}`. Tests are running against the wrong operator version. Failures below may be caused by this mismatch rather than real bugs.")
        lines.append("")

    total = analysis.get("total_tests", 0)
    passed = analysis.get("passed_tests", 0)
    failed = analysis.get("failed_tests", 0)
    flaky_count = len(flaky_tests)
    lines.append(":bar_chart: *Overall Stats*")
    lines.append(f"• *Total:* {total} | *Passed:* {passed} | *Failed:* {failed} | *Flaky:* {flaky_count}")

    cluster = analysis.get("cluster_health")
    if cluster:
        total_pods = cluster.get("total_pods", 0)
        running = cluster.get("running", 0)
        failed_pods = cluster.get("failed", 0)
        status = "Healthy" if failed_pods == 0 else f"Degraded ({failed_pods} failed pods)"
        lines.append(f"• *Cluster:* {status} ({running}/{total_pods} pods running)")

    pipeline = analysis.get("pipeline_failure")
    if pipeline:
        step = pipeline.get("failed_step", "unknown")
        error = pipeline.get("exception_type", "unknown error")
        lines.append(f"• *Pipeline failure:* `{step}` — `{error}` (post-build, did not affect test execution)")

    image_ages = analysis.get("cluster_image_ages")
    if image_ages:
        old = [i for i in image_ages if i.get('age_days') is not None and i['age_days'] > 7]
        fresh = [i for i in image_ages if i.get('age_days') is not None and i['age_days'] <= 7]
        if old:
            lines.append(f":package: *Image Ages* ({len(fresh)} fresh, {len(old)} stale)")
            for img in sorted(old, key=lambda x: -(x.get('age_days') or 0)):
                lines.append(f"   • `{img['component']}` — {img['age_str']} :warning:")
        else:
            lines.append(f":package: *Image Ages:* all {len(fresh)} images fresh (<7d)")

    if image_metadata:
        deploy_lines = []
        op_meta = image_metadata.get("operator_bundle", {})
        if op_meta and op_meta.get("full_image_uri"):
            sha = op_meta["full_image_uri"]
            short_sha = sha.split("@")[-1][:19] if "@" in sha else sha.split(":")[-1][:19]
            parts = [f"`{short_sha}`"]
            if op_meta.get("build_date"):
                parts.append(f"Built: {op_meta['build_date']}")
            if op_meta.get("rhoai_version"):
                parts.append(f"RHOAI {op_meta['rhoai_version']}")
            notif_url = analysis.get("rhoai_build_notification_url")
            if notif_url:
                parts.append(f"<{notif_url}|build notification>")
            elif analysis.get("platform", "").upper() == "RHOAI":
                parts.append("_not found in #rhoai-build-notifications_")
            deploy_lines.append(f"• *Operator:* {' | '.join(parts)}")

        dash_meta = image_metadata.get("dashboard", {})
        if dash_meta:
            commit = (dash_meta.get("commit_sha_full") or "")[:12]
            url = dash_meta.get("commit_url", "")
            if commit and url:
                deploy_lines.append(f"• *Dashboard:* commit <{url}|`{commit}`>")
            elif commit:
                deploy_lines.append(f"• *Dashboard:* commit `{commit}`")

        fbc_meta = image_metadata.get("fbc_fragment", {})
        if fbc_meta and fbc_meta.get("full_image_uri"):
            fbc_uri = fbc_meta["full_image_uri"]
            short = fbc_uri.split("/")[-1] if "/" in fbc_uri else fbc_uri
            deploy_lines.append(f"• *FBC Fragment:* `{short}`")

        if deploy_lines:
            lines.append(":gear: *Deployment Info*")
            lines.extend(deploy_lines)

    lines.append("")

    repeated = classified.get("repeated", [])
    if repeated:
        lines.append(":rotating_light: *Repeated Failures*")
        lines.append("")
        for f in repeated:
            name = f["test_name"]
            days = f.get("days_broken")
            jiras = f.get("jira_issues", [])
            notes = f.get("investigation_notes", [])
            seen_in = f.get("seen_in_builds", [])

            line = f"• `{name}`"
            if days is not None:
                line += f"\n        Note - this is now broken {days} days :warning:"

            if seen_in and channel_id:
                build_links = []
                for b in classified.get("_build_thread_map", {}).items():
                    bnum, bts = b
                    if bnum in seen_in:
                        link = _slack_thread_link(channel_id, bts)
                        build_links.append(f"<{link}|#{bnum}>")
                if build_links:
                    line += f"\n    :speech_balloon: Previous: {', '.join(build_links)}"

            for jira in jiras[:2]:
                key = jira.get("key", "")
                url = jira.get("url", f"https://redhat.atlassian.net/browse/{key}")
                summary = jira.get("summary", "")
                if key:
                    line += f"\n    :jira: <{url}|{key}> — {summary}"

            synth = _synthesize_notes(name, notes, jira_statuses)
            if synth["summary"]:
                line += f"\n    {synth['summary']}"
            for jref in synth["jira_refs"]:
                jstatus = jira_statuses.get(jref, {})
                if jstatus.get("summary", "").startswith("Nightly Analysis:"):
                    continue
                url = f"https://redhat.atlassian.net/browse/{jref}"
                status_text = f" — *{jstatus['status']}*" if jstatus.get("status") else ""
                link_text = f"{jref} — {jstatus['summary']}" if jstatus.get("summary") else jref
                line += f"\n    :jira: <{url}|{link_text}>{status_text}"
            for pr in synth["pr_refs"]:
                pr_short = pr.split("github.com/")[-1] if "github.com/" in pr else pr
                line += f"\n    :github: <{pr}|{pr_short}>"

            lines.append(line)
            lines.append("")

    new = classified.get("new", [])
    if new:
        lines.append(":new: *New Failures*")
        lines.append("")
        for f in new:
            name = f["test_name"]
            jiras = f.get("jira_issues", [])
            line = f"• `{name}`"
            for jira in jiras[:2]:
                key = jira.get("key", "")
                url = jira.get("url", f"https://redhat.atlassian.net/browse/{key}")
                summary = jira.get("summary", "")
                if key:
                    line += f" — :jira: <{url}|{key}> ({summary})"
            lines.append(line)
        lines.append("")

    if not repeated and not new:
        lines.append(":new: *New Failures*")
        lines.append("None — all failures are recurring from previous builds.")
        lines.append("")

    if flaky_tests:
        lines.append(":recycle: *Flaky Tests (passed on retry)*")
        lines.append(", ".join(f"`{t}`" for t in flaky_tests))
        lines.append("")

    message = "\n".join(lines)
    if len(message) > SLACK_CHAR_LIMIT:
        message = message[:SLACK_CHAR_LIMIT - 20] + "\n... (truncated)"
    return message


def collect_jira_keys(thread_data: Dict[str, List[str]]) -> List[str]:
    """Extract all unique Jira ticket keys referenced across thread messages."""
    jira_pattern = re.compile(r'(RHOAIENG-\d+)', re.IGNORECASE)
    keys = set()
    for messages in thread_data.values():
        for msg in messages:
            keys.update(jira_pattern.findall(msg))
    return sorted(keys)


def prepare_slack_message(
    search_results: List[str],
    thread_data: Dict[str, List[str]],
    build_number: int,
    platform: str,
    current_failures: List[str],
    flaky_tests: List[str],
    analysis: Dict[str, Any],
    jira_statuses: Optional[Dict[str, Dict[str, Any]]] = None,
    image_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    End-to-end: takes raw MCP data and returns a ready-to-post Slack message.

    Args:
        search_results: Raw messages from mcp__slack__search_messages.
        thread_data: Mapping of thread_ts -> list of thread messages from mcp__slack__get_thread.
        build_number: Current build number.
        platform: "RHOAI" or "ODH".
        current_failures: Test names that failed (real failures, not flaky).
        flaky_tests: Test names that passed on retry.
        analysis: Dict with keys: total_tests, passed_tests, failed_tests,
                  jira_ticket_key, jira_ticket_url, cluster_health, pipeline_failure,
                  rhoai_build_notification_url (optional).
        jira_statuses: {jira_key: {"status": str, "summary": str, "latest_comment": str}}
                       from jira_lock.fetch_jira_statuses().
        image_metadata: Tracer metadata per image type from comprehensive_analysis.
        channel_id: Slack channel ID for building message permalinks.

    Returns:
        {"thread_ts": str or None, "message": str} — ready to pass to mcp__slack__post_message.
    """
    current_match = match_jenkins_bot_message(search_results, build_number, platform)
    thread_ts = current_match["thread_ts"] if current_match else None

    previous = build_historical_context(search_results, build_number, platform)

    historical_builds = []
    for build in previous:
        ts = build["thread_ts"]
        messages = thread_data.get(ts, [])
        failures = parse_thread_failures(messages)
        notes = extract_investigation_context(messages)
        durations = extract_failure_durations(messages)
        historical_builds.append({
            "build_number": build["build_number"],
            "failed_test_names": failures,
            "investigation_notes": notes,
            "failure_durations": durations,
            "thread_ts": ts,
        })

    classified = classify_failures(current_failures, historical_builds)

    analysis["build_number"] = build_number
    analysis["platform"] = platform
    message = compose_slack_message(analysis, classified, flaky_tests, jira_statuses, image_metadata, channel_id)

    return {"thread_ts": thread_ts, "message": message}
