"""
COMPREHENSIVE ANALYSIS V3 - Sync Detection Edition
Implements all requested improvements:
1. General pipeline failure parser (ANY stage failure)
2. High-level image extraction (FBC fragment, IIB, dashboard)
3. Tracer tool integration for all images
4. Screenshot detection and embedding (including retries)
5. Modern, exciting report template
6. "Tests not executed" vs "tests passed" distinction
7. Dashboard commit sync issue detection (CRITICAL!)
8. Image registry type analysis (production vs development)
9. Prominent sync status warnings in reports
10. Test/code mismatch alerts
"""
import asyncio
import base64
import httpx
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env file if it exists
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

from analyzer import jenkins_client, artifact_parser, failure_analyzer, jira_client, cluster_inspector
from analyzer.config import Config


def extract_all_deployed_images(console_output: str) -> dict:
    """
    Extract ALL deployed images from console output.
    Looks for:
    - FBC fragment: quay.io/rhoai/rhoai-fbc-fragment (with @sha256: digest or :tag)
    - IIB: brew.registry.redhat.io/rh-osbs/iib:XXXXX
    - Dashboard: on quay.io or registry.redhat.io (odh-dashboard-rhel8 or odh-dashboard-rhel9)
    - Operator bundle: on quay.io or registry.redhat.io
    """
    images = {
        'fbc_fragment': None,
        'iib': None,
        'dashboard': None,
        'operator_bundle': None
    }

    if not console_output:
        return images

    # Pattern for images with sha256 digest (quay.io or registry.redhat.io)
    digest_pattern = r'((?:quay\.io|registry\.redhat\.io)/rhoai/[^@\s]+@sha256:[a-f0-9]{64})'
    for match in re.finditer(digest_pattern, console_output):
        image_uri = match.group(1)

        if 'fbc-fragment' in image_uri:
            images['fbc_fragment'] = image_uri
        elif 'odh-dashboard' in image_uri:
            images['dashboard'] = image_uri
        elif 'operator-bundle' in image_uri:
            images['operator_bundle'] = image_uri

    # Pattern for FBC fragment with tag (no digest), e.g. quay.io/rhoai/rhoai-fbc-fragment:rhoai-3.4
    if not images['fbc_fragment']:
        fbc_tag_pattern = r'(quay\.io/rhoai/rhoai-fbc-fragment:[a-zA-Z0-9._-]+)'
        match = re.search(fbc_tag_pattern, console_output)
        if match:
            images['fbc_fragment'] = match.group(1)

    # Pattern for brew IIB images
    iib_pattern = r'(brew\.registry\.redhat\.io/rh-osbs/iib:\d+)'
    match = re.search(iib_pattern, console_output)
    if match:
        images['iib'] = match.group(1)

    return images


def get_image_metadata_with_tracer(image_uri: str) -> dict:
    """
    Use tracer tool to get complete image metadata.
    Returns: build date, commit info, RHOAI version, component commits, etc.
    """
    tracer_path = os.getenv("TRACER_PATH", "/path/to/tracer/tracer.sh")

    metadata = {
        'full_image_uri': image_uri,
        'build_date': None,
        'rhoai_version': None,
        'commit_sha_full': None,
        'commit_url': None,
        'component_commits': {},
        'error': None,
        'raw_output': None
    }

    if not image_uri:
        metadata['error'] = "No image URI provided"
        return metadata

    if not os.path.exists(tracer_path):
        metadata['error'] = f"Tracer tool not found at {tracer_path}"
        return metadata

    try:
        result = subprocess.run(
            ['bash', tracer_path, '-i', image_uri, '-c'],
            capture_output=True,
            text=True,
            timeout=60
        )

        metadata['raw_output'] = result.stdout

        if result.returncode != 0:
            metadata['error'] = f"Tracer failed (exit code {result.returncode}): {result.stderr}"
            return metadata

        for line in result.stdout.strip().split('\n'):
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue

            key = parts[0].strip()
            value = parts[1].strip()

            if key == 'Image-URI':
                metadata['full_image_uri'] = value
            elif key == 'Build-Date':
                metadata['build_date'] = value
            elif key == 'RHOAI-Version':
                metadata['rhoai_version'] = value
            elif '/tree/' in value:
                # Component commit line: name → https://github.com/{owner}/{repo}/tree/{SHA}
                sha = value.split('/tree/')[-1]
                repo_match = re.match(r'https://github\.com/([^/]+)/([^/]+)/tree/', value)
                if repo_match:
                    metadata['component_commits'][key] = {
                        'sha': sha,
                        'url': value,
                        'repo_owner': repo_match.group(1),
                        'repo_name': repo_match.group(2),
                        'commit_date': None,
                    }
                if key == 'odh-dashboard' or key == 'dashboard':
                    metadata['commit_url'] = value
                    metadata['commit_sha_full'] = sha

        return metadata

    except subprocess.TimeoutExpired:
        metadata['error'] = "Tracer command timed out after 60s"
    except Exception as e:
        metadata['error'] = f"Tracer error: {str(e)}"

    return metadata


def fetch_component_commit_dates(component_commits: dict) -> dict:
    """Fetch real git commit dates from GitHub API for all components.
    Deduplicates by repo+SHA to minimize API calls."""
    if not component_commits:
        return component_commits

    # Deduplicate: group by (repo_owner, repo_name, sha)
    unique_lookups = {}
    for name, info in component_commits.items():
        if not info.get('sha') or not info.get('repo_owner'):
            continue
        lookup_key = (info['repo_owner'], info['repo_name'], info['sha'])
        if lookup_key not in unique_lookups:
            unique_lookups[lookup_key] = []
        unique_lookups[lookup_key].append(name)

    print(f"   Fetching commit dates for {len(unique_lookups)} unique repo+SHA pairs...")
    dates_cache = {}
    for (owner, repo, sha), names in unique_lookups.items():
        try:
            result = subprocess.run(
                ['gh', 'api', f'repos/{owner}/{repo}/commits/{sha}',
                 '--jq', '.commit.committer.date'],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0 and result.stdout.strip():
                dates_cache[(owner, repo, sha)] = result.stdout.strip()
        except (subprocess.TimeoutExpired, Exception):
            pass

    # Apply dates back to all components
    for name, info in component_commits.items():
        lookup_key = (info.get('repo_owner'), info.get('repo_name'), info.get('sha'))
        if lookup_key in dates_cache:
            info['commit_date'] = dates_cache[lookup_key]

    dated = sum(1 for c in component_commits.values() if c.get('commit_date'))
    print(f"   ✅ Got dates for {dated}/{len(component_commits)} components")
    return component_commits


async def inspect_cluster_image_ages(inspector, namespaces: list) -> list:
    """Get build dates for all unique images deployed on the cluster via skopeo."""
    seen_images = {}
    for ns in namespaces:
        deployments = await inspector.get_deployments(ns)
        for dep in deployments:
            dep_name = dep.get('metadata', {}).get('name', '')
            containers = dep.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
            for c in containers:
                image = c.get('image', '')
                if not image:
                    continue
                if dep_name not in seen_images:
                    seen_images[dep_name] = {'image': image, 'namespace': ns}

    results = []
    tasks = []
    for dep_name, info in sorted(seen_images.items()):
        tasks.append(_inspect_single_image(dep_name, info['image'], info['namespace']))
    results = await asyncio.gather(*tasks)
    return [r for r in results if r]


async def _inspect_single_image(dep_name: str, image: str, namespace: str) -> dict:
    entry = {
        'component': dep_name,
        'namespace': namespace,
        'image': image,
        'build_date': None,
        'age_days': None,
        'age_str': '',
        'commit': '',
    }
    try:
        proc = await asyncio.create_subprocess_exec(
            'skopeo', 'inspect', '--override-arch', 'amd64', '--override-os', 'linux',
            '--no-tags', f"docker://{image}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            import json as _json
            data = _json.loads(stdout)
            labels = data.get('Labels', {})
            build_date = labels.get('build-date', labels.get('org.opencontainers.image.created', ''))
            vcs_ref = labels.get('vcs-ref', labels.get('org.opencontainers.image.revision', ''))
            if build_date:
                entry['build_date'] = build_date[:19]
                bd = datetime.fromisoformat(build_date.replace('Z', '+00:00').replace('+00:00+00:00', '+00:00'))
                age = datetime.now(bd.tzinfo or None) - bd if bd.tzinfo else datetime.utcnow() - bd
                entry['age_days'] = age.days
                if age.days > 0:
                    entry['age_str'] = f"{age.days}d {age.seconds // 3600}h"
                else:
                    entry['age_str'] = f"{age.seconds // 3600}h {(age.seconds % 3600) // 60}m"
            if vcs_ref:
                entry['commit'] = vcs_ref[:12]
    except Exception:
        pass
    return entry


def extract_expected_version(deployed_images: dict, image_metadata: dict) -> str:
    """Extract the expected RHOAI/ODH version from the fbc_fragment image tag or tracer metadata."""
    fbc_meta = image_metadata.get('fbc_fragment', {})
    if fbc_meta.get('rhoai_version'):
        return fbc_meta['rhoai_version']

    fbc_uri = deployed_images.get('fbc_fragment', '') or ''
    tag_match = re.search(r':rhoai-(\d+\.\d+)', fbc_uri)
    if tag_match:
        return tag_match.group(1)
    return ''


def detect_version_mismatch(expected_version: str, installed_csv_version: str) -> dict:
    """Compare expected version (from fbc_fragment) against installed operator CSV version."""
    result = {
        'has_mismatch': False,
        'expected_version': expected_version,
        'installed_version': installed_csv_version or 'unknown',
        'message': '',
    }
    if not expected_version or not installed_csv_version:
        return result

    expected_normalized = re.sub(r'^v', '', expected_version)
    installed_normalized = re.sub(r'^v', '', installed_csv_version)
    expected_major_minor = re.match(r'(\d+\.\d+)', expected_normalized)
    installed_major_minor = re.match(r'(\d+\.\d+)', installed_normalized)

    if expected_major_minor and installed_major_minor:
        if expected_major_minor.group(1) != installed_major_minor.group(1):
            result['has_mismatch'] = True
            result['message'] = (
                f"FBC fragment targets {expected_version} but operator installed is {installed_csv_version}"
            )
    return result


def parse_cypress_console_results(console_output: str) -> tuple:
    """
    Parse Cypress test results from Jenkins console output.

    Handles multiple parallel Cypress runs, each with its own "Run Finished" table.
    Uses per-run summary lines for accurate totals (spec names in table rows wrap
    across lines, making them unreliable for counting).

    Each run's output is associated with its test stage (e.g. SmokeSet1) by finding
    the closest test-output/{TAG}/e2e/ directory reference before its summary line.

    Returns:
        (parsed_results, results_by_stage) tuple
    """
    # Strip ANSI escape codes and Jenkins timestamps
    clean = re.sub(r'\x1b\[[0-9;]*m', '', console_output)
    clean = re.sub(r'\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\]\s*', '', clean)

    totals = {
        'total_tests': 0,
        'passed_tests': 0,
        'failed_tests': 0,
        'skipped_tests': 0,
        'pending_tests': 0,
        'failures': [],
    }
    results_by_stage = {}

    # Parse per-run summary lines (appear after └───┘ at end of each "Run Finished" table)
    # Failed runs: ✖  X of Y failed (Z%)  HH:MM  Tests  Passing  Failing  Pending  Skipped
    fail_summary = re.compile(
        r'✖\s+\d+\s+of\s+\d+\s+failed\s+\(\d+%\)\s+'
        r'[\d:]+\s+'           # duration
        r'(\d+)\s+'            # Tests
        r'(\d+)\s+'            # Passing
        r'(\d+)\s+'            # Failing
        r'(-|\d+)\s+'          # Pending
        r'(-|\d+)'             # Skipped
    )
    # Passed runs: ✔  All specs passed!  HH:MM  Tests  Passing  Failing  Pending  Skipped
    pass_summary = re.compile(
        r'✔\s+All specs passed!\s+'
        r'[\d:]+\s+'           # duration
        r'(\d+)\s+'            # Tests
        r'(\d+)\s+'            # Passing
        r'(-|\d+)\s+'          # Failing
        r'(-|\d+)\s+'          # Pending
        r'(-|\d+)'             # Skipped
    )

    # Collect all per-run summaries with their position
    # Each entry: (position, tests, passing, failing, pending, skipped)
    run_summaries = []
    for m in fail_summary.finditer(clean):
        run_summaries.append((m.start(),
            int(m.group(1)), int(m.group(2)), int(m.group(3)),
            int(m.group(4)) if m.group(4) != '-' else 0,
            int(m.group(5)) if m.group(5) != '-' else 0))
    for m in pass_summary.finditer(clean):
        run_summaries.append((m.start(),
            int(m.group(1)), int(m.group(2)),
            int(m.group(3)) if m.group(3) != '-' else 0,
            int(m.group(4)) if m.group(4) != '-' else 0,
            int(m.group(5)) if m.group(5) != '-' else 0))
    run_summaries.sort(key=lambda x: x[0])

    # Build index of test-output/{TAG}/e2e/ positions for stage association
    tag_pattern = re.compile(r'test-output/([A-Za-z]+Set\d+)/e2e/')
    tag_positions = [(m.start(), m.group(1)) for m in tag_pattern.finditer(clean)]

    # Associate each run summary with its test stage
    for pos, tests, passing, failing, pending, skipped in run_summaries:
        # Find closest test-output/{TAG} before this summary
        stage = 'unknown'
        for tp, tn in reversed(tag_positions):
            if tp < pos:
                stage = tn
                break

        # Accumulate into per-stage results
        if stage not in results_by_stage:
            results_by_stage[stage] = {
                'total_tests': 0, 'passed_tests': 0, 'failed_tests': 0,
                'skipped_tests': 0, 'failures': []
            }
        sr = results_by_stage[stage]
        sr['total_tests'] += tests
        sr['passed_tests'] += passing
        sr['failed_tests'] += failing
        sr['skipped_tests'] += skipped

        # Accumulate totals
        totals['total_tests'] += tests
        totals['passed_tests'] += passing
        totals['failed_tests'] += failing
        totals['pending_tests'] += pending
        totals['skipped_tests'] += skipped

    # Extract failing spec names from ✖ per-spec rows in the table
    failing_spec_pattern = re.compile(
        r'[│|]\s+✖\s+'
        r'(\S+)'               # spec name (may be truncated)
    )
    for m in failing_spec_pattern.finditer(clean):
        spec_fragment = m.group(1).strip()
        if spec_fragment.endswith('.cy.'):
            spec_fragment += 'ts'
        elif not spec_fragment.endswith('.cy.ts') and not spec_fragment.endswith('.ts'):
            spec_fragment += '*.cy.ts'

        failure = {
            'test': spec_fragment,
            'fullTitle': spec_fragment,
            'suite': os.path.dirname(spec_fragment) or 'root',
            'file': f"cypress/tests/e2e/{spec_fragment}",
            'error': 'Failed (details in console output)',
        }
        totals['failures'].append(failure)

        # Associate failure with the closest stage
        stage = 'unknown'
        for tp, tn in reversed(tag_positions):
            if tp < m.start():
                stage = tn
                break
        if stage in results_by_stage:
            results_by_stage[stage]['failures'].append(failure)

    return totals, results_by_stage


# --- Pipeline stage classification (dashboard-e2e-tests) ---
# Parent/wrapper stages (shown in Jenkins UI as top-level)
PIPELINE_PARENT_STAGES = {
    'Cluster and Operator Setup',
    'Dashboard E2E Tests',
    'Dashboard E2E Tests (Container)',
}

# Stages that run BEFORE tests — failures here mean tests never ran
# Stage Group 1: Cluster & Operator Setup (Kubernetes agent)
# Stage Group 2 pre-test stages (from runDashboardTestStages.groovy)
PIPELINE_INFRA_STAGES = {
    'Install new cluster', 'Setup BYOIDC', 'Generate Test Config File',
    'Deploy external DNS', 'Add ICSP', 'Create IDP', 'Add OCP CatalogSource',
    'Cleanup Cypress Test Namespaces', 'RHOAI Operator Cleanup',
    'Deploy RHOAI operator', 'Validate RHOAI Health', 'Install GPU Operator',
    'Deploy NFS', 'Set Hibernate Timeout', 'Upgrade RHOAI Operator',
    'Stash Test Config',
    'Retrieve Test Config', 'Setup Dashboard Tools',
    'Clone ODH-Dashboard', 'Generate Dashboard Test Variables',
    'Setup OpenShift Local Cluster', 'Verify Cluster is Ready',
    'Verify Dashboard is Ready', 'Update Dashboard Image in DSC',
    'Match Dashboard Testing Branch', 'Update Dashboard Packages',
}

# The test execution stages
PIPELINE_TEST_STAGES = {
    'Execute Cypress Tests',       # Inner stage from runDashboardTestStages()
    'Run Dashboard Tests',         # Wrapper stage in Jenkinsfile
}


def analyze_pipeline_failure_general(console_output: str, build_result: str) -> dict:
    """
    Pipeline failure detection for the dashboard-e2e-tests Jenkins pipeline.

    The pipeline (Jenkinsfile_dashboard_e2e.groovy) has two stage groups:

    Stage Group 1 - Cluster & Operator Setup (Kubernetes agent):
      Install new cluster, Setup BYOIDC, Generate Test Config File,
      Deploy external DNS, Add ICSP, Create IDP, Add OCP CatalogSource,
      Cleanup Cypress Test Namespaces, RHOAI Operator Cleanup,
      Deploy RHOAI operator, Validate RHOAI Health, Install GPU Operator,
      Deploy NFS, Set Hibernate Timeout, Upgrade RHOAI Operator, Stash Test Config

    Stage Group 2 - Dashboard E2E Tests (VM or Container agent):
      Retrieve Test Config, Setup Dashboard Tools, Clone ODH-Dashboard,
      Generate Dashboard Test Variables, Setup OpenShift Local Cluster,
      Verify Cluster is Ready, Verify Dashboard is Ready,
      Update Dashboard Image in DSC, Match Dashboard Testing Branch,
      Update Dashboard Packages, Execute Cypress Tests

    Post-build: dashboardPostBuild() runs always after stage group 2.

    Error patterns in console output:
    1. Jenkins error() calls: "**** ERROR_MESSAGE ****" from common.failWhenRcNotZero()
    2. Java/Groovy exceptions with stack traces
    3. Shell script exit codes: "script returned exit code X : STAGE : Shell Script"
    4. Unique Error Messages block from dashboardPostBuild FlowGraphAction parsing
    """
    failure_info = {
        'is_deployment_failure': False,
        'failed_step': None,
        'error_details': None,
        'exception_type': None,
        'exception_message': None,
        'exception_location': None,
        'known_issue': None,
        'needs_cluster_analysis': False,
        'all_failed_stages': [],
        'is_post_test_failure': False,
        'failure_category': None,  # 'infra', 'test_execution', 'post_build', None
    }

    if build_result == 'SUCCESS':
        return failure_info

    if not console_output:
        return failure_info

    # Use module-level stage classification constants
    infra_stages = PIPELINE_INFRA_STAGES
    test_stages = PIPELINE_TEST_STAGES

    # --- Known error messages from failWhenRcNotZero() calls in Jenkinsfile ---
    # Maps error message substrings to (stage_name, known_jira)
    known_error_messages = {
        'OCM Login failed': ('Install new cluster', None),
        'OSD Cluster deployment failed': ('Install new cluster', None),
        'OCP Cluster deployment failed': ('Install new cluster', None),
        'Adding ICSP': ('Add ICSP', None),
        'CatalogSource failed': ('Add OCP CatalogSource', None),
        'RHOAI Operator cleanup failed': ('RHOAI Operator Cleanup', None),
        'RHOAI Operator deployment failed': ('Deploy RHOAI operator', None),
        'RHOAI health check validation failed': ('Validate RHOAI Health', None),
        'GPU Installation failed': ('Install GPU Operator', None),
        'NFS Storage deployment failed': ('Deploy NFS', None),
        'Dashboard console is not accessible': ('Verify Dashboard is Ready', None),
        'Failed to verify dashboard route': ('Verify Dashboard is Ready', None),
        'Failed to install required tools': ('Setup Dashboard Tools', None),
        'Failed to setup cypress test tools': ('Setup Dashboard Tools', None),
        'Cypress Test Results were not created': ('Execute Cypress Tests', None),
        'OCP Cluster de-provisioning failed': ('Post-failure cleanup', None),
    }

    # PATTERN 1: failWhenRcNotZero error messages: "**** ERROR_MESSAGE ****"
    # Real error messages are 15+ chars (e.g. "OCM Login failed!"), on a single line
    error_star_pattern = r'\*{4}\s+(.{15,}?)\s+\*{4}'
    error_star_matches = re.findall(error_star_pattern, console_output)

    for error_msg in error_star_matches:
        matched_known = False
        for known_substr, (stage_name, jira_key) in known_error_messages.items():
            if known_substr in error_msg:
                failure_info['is_deployment_failure'] = True
                failure_info['failed_step'] = stage_name
                failure_info['error_details'] = error_msg
                failure_info['known_issue'] = jira_key
                failure_info['needs_cluster_analysis'] = stage_name in infra_stages
                failure_info['failure_category'] = 'infra'
                failure_info['all_failed_stages'].append({
                    'stage': stage_name,
                    'error': error_msg
                })
                matched_known = True
                break
        # Capture unknown **** **** errors as generic pipeline failures
        if not matched_known and not failure_info['is_deployment_failure']:
            failure_info['is_deployment_failure'] = True
            failure_info['failed_step'] = 'Unknown (failWhenRcNotZero)'
            failure_info['error_details'] = error_msg
            failure_info['needs_cluster_analysis'] = True
            failure_info['failure_category'] = 'infra'
            failure_info['all_failed_stages'].append({
                'stage': 'Unknown',
                'error': error_msg
            })
        if failure_info['is_deployment_failure']:
            break

    # PATTERN 2: Java/Groovy exceptions with stack traces
    if not failure_info['is_deployment_failure']:
        java_exception_pattern = r'(java\.[a-zA-Z.]+Exception):\s*(.+?)(?:\n|$)'
        java_match = re.search(java_exception_pattern, console_output)

        if java_match:
            exception_type = java_match.group(1)
            exception_message = java_match.group(2).strip()

            failure_info['is_deployment_failure'] = True
            failure_info['exception_type'] = exception_type
            failure_info['exception_message'] = exception_message
            failure_info['error_details'] = f'{exception_type}: {exception_message}'

            # Extract the method/location from stack trace
            location_pattern = r'at\s+(\w+)\.call\(([^)]+)\)'
            location_matches = re.findall(location_pattern, console_output)
            if location_matches:
                failure_info['exception_location'] = location_matches[:3]
                failed_method = location_matches[0][0]
                failure_info['failed_step'] = failed_method

                # Classify based on the Groovy script that failed
                if failed_method == 'dashboardPostBuild':
                    failure_info['failure_category'] = 'post_build'
                elif failed_method == 'dashboardHelper':
                    failure_info['failure_category'] = 'test_execution'
                else:
                    failure_info['failure_category'] = 'infra'
            else:
                failure_info['failed_step'] = 'Unknown'
                failure_info['failure_category'] = 'infra'

            failure_info['needs_cluster_analysis'] = True

    # PATTERN 3: Shell script exit code failures
    if not failure_info['is_deployment_failure']:
        pattern = r'script returned exit code (\d+) : ([^:]+) :'
        matches = re.findall(pattern, console_output)

        if matches:
            for exit_code, stage_name in matches:
                failure_info['all_failed_stages'].append({
                    'stage': stage_name.strip(),
                    'exit_code': int(exit_code)
                })

            # Use the LAST failure as the primary one
            exit_code, stage_name = matches[-1]
            stage_name = stage_name.strip()
            failure_info['is_deployment_failure'] = True
            failure_info['failed_step'] = stage_name
            failure_info['error_details'] = f'Stage exited with code {exit_code}'
            failure_info['needs_cluster_analysis'] = True

            if stage_name in infra_stages:
                failure_info['failure_category'] = 'infra'
            elif stage_name in test_stages:
                failure_info['failure_category'] = 'test_execution'
            else:
                failure_info['failure_category'] = 'post_build'

    # PATTERN 4: Unique Error Messages from dashboardPostBuild FlowGraphAction
    if not failure_info['is_deployment_failure']:
        unique_errors_pattern = r'Unique Error Messages:\n(.+?)(?:\n\[|$)'
        unique_match = re.search(unique_errors_pattern, console_output, re.DOTALL)
        if unique_match:
            errors_text = unique_match.group(1).strip()
            if errors_text and 'No errors found' not in errors_text:
                # Format: "error_message : stage_name : node_name"
                for line in errors_text.split('\n'):
                    line = line.strip()
                    if ' : ' in line:
                        parts = line.split(' : ', 2)
                        error_msg = parts[0].strip()
                        stage_name = parts[1].strip() if len(parts) > 1 else 'Unknown'
                        failure_info['all_failed_stages'].append({
                            'stage': stage_name,
                            'error': error_msg
                        })
                        if not failure_info['is_deployment_failure']:
                            failure_info['is_deployment_failure'] = True
                            failure_info['failed_step'] = stage_name
                            failure_info['error_details'] = error_msg

                            if stage_name in infra_stages:
                                failure_info['failure_category'] = 'infra'
                            elif stage_name in test_stages:
                                failure_info['failure_category'] = 'test_execution'
                            else:
                                failure_info['failure_category'] = 'post_build'

    # PATTERN 5: Post-build failure detection
    # dashboardPostBuild marks build UNSTABLE (1-20% failures) or FAILURE (>20%)
    # If tests ran and some failed, that's a test failure, not a pipeline failure
    if not failure_info['is_deployment_failure'] and build_result in ['FAILURE', 'UNSTABLE']:
        # Check if tests actually ran by looking for Cypress output markers
        tests_ran = bool(re.search(r'(passing|failing|Cypress\s+test|Run Finished|cypress run)', console_output, re.IGNORECASE))

        if tests_ran:
            failure_info['is_post_test_failure'] = True
            failure_info['failure_category'] = 'test_execution'
        else:
            failure_info['is_deployment_failure'] = True
            failure_info['failed_step'] = 'Unknown pipeline step'
            failure_info['error_details'] = 'Build failed but specific step not identified'
            failure_info['needs_cluster_analysis'] = True
            failure_info['failure_category'] = 'infra'

    return failure_info


def extract_test_keywords(filename: str) -> set:
    """
    Extract meaningful keywords from a test filename for fuzzy matching.
    
    Examples:
    - 'testWorkbenchCreation.cy.ts' -> {'workbench', 'creation'}
    - 'workbenches.cy.ts' -> {'workbench'}
    - 'testCreateConnectionTypes.cy.ts' -> {'create', 'connection', 'type'}
    - 'connectionTypes.cy.ts' -> {'connection', 'type'}
    """
    if not filename:
        return set()
    
    # Remove extension and path
    name = os.path.basename(filename).replace('.cy.ts', '').replace('.ts', '')
    
    # Remove common prefixes
    name = re.sub(r'^test', '', name, flags=re.IGNORECASE)
    
    # Split camelCase and snake_case
    # 'WorkbenchCreation' -> ['Workbench', 'Creation']
    words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', name)
    
    # Normalize to lowercase and singularize common plurals
    keywords = set()
    for word in words:
        word = word.lower()
        # Simple singularization
        if word.endswith('es') and len(word) > 3:
            keywords.add(word[:-2])  # workbenches -> workbench
        elif word.endswith('s') and len(word) > 3:
            keywords.add(word[:-1])  # types -> type
        keywords.add(word)
    
    # Filter out very short or common words
    keywords = {k for k in keywords if len(k) > 2 and k not in {'test', 'the', 'and', 'for'}}
    
    return keywords


def test_files_match(test_file: str, screenshot_file: str, test_name: str = "") -> bool:
    """
    Check if a test file matches a screenshot file using smart keyword matching.
    
    Returns True if:
    1. Exact basename match, OR
    2. Significant keyword overlap between files, OR
    3. Test name keywords match screenshot path
    """
    if not test_file or not screenshot_file:
        return False
    
    test_basename = os.path.basename(test_file)
    screenshot_basename = os.path.basename(screenshot_file)
    
    # 1. Exact match
    if test_basename == screenshot_basename:
        return True
    
    # 2. Keyword overlap matching
    test_keywords = extract_test_keywords(test_file)
    screenshot_keywords = extract_test_keywords(screenshot_file)
    
    if test_keywords and screenshot_keywords:
        # Check for significant overlap
        overlap = test_keywords & screenshot_keywords
        # Match if at least one significant keyword overlaps
        # (significant = longer than 4 chars to avoid matching 'test', 'type', etc. alone)
        significant_overlap = {k for k in overlap if len(k) > 4}
        if significant_overlap:
            return True
        # Also match if multiple shorter keywords overlap
        if len(overlap) >= 2:
            return True
    
    # 3. Check test name against screenshot path (fallback)
    if test_name:
        test_name_lower = test_name.lower()
        screenshot_lower = screenshot_file.lower()
        # Extract key concepts from test name
        key_concepts = ['workbench', 'connection', 'model', 'pipeline', 'storage', 
                       'project', 'registry', 'nim', 'serving', 'runtime']
        for concept in key_concepts:
            if concept in test_name_lower and concept in screenshot_lower:
                return True
    
    return False


async def get_test_failure_screenshots(jenkins_cli, job_path: str, build_num: int, test_name: str, test_file: str = None) -> list:
    """
    Find screenshots for a specific failed test, including retry attempts.
    Uses smart keyword matching to find screenshots even when file names differ slightly.

    Cypress saves screenshots as:
    - screenshots/<spec-name>/<test-name>.png
    - screenshots/<spec-name>/<test-name> (attempt 2).png
    - screenshots/<spec-name>/<test-name> (attempt 3).png
    """
    screenshots = []

    try:
        artifacts = await jenkins_cli.list_artifacts(job_path, build_num)

        for artifact in artifacts:
            rel_path = artifact.get('relativePath', '')

            # Look for screenshots directory
            if 'screenshots' in rel_path and rel_path.endswith('.png'):
                # Extract actual test file from screenshot path
                actual_test_file = None
                if '.cy.ts' in rel_path:
                    # Extract from path like: screenshots/dataScienceProjects/models/testModelStopStart.cy.ts/...
                    match = re.search(r'screenshots/(.+?\.cy\.ts)/', rel_path)
                    if match:
                        actual_test_file = f"cypress/tests/e2e/{match.group(1)}"
                
                # Smart matching: use keyword-based matching instead of strict basename comparison
                if test_file and actual_test_file:
                    if not test_files_match(test_file, actual_test_file, test_name):
                        continue  # Skip - doesn't match our test
                
                # Fallback: If no test_file provided, use loose name matching
                elif not test_file or 'unknown' in test_file:
                    test_normalized = test_name.lower().replace(' ', '_').replace('-', '_')
                    path_normalized = rel_path.lower().replace(' ', '_').replace('-', '_')
                    test_words = test_normalized.split('_')
                    if not any(word in path_normalized for word in test_words if len(word) > 4):
                        continue  # Skip - doesn't match test name

                # Build screenshot URL
                screenshot_url = f"{jenkins_cli.jenkins_url}/job/{job_path.replace('/', '/job/')}/{build_num}/artifact/{rel_path}"

                screenshots.append({
                    'path': rel_path,
                    'url': screenshot_url,
                    'name': os.path.basename(rel_path),
                    'is_retry': 'attempt' in rel_path.lower(),
                    'test_file': actual_test_file
                })

    except Exception as e:
        print(f"  Warning: Could not fetch screenshots: {e}")

    # Sort: original first, then retries
    screenshots.sort(key=lambda x: (x['is_retry'], x['name']))

    return screenshots


def extract_dashboard_commit(console_output: str) -> dict:
    """Extract dashboard commit info from Jenkins console (legacy fallback)"""
    commit_info = {
        'commit_hash': None,
        'commit_date': None,
        'branch': None
    }

    if not console_output:
        return commit_info

    # Look for git commit patterns
    for line in console_output.split('\n'):
        # Pattern: "Dashboard commit: abc123def"
        if 'dashboard' in line.lower() and ('commit' in line.lower() or 'sha' in line.lower()):
            match = re.search(r'([a-f0-9]{7,40})', line)
            if match:
                commit_info['commit_hash'] = match.group(1)[:8]

        # Pattern: "Branch: main" or "ref: refs/heads/main"
        if 'branch' in line.lower() or 'ref' in line.lower():
            if 'main' in line.lower():
                commit_info['branch'] = 'main'
            elif 'master' in line.lower():
                commit_info['branch'] = 'master'

    return commit_info


def check_git_diff_for_test(test_file: str, image_commit: str, repo_path: str) -> dict:
    """
    Check if test file changed between image commit and main.
    
    IMPROVED: Now detects:
    - @Bug/@Maintain tags (existing)
    - it.skip/describe.skip annotations
    - skipOn configuration
    - Whether test was quarantined AFTER the build (not in build commit)
    """
    result = {
        'file_changed': False,
        'commits_behind': 0,
        'main_commit': None,
        'quarantined': False,
        'quarantined_in_main_only': False,  # NEW: quarantined after build
        'quarantine_method': None,  # NEW: how it's quarantined
        'needs_maintenance': False,
        'bug_references': [],
        'current_test_content': None,
        'image_test_content': None,
        'was_quarantined_at_build': False,  # NEW: was it quarantined at build time?
    }

    try:
        # Get current main commit
        main_result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        if main_result.returncode == 0:
            result['main_commit'] = main_result.stdout.strip()[:8]

        # If we have image commit, check diff
        if image_commit:
            # Count commits between image and main
            count_result = subprocess.run(
                ['git', 'rev-list', '--count', f'{image_commit}..HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if count_result.returncode == 0:
                result['commits_behind'] = int(count_result.stdout.strip())

            # Check if test file changed
            diff_result = subprocess.run(
                ['git', 'diff', '--name-only', image_commit, 'HEAD', '--', test_file],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if diff_result.returncode == 0 and diff_result.stdout.strip():
                result['file_changed'] = True

        # Read current test file content (main branch) to check for tags
        full_test_path = os.path.join(repo_path, 'frontend/src/__tests__/cypress', test_file)
        if os.path.exists(full_test_path):
            with open(full_test_path, 'r') as f:
                content = f.read()
                result['current_test_content'] = content

                # IMPROVED: Check for multiple quarantine methods
                quarantine_info = _detect_quarantine_status(content)
                result['quarantined'] = quarantine_info['quarantined']
                result['quarantine_method'] = quarantine_info['method']
                result['bug_references'].extend(quarantine_info['bug_refs'])
                result['needs_maintenance'] = quarantine_info['needs_maintenance']

        # NEW: Check if test was quarantined at build commit (not just in main)
        if image_commit and result['quarantined']:
            build_content = _get_file_at_commit(test_file, image_commit, repo_path)
            if build_content:
                result['image_test_content'] = build_content
                build_quarantine = _detect_quarantine_status(build_content)
                result['was_quarantined_at_build'] = build_quarantine['quarantined']
                
                # If quarantined in main but NOT at build time, it was quarantined AFTER the build
                if result['quarantined'] and not result['was_quarantined_at_build']:
                    result['quarantined_in_main_only'] = True
                    print(f"   ⚠️  Test was quarantined AFTER build (not in commit {image_commit[:8]})")

    except Exception as e:
        print(f"  Warning: Could not analyze git diff: {e}")

    return result


def _detect_quarantine_status(content: str) -> dict:
    """
    Detect if a test file has quarantine/skip markers.
    
    Checks for:
    - @Bug tag (product bug - quarantined)
    - @Maintain tag (automation bug - needs fix)
    - it.skip() / describe.skip() - explicitly skipped
    - skipOn configuration
    """
    result = {
        'quarantined': False,
        'needs_maintenance': False,
        'method': None,
        'bug_refs': []
    }
    
    # Check for @Bug tag (product bug - quarantined)
    if '@Bug' in content:
        result['quarantined'] = True
        result['method'] = '@Bug tag'
        bug_matches = re.findall(r'Product Bug[:\- ]+([A-Z]+-\d+)', content, re.IGNORECASE)
        result['bug_refs'].extend(bug_matches)
        # Also try to find bug in @Bug(...) format
        bug_annotation = re.findall(r'@Bug\s*\(\s*[\'"]([A-Z]+-\d+)[\'"]', content)
        result['bug_refs'].extend(bug_annotation)

    # Check for @Maintain tag (automation bug)
    if '@Maintain' in content:
        result['needs_maintenance'] = True
        if not result['method']:
            result['method'] = '@Maintain tag'
        auto_matches = re.findall(r'Automation Bug[:\- ]+([A-Z]+-\d+)', content, re.IGNORECASE)
        result['bug_refs'].extend(auto_matches)
    
    # Check for it.skip / describe.skip - explicit skip
    skip_patterns = [
        r'\bit\.skip\s*\(',      # it.skip(
        r'\bdescribe\.skip\s*\(', # describe.skip(
        r'\bxit\s*\(',           # xit( - Jasmine-style skip
        r'\bxdescribe\s*\(',     # xdescribe( - Jasmine-style skip
    ]
    for pattern in skip_patterns:
        if re.search(pattern, content):
            result['quarantined'] = True
            result['method'] = result['method'] or 'it.skip/describe.skip'
            break
    
    # Check for skipOn configuration (conditional skip)
    if 'skipOn' in content or 'skip:' in content:
        # Look for skipOn patterns like: skipOn: { ... }
        if re.search(r'skipOn\s*[:\{]', content):
            result['quarantined'] = True
            result['method'] = result['method'] or 'skipOn configuration'
    
    # Deduplicate bug references
    result['bug_refs'] = list(set(result['bug_refs']))
    
    return result


def _get_file_at_commit(file_path: str, commit: str, repo_path: str) -> str:
    """Get the content of a file at a specific commit."""
    try:
        # Try multiple possible paths for the test file
        paths_to_try = [
            f'frontend/src/__tests__/cypress/{file_path}',
            file_path,
            f'packages/cypress/{file_path}',
        ]
        
        for path in paths_to_try:
            result = subprocess.run(
                ['git', 'show', f'{commit}:{path}'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout
        
        return None
    except Exception as e:
        print(f"  Warning: Could not get file at commit {commit}: {e}")
        return None


def extract_test_name_from_it_block(test_file_path: str) -> str:
    """
    Extract the actual test name from the it() or describe() block in the test file.
    
    Priority:
    1. Look for it() blocks with test names
    2. Look for describe() blocks as fallback
    3. Extract from file name as last resort (e.g., testClusterAdminSettings from testClusterAdminSettings.cy.ts)
    """
    try:
        if not os.path.exists(test_file_path):
            # If file doesn't exist, try to extract from path
            filename = os.path.basename(test_file_path)
            if filename.startswith('test') and filename.endswith('.cy.ts'):
                # Extract testClusterAdminSettings from testClusterAdminSettings.cy.ts
                return filename.replace('.cy.ts', '')
            return None
            
        with open(test_file_path, 'r') as f:
            content = f.read()
            
            # Try to find it() blocks first (most specific)
            # Handles: it('test name', () => {})
            it_patterns = [
                r'''it\s*\(\s*['"]([^'"]+)['"]''',  # Standard it('name')
                r'''it\.only\s*\(\s*['"]([^'"]+)['"]''',  # it.only('name')
                r'''it\.skip\s*\(\s*['"]([^'"]+)['"]''',  # it.skip('name')
            ]
            
            for pattern in it_patterns:
                match = re.search(pattern, content)
                if match:
                    return match.group(1)
            
            # Try describe() blocks as fallback
            describe_match = re.search(r'''describe\s*\(\s*['"]([^'"]+)['"]''', content)
            if describe_match:
                return describe_match.group(1)
            
            # Last resort: extract from filename
            filename = os.path.basename(test_file_path)
            if filename.startswith('test') and filename.endswith('.cy.ts'):
                # Extract testClusterAdminSettings from testClusterAdminSettings.cy.ts
                return filename.replace('.cy.ts', '')
                
    except Exception as e:
        print(f"  Warning: Could not extract test name from {test_file_path}: {e}")
        # Try to extract from path even on error
        try:
            filename = os.path.basename(test_file_path)
            if filename.startswith('test') and filename.endswith('.cy.ts'):
                return filename.replace('.cy.ts', '')
        except:
            pass
    
    return None


def improved_file_extraction(test_name: str, suite_name: str) -> str:
    """Better test file path extraction - comprehensive mapping for all test types"""
    combined = (test_name + ' ' + suite_name).lower()

    # Gen AI tests - MUST come FIRST (before connection tests, because "Gen AI...Connection" contains "connection")
    if 'gen ai' in combined or 'genai' in combined or 'llamastack' in combined or 'gen ai studio' in combined:
        return "cypress/tests/e2e/gen-ai/testGenAi.cy.ts"
    
    # Model stop/start tests - MUST come before general model tests
    if 'model' in combined and ('stop' in combined or 'start' in combined):
        return "cypress/tests/e2e/dataScienceProjects/models/testModelStopStart.cy.ts"
    
    # ISV/Explore tests
    if 'isv' in combined or 'explore' in combined:
        return "cypress/tests/e2e/applications/explore/testEnabledISVs.cy.ts"
    
    # Workbenches - tolerations tests (must come before general workbench tests)
    if 'workbench' in combined and 'toleration' in combined:
        return "cypress/tests/e2e/settings/hardwareProfiles/testWorkbenchTolerations.cy.ts"

    # Storage tests
    if 'cluster storage' in combined:
        if 'access mode' in combined:
            return "cypress/tests/e2e/dataScienceProjects/clusterStorage/testClusterStorageAccessModes.cy.ts"
        else:
            return "cypress/tests/e2e/dataScienceProjects/clusterStorage/testClusterStorageCreation.cy.ts"

    # OCI tests (must come before connection tests — "OCI Connection" contains "connection")
    if 'oci' in combined:
        return "cypress/tests/e2e/dataScienceProjects/models/testDeployOCIModel.cy.ts"

    # Connection tests (must come after Gen AI and OCI tests)
    if 'connection' in combined:
        if 'type' in combined:
            return "cypress/tests/e2e/settings/connectionTypes/connectionTypes.cy.ts"
        else:
            return "cypress/tests/e2e/dataScienceProjects/connections/testConnectionCreation.cy.ts"

    # Workbench tests
    if 'workbench' in combined:
        if 'variable' in combined:
            return "cypress/tests/e2e/dataScienceProjects/workbenches/testWorkbenchVariables.cy.ts"
        elif 'storage' in combined:
            return "cypress/tests/e2e/dataScienceProjects/workbenches/testWorkbenchStorageClasses.cy.ts"
        elif 'creation' in combined:
            return "cypress/tests/e2e/dataScienceProjects/workbenches/testWorkbenchCreation.cy.ts"
        elif 'status' in combined:
            return "cypress/tests/e2e/dataScienceProjects/workbenches/testWorkbenchStatus.cy.ts"
        elif 'image' in combined:
            return "cypress/tests/e2e/dataScienceProjects/workbenches/testWorkbenchImages.cy.ts"
        elif 'control' in combined:
            return "cypress/tests/e2e/dataScienceProjects/workbenches/testWorkbenchControlSuite.cy.ts"
        elif 'negative' in combined:
            return "cypress/tests/e2e/dataScienceProjects/workbenches/testWorkbenchNegativeTests.cy.ts"
        else:
            return "cypress/tests/e2e/dataScienceProjects/workbenches/workbenches.cy.ts"

    # Model Registry tests
    if 'model' in combined or 'registry' in combined:
        if 'archive' in combined:
            return "cypress/tests/e2e/modelRegistry/testArchiveModels.cy.ts"
        elif 'register' in combined:
            return "cypress/tests/e2e/modelRegistry/testRegisterModel.cy.ts"
        elif 'deploy' in combined:
            return "cypress/tests/e2e/modelRegistry/testRegistryDeployModel.cy.ts"
        elif 'permission' in combined:
            return "cypress/tests/e2e/modelRegistry/testManageRegistryPermissions.cy.ts"
        elif 'create' in combined:
            return "cypress/tests/e2e/modelRegistry/testCreateModelRegistry.cy.ts"
        elif 'edit' in combined:
            return "cypress/tests/e2e/modelRegistry/testAdminEditRegistry.cy.ts"
        else:
            return "cypress/tests/e2e/modelRegistry/testArchiveModels.cy.ts"

    # Serving Runtime tests
    if 'serving' in combined or 'runtime' in combined:
        return "cypress/tests/e2e/settings/servingRuntimes/testSingleServingRuntimeCreation.cy.ts"

    # NIM tests
    if 'nim' in combined:
        return "cypress/tests/e2e/nim/testEnableNIM.cy.ts"

    # Pipeline tests
    if 'pipeline' in combined:
        return "cypress/tests/e2e/pipelines/createRunDeletePipeline.cy.ts"

    # Project tests
    if 'project' in combined:
        if 'edit' in combined:
            return "cypress/tests/e2e/dataScienceProjects/testProjectEditing.cy.ts"
        elif 'contributor' in combined or 'permission' in combined:
            return "cypress/tests/e2e/dataScienceProjects/testProjectContributorPermissions.cy.ts"
        else:
            return "cypress/tests/e2e/dataScienceProjects/testProjectEditing.cy.ts"

    # User Management tests (narrow match — 'user' alone is too broad)
    if 'unauthorized' in combined or 'user group' in combined or 'user management' in combined:
        if 'spawn' in combined or 'notebook' in combined:
            return "cypress/tests/e2e/settings/userManagement/testUnauthorizedUserNotebookSpawnBlocked.cy.ts"
        elif 'permission' in combined or 'perm change' in combined:
            return "cypress/tests/e2e/settings/userManagement/testUnathorizedPermChange.cy.ts"
        elif 'removed' in combined or 'notification' in combined:
            return "cypress/tests/e2e/settings/userManagement/testUserGroupRemovedNotification.cy.ts"

    # Hardware Profile / Toleration tests
    if 'hardware' in combined or 'toleration' in combined:
        if 'workbench' in combined:
            return "cypress/tests/e2e/settings/hardwareProfiles/testWorkbenchTolerations.cy.ts"
        elif 'notebook' in combined:
            return "cypress/tests/e2e/settings/hardwareProfiles/testNotebookTolerations.cy.ts"
        elif 'serving' in combined:
            return "cypress/tests/e2e/settings/hardwareProfiles/testModelServingTolerations.cy.ts"
        else:
            return "cypress/tests/e2e/settings/hardwareProfiles/testHardwareProfiles.cy.ts"

    # Cluster Settings tests
    if 'cluster setting' in combined or 'data collection' in combined:
        if 'data collection' in combined:
            return "cypress/tests/e2e/settings/clusterSettings/testDataCollection.cy.ts"
        else:
            return "cypress/tests/e2e/settings/clusterSettings/testAdminClusterSettings.cy.ts"

    # Storage Classes tests
    if 'storage class' in combined:
        return "cypress/tests/e2e/storageClasses/storageClasses.cy.ts"

    # Distributed Workload tests
    if 'workload' in combined and 'metric' in combined:
        return "cypress/tests/e2e/distributedWorkloadMetrics/testWorkloadMetricsDefaultPageContents.cy.ts"

    # Feature Store tests
    if 'feature store' in combined or 'feature_store' in combined:
        return "cypress/tests/e2e/featureStore/testFeatureStoreFeatures.cy.ts"

    # RayJob / Model Training tests
    if 'rayjob' in combined or 'ray job' in combined or 'ray_job' in combined:
        return "cypress/tests/e2e/modelTraining/testRayJobs.cy.ts"

    # Model Catalog tests
    if 'model catalog' in combined or 'catalog source' in combined:
        return "cypress/tests/e2e/modelCatalog/testModelCatalog.cy.ts"

    # Performance / Benchmark tests
    if 'performance' in combined or 'benchmark' in combined:
        return "cypress/tests/e2e/modelRegistry/testPerformanceFilters.cy.ts"

    # LM Eval tests
    if 'lmeval' in combined or 'lm eval' in combined or 'lm_eval' in combined:
        return "cypress/tests/e2e/lmEval/testLMEvalDynamic.cy.ts"

    # Notebook / Jupyter tests
    if 'notebook' in combined or 'jupyter' in combined:
        if 'admin' in combined:
            return "cypress/tests/e2e/applications/enabled/testNotebookAdministration.cy.ts"
        elif 'launch' in combined or 'standalone' in combined:
            return "cypress/tests/e2e/dataScienceProjects/testLaunchStandaloneNotebook.cy.ts"
        else:
            return "cypress/tests/e2e/dataScienceProjects/testLaunchStandaloneNotebook.cy.ts"

    # Learning Resources tests
    if 'resource' in combined and ('learning' in combined or 'custom' in combined or 'filter' in combined):
        return "cypress/tests/e2e/learningResources/testCustomResourceCreation.cy.ts"

    # Navigation / Login tests
    if 'login' in combined or 'navigation' in combined:
        return "cypress/tests/e2e/dashboardNavigation/testUserLogin.cy.ts"

    # Application tests
    if 'application' in combined or 'about' in combined:
        return "cypress/tests/e2e/application.cy.ts"

    # Dynamic fallback: try to match describe() blocks in e2e test files
    frontend_repo = os.getenv("FRONTEND_REPO_PATH", "")
    if frontend_repo:
        matched = _find_test_file_by_describe(test_name, suite_name, frontend_repo)
        if matched:
            return matched

    return "cypress/tests/e2e/unknown.cy.ts"


def _find_test_file_by_describe(test_name: str, suite_name: str, frontend_repo: str) -> str:
    """Dynamically scan e2e test files for matching describe() blocks"""
    import glob
    e2e_dir = os.path.join(frontend_repo, 'frontend/src/__tests__/cypress/cypress/tests/e2e')
    if not os.path.isdir(e2e_dir):
        return ""

    search_terms = []
    if suite_name:
        search_terms.append(suite_name.strip())
    # Use first meaningful part of test_name (before "before each" or "after all")
    clean_name = re.split(r'"(?:before|after)\s+(?:each|all)"', test_name)[0].strip()
    if clean_name and clean_name != suite_name:
        search_terms.append(clean_name)

    for cy_file in glob.glob(os.path.join(e2e_dir, '**/*.cy.ts'), recursive=True):
        try:
            with open(cy_file, 'r', errors='ignore') as f:
                content = f.read(5000)  # read enough to find describe()
            for term in search_terms:
                if term and term in content:
                    # Return relative path from cypress root
                    rel = cy_file.split('/cypress/tests/e2e/')[-1]
                    return f"cypress/tests/e2e/{rel}"
        except Exception:
            continue
    return ""


def generate_html_report(
    name, build_num, build_url, nightly_info, parsed_results, failures,
    pipeline_failure, image_metadata, screenshots_by_stage, timed_out_stages,
    stage_timeout_minutes, results_by_stage, test_stages_ran,
    analysis_with_reruns, cluster_analysis, recent_merges, git_analysis,
    all_tests_by_stage=None, version_mismatch=None, cluster_image_ages=None,
) -> str:
    import html as html_mod
    esc = html_mod.escape
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = parsed_results.get("total_tests", 0)
    passed = parsed_results.get("passed_tests", 0)
    failed = parsed_results.get("failed_tests", 0)
    skipped = parsed_results.get("skipped_tests", 0)
    pass_rate = round(passed / total * 100, 1) if total > 0 else 0
    num_analyses = len(analysis_with_reruns.get("failure_analyses", []))
    has_pipeline_fail = pipeline_failure.get("is_deployment_failure", False)

    if has_pipeline_fail and total == 0:
        status_label, status_cls = "PIPELINE FAILURE", "badge-fail"
    elif timed_out_stages:
        status_label, status_cls = "TIMEOUT", "badge-timeout"
    elif failed > 0 or num_analyses > 0:
        status_label, status_cls = "FAILED", "badge-fail"
    else:
        status_label, status_cls = "PASSED", "badge-pass"

    all_display_stages = sorted(set(list(results_by_stage.keys()) + list(test_stages_ran)))
    stage_html_parts = []
    for stage_name in all_display_stages:
        sr = results_by_stage.get(stage_name)
        is_timed_out = stage_name in timed_out_stages
        stage_failures = [f for f in failures if getattr(f, "_stage", "") == stage_name]
        all_retry_pass = stage_failures and all(getattr(f, "_is_retry_pass", False) for f in stage_failures)
        failed_files = screenshots_by_stage.get(stage_name, {})

        s_total = s_passed = s_failed = s_skipped = 0
        real_fail_count = sum(1 for f in stage_failures if not getattr(f, "_is_retry_pass", False))
        if sr:
            s_total = sr["total_tests"]
            s_skipped = sr.get("skipped_tests", 0)
            if stage_failures:
                s_failed = real_fail_count
                s_passed = s_total - s_failed - s_skipped
            else:
                s_passed = sr["passed_tests"]
                s_failed = sr["failed_tests"]
                if s_failed == 0 and failed_files:
                    s_failed = len(failed_files)

        if is_timed_out:
            icon_cls, icon = "icon-timeout", "&#9201;"
        elif all_retry_pass:
            icon_cls, icon = "icon-pass", "&#10004;"
        elif s_failed > 0 or (not sr and failed_files):
            icon_cls, icon = "icon-fail", "&#10008;"
        else:
            icon_cls, icon = "icon-pass", "&#10004;"

        pct = round(s_passed / s_total * 100) if s_total > 0 else 100
        timeout_tag = f' <span class="tag-timeout">TIMEOUT - killed after {stage_timeout_minutes}min</span>' if is_timed_out else ""
        stats = f"{s_total} tests, {s_passed} passed, {s_failed} failed"
        if s_skipped:
            stats += f", {s_skipped} skipped"

        nested = ""
        items = []
        failure_names = set()

        # Render failure items (with expandable details)
        for f in stage_failures:
            cat = getattr(f, "_category", "unknown")
            is_rp = getattr(f, "_is_retry_pass", False)
            fname = os.path.basename(f.test_file).replace(".cy.ts", "")
            failure_names.add(fname)
            screenshot_data = getattr(f, "_screenshot_data", [])
            video_local = getattr(f, "_video_local", None)
            err_msg = f.error_message if f.error_message and f.error_message != "No error details (screenshot-only failure)" else ""

            is_test_timeout = getattr(f, "_is_test_timeout", False)
            if is_rp:
                summary_icon = '<span class="icon-warn">&#9888;</span>'
                retry_tag = ' <span class="tag-retry">passed on retry</span>'
            else:
                summary_icon = '<span class="icon-fail">&#10008;</span>'
                retry_tag = ''
            tt_tag = ' <span class="tag-test-timeout">test-timeout</span>' if is_test_timeout else ''
            summary = f'{summary_icon} {esc(fname)} <span class="tag-cat">{esc(cat)}</span>{tt_tag}{retry_tag}'

            artifacts_html = ""
            valid_screenshots = [ss for ss in screenshot_data if ss.get("data_uri")]
            if valid_screenshots:
                imgs = []
                for ss in valid_screenshots:
                    retry_label = ' <span class="tag-retry">retry</span>' if ss["is_retry"] else ""
                    imgs.append(
                        f'<div class="artifact-thumb">'
                        f'<a href="{ss["data_uri"]}" target="_blank"><img src="{ss["data_uri"]}" loading="lazy" alt="{esc(ss["name"])}"></a>'
                        f'<div class="artifact-label">{esc(ss["name"])}{retry_label}</div></div>'
                    )
                artifacts_html += f'<div class="artifact-section"><div class="artifact-title">Screenshots</div><div class="artifact-grid">{"".join(imgs)}</div></div>'

            if video_local:
                video_url = getattr(f, "_video_url", None)
                video_src = video_url or video_local
                artifacts_html += (
                    f'<div class="artifact-section"><div class="artifact-title">Video</div>'
                    f'<video controls preload="none" class="artifact-video"><source src="{esc(video_src)}" type="video/mp4"></video>'
                )
                if video_url:
                    artifacts_html += f'<div class="artifact-label"><a href="{esc(video_url)}" target="_blank">Open video in Jenkins</a></div>'
                artifacts_html += '</div>'

            if err_msg:
                artifacts_html += f'<div class="artifact-section"><div class="artifact-title">Error</div><pre class="code-block">{esc(err_msg[:2000])}</pre></div>'
            if f.stack_trace:
                artifacts_html += f'<div class="artifact-section"><details><summary class="artifact-title" style="cursor:pointer">Stack trace</summary><pre class="code-block">{esc(f.stack_trace[:3000])}</pre></details></div>'

            if not artifacts_html:
                artifacts_html = f'<div class="artifact-section"><div class="artifact-label">File: <code>{esc(f.test_file)}</code></div></div>'
            li_status = "retry" if is_rp else "failed"
            items.append(f'<li data-status="{li_status}"><details class="test-detail"><summary>{summary}</summary><div class="test-artifacts">{artifacts_html}</div></details></li>')

        # Render passed/skipped tests from JUnit data
        if all_tests_by_stage:
            stage_tests = all_tests_by_stage.get(stage_name, [])
            for t in stage_tests:
                tname = t.get("name", "")
                if tname in failure_names:
                    continue
                tfile = t.get("file", "")
                label = f'{esc(tfile)} - {esc(tname)}' if tfile else esc(tname)
                status = t.get("status", "passed")
                if status == "passed":
                    items.append(f'<li data-status="passed"><span class="icon-pass">&#10004;</span> {label}</li>')
                elif status == "skipped":
                    items.append(f'<li data-status="skipped"><span class="icon-skip">&#8212;</span> {label} <span class="tag-cat">skipped</span></li>')

        if items:
            nested = f'<ul class="failure-list">{"".join(items)}</ul>'

        bar_fail_pct = 100 - pct
        stage_html_parts.append(f"""
        <div class="stage-card">
          <div class="stage-header">
            <span class="{icon_cls}" style="font-size:1.1em">{icon}</span>
            <strong>{esc(stage_name)}</strong>
            <span class="stage-stats">{stats}{timeout_tag}</span>
          </div>
          <div class="progress-bar"><div class="progress-fill" style="width:{pct}%"></div><div class="progress-fail" style="width:{bar_fail_pct}%"></div></div>
          {nested}
        </div>""")
    stages_section = "".join(stage_html_parts)

    pipeline_section = ""
    if has_pipeline_fail:
        step = esc(str(pipeline_failure.get("failed_step", "Unknown")))
        err = esc(str(pipeline_failure.get("error_details", "")))
        exc_type = pipeline_failure.get("exception_type", "")
        exc_msg = pipeline_failure.get("exception_message", "")
        extra = ""
        if exc_type:
            extra += f'<p><strong>Exception:</strong> <code>{esc(exc_type)}</code></p>'
        if exc_msg:
            extra += f'<details><summary>Exception message</summary><pre class="code-block">{esc(exc_msg)}</pre></details>'
        pipeline_section = f"""
        <section class="section">
          <h2>Pipeline Failure</h2>
          <div class="card card-fail">
            <p><strong>Failed Step:</strong> <code>{step}</code></p>
            <p><strong>Error:</strong> {err}</p>
            {extra}
          </div>
        </section>"""

    img_rows = []
    stale_warnings = []
    for img_type in ("fbc_fragment", "iib", "dashboard", "operator_bundle"):
        meta = image_metadata.get(img_type, {})
        if not meta:
            continue
        uri = meta.get("full_image_uri", "")
        if not uri:
            continue
        build_date = meta.get("build_date") or ""
        version = meta.get("rhoai_version") or ""
        commit = meta.get("commit_sha_full", "")[:12] if meta.get("commit_sha_full") else ""
        commit_url = meta.get("commit_url", "")
        commit_link = f'<a href="{esc(commit_url)}">{esc(commit)}</a>' if commit_url and commit else esc(commit)
        age_html = ""
        is_stale = False
        if build_date and img_type in ("operator_bundle", "dashboard"):
            try:
                bd = datetime.fromisoformat(build_date.replace("Z", "+00:00"))
                delta = datetime.now(tz=timezone.utc) - bd
                hours = int(delta.total_seconds() // 3600)
                if hours < 1:
                    age_html = '<span style="color:#28a745">< 1h ago</span>'
                elif hours < 24:
                    age_html = f'<span style="color:#28a745">{hours}h ago</span>'
                else:
                    days = hours // 24
                    is_stale = True
                    age_html = f'<span style="color:#ff4444;font-weight:700">&#x1F6A8; {days}d {hours % 24}h ago &#x1F6A8;</span>'
                    label = "Operator" if img_type == "operator_bundle" else "Dashboard"
                    stale_warnings.append(f"{label} image is {days}d {hours % 24}h old — fixes merged after this build are not present")
            except (ValueError, TypeError):
                pass
        date_cell = esc(build_date)
        if age_html:
            date_cell += f" ({age_html})"
        row_style = ' style="background:rgba(255,68,68,0.08)"' if is_stale else ""
        img_rows.append(f"""
        <tr{row_style}>
          <td><strong>{esc(img_type.replace('_',' ').title())}</strong></td>
          <td class="mono">{esc(uri[:80])}{"..." if len(uri)>80 else ""}</td>
          <td>{date_cell}</td>
          <td>{esc(version)}</td>
          <td>{commit_link}</td>
        </tr>""")
    images_section = ""
    if img_rows:
        stale_banner = ""
        if stale_warnings:
            warnings_html = "".join(f"<li>{esc(w)}</li>" for w in stale_warnings)
            stale_banner = f"""
          <div style="border:2px solid #ff4444;background:rgba(255,68,68,0.08);padding:12px 16px;border-radius:8px;margin-bottom:12px;">
            <strong style="color:#ff4444">&#x1F6A8; Stale Build Warning</strong>
            <ul style="margin:4px 0 0 0">{warnings_html}</ul>
          </div>"""
        images_section = f"""
        <section class="section">
          <h2>Deployment Info</h2>{stale_banner}
          <div class="table-wrap"><table>
            <thead><tr><th>Image</th><th>URI</th><th>Build Date</th><th>Version</th><th>Commit</th></tr></thead>
            <tbody>{"".join(img_rows)}</tbody>
          </table></div>
        </section>"""

    # Component commits table from FBC fragment
    fbc_components = (image_metadata.get('fbc_fragment') or {}).get('component_commits', {})
    if fbc_components:
        # Deduplicate by repo+SHA, group component names
        seen = {}
        for comp_name, info in fbc_components.items():
            key = (info.get('repo_name', ''), info.get('sha', ''))
            if key not in seen:
                seen[key] = {'info': info, 'names': []}
            seen[key]['names'].append(comp_name)

        # Sort by commit_date ascending (oldest first), None dates at end
        def sort_key(item):
            d = item[1]['info'].get('commit_date')
            return d if d else '9999'
        sorted_components = sorted(seen.items(), key=sort_key)

        comp_rows = []
        now = datetime.now(tz=timezone.utc)
        for (repo_name, sha), group in sorted_components:
            info = group['info']
            names = group['names']
            names_html = ", ".join(f"<code>{esc(n)}</code>" for n in sorted(names))
            if len(names) > 3:
                names_html = f"<code>{esc(names[0])}</code> +{len(names)-1} more"
            repo_link = f'<a href="https://github.com/{esc(info["repo_owner"])}/{esc(repo_name)}">{esc(repo_name)}</a>' if info.get('repo_owner') else esc(repo_name)
            commit_link = f'<a href="{esc(info["url"])}">{esc(sha[:12])}</a>' if info.get('url') else esc(sha[:12])
            date_str = info.get('commit_date', '')
            age_html = ""
            row_style = ""
            if date_str:
                try:
                    cd = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    delta = now - cd
                    hours = int(delta.total_seconds() // 3600)
                    if hours < 1:
                        age_html = '<span style="color:#28a745">< 1h</span>'
                    elif hours < 24:
                        age_html = f'<span style="color:#28a745">{hours}h</span>'
                    else:
                        days = hours // 24
                        age_html = f'<span style="color:#ff4444;font-weight:700">{days}d {hours % 24}h</span>'
                        row_style = ' style="background:rgba(255,68,68,0.05)"'
                except (ValueError, TypeError):
                    pass
                date_display = esc(date_str[:19])
            else:
                date_display = '<span style="color:#999">unknown</span>'
            comp_rows.append(f"""
            <tr{row_style}>
              <td>{names_html}</td>
              <td>{repo_link}</td>
              <td class="mono">{commit_link}</td>
              <td>{date_display}</td>
              <td>{age_html}</td>
            </tr>""")

        images_section += f"""
        <section class="section">
          <details>
            <summary style="cursor:pointer;font-size:1.2em;font-weight:700;margin-bottom:8px;">
              Operator Component Commits ({len(sorted_components)} repos)
            </summary>
            <div class="table-wrap"><table>
              <thead><tr><th>Component(s)</th><th>Repo</th><th>Commit</th><th>Commit Date</th><th>Age</th></tr></thead>
              <tbody>{"".join(comp_rows)}</tbody>
            </table></div>
          </details>
        </section>"""

    vm = version_mismatch or {}
    if vm.get('has_mismatch'):
        images_section += f"""
        <section class="section">
          <div class="card" style="border:2px solid #ff4444;background:rgba(255,68,68,0.08);">
            <h3 style="color:#ff4444;margin-top:0;">🚨 Version Mismatch Detected</h3>
            <p><strong>Expected (FBC fragment):</strong> <code>{esc(vm['expected_version'])}</code></p>
            <p><strong>Installed (operator CSV):</strong> <code>{esc(vm['installed_version'])}</code></p>
            <p style="margin-bottom:0;">{esc(vm['message'])}</p>
          </div>
        </section>"""

    cia = cluster_image_ages or []
    if cia:
        age_rows = []
        for img in sorted(cia, key=lambda x: -(x.get('age_days') or 0)):
            if not img.get('build_date'):
                continue
            age_d = img.get('age_days') or 0
            age_cls = ' style="color:#ff4444;font-weight:700"' if age_d > 14 else (' style="color:#ffa500"' if age_d > 7 else '')
            age_rows.append(f"""
            <tr>
              <td><strong>{esc(img['component'])}</strong></td>
              <td>{esc(img['build_date'])}</td>
              <td{age_cls}>{esc(img['age_str'])}</td>
              <td class="mono">{esc(img.get('commit',''))}</td>
            </tr>""")
        if age_rows:
            images_section += f"""
        <section class="section">
          <h2>Cluster Image Ages</h2>
          <div class="table-wrap"><table>
            <thead><tr><th>Component</th><th>Build Date</th><th>Age</th><th>Commit</th></tr></thead>
            <tbody>{"".join(age_rows)}</tbody>
          </table></div>
        </section>"""

    failure_cards = []
    for fa in analysis_with_reruns.get("failure_analyses", []):
        f = fa.failure
        cat = getattr(f, "_category", "unknown")
        stage = getattr(f, "_stage", "")
        is_rp = getattr(f, "_is_retry_pass", False)
        fname = os.path.basename(f.test_file).replace(".cy.ts", "") if f.test_file else f.test_name
        cat_cls = {"timeout": "tag-timeout", "assertion": "tag-assertion"}.get(cat, "tag-unknown")

        is_test_timeout = getattr(f, "_is_test_timeout", False)
        badges = f'<span class="tag-cat {cat_cls}">{esc(cat)}</span>'
        if stage:
            badges += f' <span class="tag-stage">{esc(stage)}</span>'
        if is_test_timeout:
            badges += ' <span class="tag-test-timeout">test-timeout</span>'
        if is_rp:
            badges += ' <span class="tag-retry">passed on retry</span>'

        err_block = ""
        if f.error_message and f.error_message != "No error details (screenshot-only failure)":
            err_block = f'<details><summary>Error message</summary><pre class="code-block">{esc(f.error_message[:2000])}</pre></details>'
        stack_block = ""
        if f.stack_trace:
            stack_block = f'<details><summary>Stack trace</summary><pre class="code-block">{esc(f.stack_trace[:3000])}</pre></details>'

        rerun_html = ""
        rerun_explanation_html = ""
        rerun = getattr(fa, "rerun_result", None)
        if rerun and isinstance(rerun, dict) and rerun.get('attempted'):
            rr_pass = rerun.get("success", False)
            gi_for_class = git_analysis.get(f.test_file, {})
            classification = classify_failure_result(rerun, gi_for_class)
            if rr_pass:
                rr_cls = "tag-rerun-pass"
                rr_label = "Pass on re-run"
            else:
                rr_cls = "tag-fail-sm"
                rr_label = "FAILED on rerun"
            rerun_html = f' <span class="{rr_cls}">{rr_label}</span>'
            ran_on = f"build commit {rerun.get('ran_at_commit', 'unknown')[:8]}" if not rerun.get('ran_on_main', True) else "main branch"
            duration = rerun.get('duration', 0)
            rerun_explanation_html = f'<div class="rerun-explanation"><strong>Rerun ({ran_on}):</strong> {esc(classification["explanation"])}'
            if duration > 0:
                rerun_explanation_html += f' <em>({duration:.1f}s)</em>'
            if not rr_pass:
                rerun_error = rerun.get('error_output', '')
                if rerun_error:
                    rerun_explanation_html += f'<details><summary>Rerun error</summary><pre class="code-block">{esc(rerun_error[:500])}</pre></details>'
            rerun_explanation_html += '</div>'

        git_html = ""
        gi = git_analysis.get(f.test_file)
        if gi and gi.get("recently_changed"):
            days = gi.get("days_since_change", "?")
            git_html = f'<p class="git-warn">File recently changed ({days} days ago)</p>'

        screenshot_data = getattr(f, "_screenshot_data", [])
        video_local = getattr(f, "_video_local", None)
        media_block = ""
        valid_screenshots = [ss for ss in screenshot_data if ss.get("data_uri")]
        if valid_screenshots:
            imgs = []
            for ss in valid_screenshots:
                retry_label = ' <span class="tag-retry">retry</span>' if ss["is_retry"] else ""
                imgs.append(
                    f'<div class="artifact-thumb">'
                    f'<a href="{ss["data_uri"]}" target="_blank"><img src="{ss["data_uri"]}" loading="lazy" alt="{esc(ss["name"])}"></a>'
                    f'<div class="artifact-label">{esc(ss["name"])}{retry_label}</div></div>'
                )
            media_block += f'<details open><summary>Screenshots ({len(valid_screenshots)})</summary><div class="artifact-grid">{"".join(imgs)}</div></details>'
        if video_local:
            video_url = getattr(f, "_video_url", None)
            video_src = video_url or video_local
            video_link = f'<div class="artifact-label"><a href="{esc(video_url)}" target="_blank">Open video in Jenkins</a></div>' if video_url else ''
            media_block += f'<details><summary>Video</summary><video controls preload="none" class="artifact-video"><source src="{esc(video_src)}" type="video/mp4"></video>{video_link}</details>'

        card_status = "retry" if is_rp else "failed"
        failure_cards.append(f"""
        <div class="card failure-card" data-status="{card_status}">
          <div class="failure-header">
            <span class="failure-name">{esc(fname)}</span>{rerun_html}
          </div>
          <div class="failure-badges">{badges}</div>
          {rerun_explanation_html}{media_block}{err_block}{stack_block}{git_html}
        </div>""")

    failures_section = ""
    if failure_cards:
        real_count = sum(1 for fa in analysis_with_reruns.get("failure_analyses", []) if not getattr(fa.failure, "_is_retry_pass", False))
        retry_count = len(failure_cards) - real_count
        failures_section = f"""
        <section class="section">
          <h2>Detailed Failures ({len(failure_cards)})</h2>
          <div class="filter-bar" id="failure-filter-bar">
            <span class="filter-label">Filter:</span>
            <button class="failure-filter-btn active" data-ffilter="all">All ({len(failure_cards)})</button>
            <button class="failure-filter-btn active" data-ffilter="failed"><span class="icon-fail">&#10008;</span> Failed ({real_count})</button>
            <button class="failure-filter-btn active" data-ffilter="retry"><span class="icon-warn">&#9888;</span> Passed on retry ({retry_count})</button>
          </div>
          {"".join(failure_cards)}
        </section>"""

    cluster_section = ""
    if cluster_analysis:
        ph = cluster_analysis.get("pod_health", {})
        ns = esc(cluster_analysis.get("namespace", ""))
        cluster_section = f"""
        <section class="section">
          <h2>Cluster Health</h2>
          <div class="card">
            <p><strong>Namespace:</strong> <code>{ns}</code></p>
            <div class="summary-row">
              <div class="summary-card"><div class="summary-value">{ph.get("total",0)}</div><div class="summary-label">Total Pods</div></div>
              <div class="summary-card card-pass-bg"><div class="summary-value">{ph.get("running",0)}</div><div class="summary-label">Running</div></div>
              <div class="summary-card card-fail-bg"><div class="summary-value">{ph.get("failed",0)}</div><div class="summary-label">Failed</div></div>
            </div>
          </div>
        </section>"""

    merges_section = ""
    if recent_merges:
        merge_items = []
        for m in recent_merges[:10]:
            repo = esc(m.get("repository", ""))
            author = esc(m.get("author", ""))
            subject = esc(m.get("subject", "")[:100])
            sha_short = esc(m.get("sha", "")[:8])
            full_sha = m.get("full_sha", m.get("sha", ""))
            mr_num = m.get("mr_number", "")
            merged = m.get("merged")
            merge_badge = '<span style="color:var(--green)">&#x2705; merged</span>' if merged else '<span style="color:var(--orange)">&#x26A0;&#xFE0F; unmerged</span>'
            if repo == "Jenkins" and mr_num:
                ref_link = f'<a href="https://gitlab.cee.redhat.com/ods/jenkins/-/merge_requests/{esc(str(mr_num))}">!{esc(str(mr_num))}</a>'
            elif repo == "Dashboard" and mr_num:
                ref_link = f'<a href="https://github.com/opendatahub-io/odh-dashboard/pull/{esc(str(mr_num))}">#{esc(str(mr_num))}</a>'
            else:
                ref_link = f'<code>{sha_short}</code>'
            merge_status = f" ({merge_badge})" if merged is not None else ""
            merge_items.append(f"<li><strong>[{repo}]</strong> {ref_link}{merge_status} by {author} &mdash; {subject}</li>")
        merges_section = f"""
        <section class="section">
          <h2>Recent Commits</h2>
          <ul>{"".join(merge_items)}</ul>
        </section>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(name)} E2E Report - Build #{build_num}</title>
<style>
:root {{
  --bg: #1a1a2e; --bg2: #16213e; --bg3: #0f3460; --card: #1e2745; --border: #2a3a5c;
  --text: #e0e0e0; --text2: #a0aec0; --green: #48bb78; --red: #fc5c65;
  --yellow: #f6e05e; --blue: #63b3ed; --orange: #ed8936;
  --mono: "SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;
}}
*,*::before,*::after {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:var(--bg); color:var(--text); line-height:1.6; }}
a {{ color:var(--blue); text-decoration:none; }}
a:hover {{ text-decoration:underline; }}
code,.mono {{ font-family:var(--mono); font-size:0.9em; }}
.container {{ max-width:1100px; margin:0 auto; padding:1rem; }}
.header {{ background:linear-gradient(135deg,var(--bg2),var(--bg3)); padding:2rem 1.5rem;
  border-bottom:2px solid var(--border); text-align:center; }}
.header h1 {{ margin:0 0 .5rem; font-size:1.8rem; }}
.header-meta {{ color:var(--text2); font-size:0.95rem; }}
.badge {{ display:inline-block; padding:4px 14px; border-radius:20px; font-weight:700;
  font-size:0.85rem; margin-left:8px; letter-spacing:.5px; }}
.badge-pass {{ background:var(--green); color:#1a1a2e; }}
.badge-fail {{ background:var(--red); color:#fff; }}
.badge-timeout {{ background:var(--orange); color:#1a1a2e; }}
.summary-row {{ display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }}
.summary-card {{ flex:1; min-width:120px; background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:1rem; text-align:center; transition:transform .15s; }}
.summary-card:hover {{ transform:translateY(-2px); }}
.summary-value {{ font-size:2rem; font-weight:700; }}
.summary-label {{ color:var(--text2); font-size:0.85rem; text-transform:uppercase; letter-spacing:.5px; }}
.card-pass-bg {{ border-color:var(--green); }}
.card-pass-bg .summary-value {{ color:var(--green); }}
.card-fail-bg {{ border-color:var(--red); }}
.card-fail-bg .summary-value {{ color:var(--red); }}
.section {{ margin:2rem 0; }}
.section h2 {{ font-size:1.3rem; border-bottom:1px solid var(--border); padding-bottom:.4rem; }}
.card {{ background:var(--card); border:1px solid var(--border); border-radius:10px;
  padding:1rem 1.2rem; margin:.8rem 0; transition:border-color .15s; }}
.card:hover {{ border-color:var(--blue); }}
.card-fail {{ border-left:4px solid var(--red); }}
.stage-card {{ background:var(--card); border:1px solid var(--border); border-radius:8px;
  padding:.8rem 1rem; margin:.5rem 0; }}
.stage-header {{ display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; }}
.stage-stats {{ color:var(--text2); font-size:0.9rem; margin-left:auto; }}
.progress-bar {{ display:flex; height:6px; border-radius:3px; overflow:hidden; margin:.5rem 0; background:#2d3748; }}
.progress-fill {{ background:var(--green); transition:width .3s; }}
.progress-fail {{ background:var(--red); transition:width .3s; }}
.failure-list {{ list-style:none; padding-left:1.5rem; margin:.4rem 0 0; font-size:0.9rem; }}
.failure-list li {{ padding:4px 0; }}
.test-detail summary {{ list-style:none; cursor:pointer; }}
.test-detail summary::-webkit-details-marker {{ display:none; }}
.test-detail summary::before {{ content:"▶ "; font-size:0.7em; color:var(--text2); transition:transform .2s; display:inline-block; }}
.test-detail[open] summary::before {{ content:"▼ "; }}
.test-artifacts {{ margin:.6rem 0 .2rem 1.2rem; padding:.6rem; background:var(--bg2); border:1px solid var(--border);
  border-radius:8px; }}
.artifact-section {{ margin:.5rem 0; }}
.artifact-title {{ font-size:0.82rem; font-weight:600; color:var(--text2); text-transform:uppercase; letter-spacing:.3px; margin-bottom:.3rem; }}
.artifact-grid {{ display:flex; gap:.6rem; flex-wrap:wrap; }}
.artifact-thumb {{ max-width:320px; }}
.artifact-thumb img {{ width:100%; border-radius:6px; border:1px solid var(--border); cursor:pointer; transition:transform .15s, border-color .15s; }}
.artifact-thumb img:hover {{ transform:scale(1.03); border-color:var(--blue); }}
.artifact-label {{ font-size:0.72rem; color:var(--text2); margin-top:2px; word-break:break-all; }}
.artifact-video {{ width:100%; max-width:640px; border-radius:6px; border:1px solid var(--border); background:#000; }}
.icon-pass {{ color:var(--green); }}
.icon-fail {{ color:var(--red); }}
.icon-warn {{ color:var(--yellow); }}
.icon-timeout {{ color:var(--orange); }}
.icon-skip {{ color:var(--text2); }}
.filter-bar {{ display:flex; align-items:center; gap:.5rem; flex-wrap:wrap; margin:.8rem 0; }}
.filter-label {{ font-size:0.85rem; color:var(--text2); font-weight:600; }}
.filter-btn {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:4px 12px;
  font-size:0.8rem; color:var(--text2); cursor:pointer; transition:all .15s; font-family:inherit; }}
.filter-btn:hover {{ border-color:var(--blue); }}
.filter-btn.active {{ background:var(--bg3); border-color:var(--blue); color:var(--text); }}
.filter-btn.active[data-filter="failed"] {{ border-color:var(--red); }}
.filter-btn.active[data-filter="retry"] {{ border-color:var(--yellow); }}
.filter-btn.active[data-filter="passed"] {{ border-color:var(--green); }}
.filter-btn.active[data-filter="skipped"] {{ border-color:var(--text2); }}
.tag-cat {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.75rem;
  background:var(--bg3); color:var(--text2); margin-left:4px; }}
.tag-timeout {{ background:#744210; color:var(--orange); }}
.tag-assertion {{ background:#2a4365; color:var(--blue); }}
.tag-stage {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.75rem;
  background:#2d3748; color:var(--text2); }}
.tag-retry {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.75rem;
  background:#744210; color:var(--yellow); }}
.tag-test-timeout {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.75rem;
  background:#7c2d12; color:var(--orange); font-weight:600; }}
.tag-pass {{ display:inline-block; padding:2px 10px; border-radius:10px; font-size:0.8rem;
  background:var(--green); color:#1a1a2e; font-weight:600; }}
.tag-rerun-pass {{ display:inline-block; padding:2px 10px; border-radius:10px; font-size:0.8rem;
  background:#39ff14; color:#1a1a2e; font-weight:700; text-shadow:0 0 6px rgba(57,255,20,0.4); }}
.tag-fail-sm {{ display:inline-block; padding:2px 10px; border-radius:10px; font-size:0.8rem;
  background:var(--red); color:#fff; font-weight:600; }}
.rerun-explanation {{ margin:.5rem 0; padding:.5rem .8rem; border-left:3px solid var(--blue);
  background:var(--bg2); border-radius:0 6px 6px 0; font-size:0.88rem; }}
.failure-filter-btn {{ background:var(--card); border:1px solid var(--border); border-radius:16px; padding:4px 12px;
  font-size:0.8rem; color:var(--text2); cursor:pointer; transition:all .15s; font-family:inherit; }}
.failure-filter-btn:hover {{ border-color:var(--blue); }}
.failure-filter-btn.active {{ background:var(--bg3); border-color:var(--blue); color:var(--text); }}
.failure-filter-btn.active[data-ffilter="failed"] {{ border-color:var(--red); }}
.failure-filter-btn.active[data-ffilter="retry"] {{ border-color:var(--yellow); }}
.legend {{ margin-top:.8rem; font-size:0.85rem; }}
.legend summary {{ color:var(--text2); cursor:pointer; font-size:0.85rem; }}
.legend-grid {{ display:flex; gap:1.5rem; flex-wrap:wrap; padding:.6rem 0; }}
.legend-group {{ min-width:200px; }}
.legend-title {{ font-weight:600; color:var(--text2); text-transform:uppercase; font-size:0.75rem; letter-spacing:.3px; margin-bottom:.3rem; }}
.legend-group > div {{ padding:2px 0; }}
.failure-card {{ border-left:4px solid var(--red); }}
.failure-header {{ display:flex; align-items:center; gap:.6rem; flex-wrap:wrap; }}
.failure-name {{ font-weight:700; font-size:1.05rem; }}
.failure-badges {{ margin:.3rem 0; }}
.code-block {{ background:#0d1117; padding:.8rem; border-radius:6px; overflow-x:auto;
  font-size:0.82rem; line-height:1.5; white-space:pre-wrap; word-break:break-all; max-height:300px; overflow-y:auto; }}
details {{ margin:.4rem 0; }}
details summary {{ cursor:pointer; color:var(--blue); font-size:0.9rem; }}
details summary:hover {{ text-decoration:underline; }}
.table-wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:0.88rem; }}
th,td {{ padding:8px 10px; text-align:left; border-bottom:1px solid var(--border); }}
th {{ background:var(--bg2); color:var(--text2); font-weight:600; text-transform:uppercase; font-size:0.78rem; letter-spacing:.3px; }}
.git-warn {{ color:var(--yellow); font-size:0.88rem; margin:.2rem 0; }}
@media(max-width:640px) {{
  .summary-row {{ flex-direction:column; }}
  .stage-header {{ flex-direction:column; align-items:flex-start; }}
  .stage-stats {{ margin-left:0; }}
  .header h1 {{ font-size:1.3rem; }}
}}
</style>
</head>
<body>
<div class="header">
  <h1>{esc(name)} E2E Analysis &mdash; Build #{build_num} <span class="badge {status_cls}">{status_label}</span></h1>
  <div class="header-meta">
    {now} &nbsp;|&nbsp; <a href="{esc(build_url)}">Jenkins Build</a>
    {"&nbsp;|&nbsp; Nightly: " + esc(nightly_info.get("cluster_name","")) if nightly_info.get("is_nightly") else ""}
  </div>
</div>
<div class="container">
  <div class="summary-row">
    <div class="summary-card"><div class="summary-value">{total}</div><div class="summary-label">Total Tests</div></div>
    <div class="summary-card card-pass-bg"><div class="summary-value">{passed}</div><div class="summary-label">Passed</div></div>
    <div class="summary-card card-fail-bg"><div class="summary-value">{failed}</div><div class="summary-label">Failed</div></div>
    <div class="summary-card"><div class="summary-value">{pass_rate}%</div><div class="summary-label">Pass Rate</div></div>
  </div>
  <section class="section">
    <h2>Stage Breakdown ({len(all_display_stages)} stages)</h2>
    <details class="legend"><summary>Legend</summary>
    <div class="legend-grid">
      <div class="legend-group"><div class="legend-title">Stage Status</div>
        <div><span class="icon-pass">&#10004;</span> All tests passed (or only flaky failures)</div>
        <div><span class="icon-fail">&#10008;</span> One or more tests failed</div>
        <div><span class="icon-timeout">&#9201;</span> Stage killed by timeout</div>
      </div>
      <div class="legend-group"><div class="legend-title">Test Status</div>
        <div><span class="icon-fail">&#10008;</span> Test failed</div>
        <div><span class="icon-warn">&#9888;</span> Test failed initially but passed on retry (flaky)</div>
      </div>
      <div class="legend-group"><div class="legend-title">Badges</div>
        <div><span class="tag-cat">unknown</span> Failure category (unknown, timeout, assertion, element_not_found, network, auth, resource)</div>
        <div><span class="tag-test-timeout">test-timeout</span> Test exceeded Cypress time limit</div>
        <div><span class="tag-retry">passed on retry</span> Test passed on a subsequent attempt</div>
        <div><span class="tag-cat tag-timeout">timeout</span> Failure caused by a timeout</div>
        <div><span class="tag-cat tag-assertion">assertion</span> Assertion or expectation failure</div>
      </div>
    </div>
    </details>
    <div class="filter-bar">
      <span class="filter-label">Filter:</span>
      <button class="filter-btn active" data-filter="all">All</button>
      <button class="filter-btn active" data-filter="failed"><span class="icon-fail">&#10008;</span> Failed</button>
      <button class="filter-btn active" data-filter="retry"><span class="icon-warn">&#9888;</span> Retry</button>
      <button class="filter-btn active" data-filter="passed"><span class="icon-pass">&#10004;</span> Passed</button>
      <button class="filter-btn active" data-filter="skipped"><span class="icon-skip">&#8212;</span> Skipped</button>
    </div>
    {stages_section}
  </section>
  {pipeline_section}
  {images_section}
  {failures_section}
  {cluster_section}
  {merges_section}
  <footer style="text-align:center;color:var(--text2);font-size:0.8rem;padding:2rem 0 1rem;border-top:1px solid var(--border);margin-top:2rem;">
    Generated by Dashboard Test Analyser &mdash; {now}
  </footer>
</div>
<div id="lightbox" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:1000;cursor:zoom-out;align-items:center;justify-content:center" onclick="this.style.display='none'">
  <img id="lb-img" style="max-width:95vw;max-height:95vh;border-radius:8px;box-shadow:0 0 40px rgba(0,0,0,.5)">
</div>
<script>
document.querySelectorAll('.artifact-thumb a').forEach(a=>{{
  a.addEventListener('click',e=>{{
    e.preventDefault();
    const lb=document.getElementById('lightbox');
    document.getElementById('lb-img').src=a.href;
    lb.style.display='flex';
  }});
}});
document.addEventListener('keydown',e=>{{if(e.key==='Escape')document.getElementById('lightbox').style.display='none'}});

// Filter logic - stage breakdown
(function(){{
  const filters={{}};
  document.querySelectorAll('.filter-btn[data-filter]').forEach(btn=>{{
    const f=btn.dataset.filter;
    if(f!=='all') filters[f]=true;
    btn.addEventListener('click',()=>{{
      if(f==='all'){{
        for(const k in filters) filters[k]=true;
      }} else {{
        filters[f]=!filters[f];
      }}
      document.querySelectorAll('.filter-btn[data-filter]').forEach(b=>{{
        const bf=b.dataset.filter;
        if(bf==='all') b.classList.toggle('active',Object.values(filters).every(v=>v));
        else b.classList.toggle('active',filters[bf]);
      }});
      document.querySelectorAll('li[data-status]').forEach(li=>{{
        li.style.display=filters[li.dataset.status]?'':'none';
      }});
    }});
  }});
}})();
// Filter logic - detailed failure cards
(function(){{
  const ff={{}};
  document.querySelectorAll('.failure-filter-btn[data-ffilter]').forEach(btn=>{{
    const f=btn.dataset.ffilter;
    if(f!=='all') ff[f]=true;
    btn.addEventListener('click',()=>{{
      if(f==='all'){{
        for(const k in ff) ff[k]=true;
      }} else {{
        ff[f]=!ff[f];
      }}
      document.querySelectorAll('.failure-filter-btn[data-ffilter]').forEach(b=>{{
        const bf=b.dataset.ffilter;
        if(bf==='all') b.classList.toggle('active',Object.values(ff).every(v=>v));
        else b.classList.toggle('active',ff[bf]);
      }});
      document.querySelectorAll('.failure-card[data-status]').forEach(card=>{{
        card.style.display=ff[card.dataset.status]?'':'none';
      }});
    }});
  }});
}})();
</script>
</body>
</html>"""


def compare_errors(original_error: str, rerun_error: str) -> dict:
    """Compare original and rerun errors to see if they're the same"""
    def normalize_error(error):
        if not error:
            return ""
        error = re.sub(r':\d+:\d+', '', error)
        error = re.sub(r'at \S+:\d+', '', error)
        return error.lower().strip()

    norm_original = normalize_error(original_error)
    norm_rerun = normalize_error(rerun_error)

    return {
        'same_error': norm_original == norm_rerun if norm_original and norm_rerun else False,
        'similarity': 'identical' if norm_original == norm_rerun else 'different'
    }


def classify_failure_result(rerun_result: dict, git_info: dict) -> dict:
    """
    Classify a test failure based on rerun results and git analysis.
    
    IMPROVED classification logic that handles:
    - Flaky tests (same code, passes on rerun)
    - Quarantined tests (skipped in main but not at build time)
    - Fixed in main (code changed, passes on rerun)
    - Consistent failures (fails on rerun with same error)
    - Different failures (fails on rerun with different error)
    
    Returns:
        dict with:
        - classification: str (one of the above)
        - confidence: str ('high', 'medium', 'low')
        - explanation: str (human-readable explanation)
        - action_required: bool
        - suggested_action: str
    """
    result = {
        'classification': 'unknown',
        'confidence': 'low',
        'explanation': '',
        'action_required': True,
        'suggested_action': '',
        'emoji': '❓'
    }
    
    if not rerun_result or not rerun_result.get('attempted'):
        result['classification'] = 'not_tested'
        result['explanation'] = 'Test was not rerun'
        result['suggested_action'] = 'Manually rerun the test to determine if it is flaky'
        return result
    
    rerun_passed = rerun_result.get('success', False)
    ran_on_main = rerun_result.get('ran_on_main', True)
    
    # Extract git analysis info
    quarantined_in_main = git_info.get('quarantined', False)
    quarantined_after_build = git_info.get('quarantined_in_main_only', False)
    file_changed = git_info.get('file_changed', False)
    needs_maintenance = git_info.get('needs_maintenance', False)
    bug_refs = git_info.get('bug_references', [])
    
    if rerun_passed:
        # Test passed on rerun - determine why
        
        if quarantined_after_build:
            # Test was quarantined AFTER the build - explains the "pass"
            result['classification'] = 'quarantined_after_build'
            result['confidence'] = 'high'
            result['explanation'] = f'Test was quarantined in main AFTER this build. The rerun passed because the test is now skipped.'
            result['action_required'] = False
            result['suggested_action'] = f'Wait for fix in {", ".join(bug_refs) if bug_refs else "linked Jira issue"}'
            result['emoji'] = '🔒'
            
        elif quarantined_in_main and ran_on_main:
            # Test is quarantined in main (may or may not have been at build time)
            result['classification'] = 'quarantined'
            result['confidence'] = 'high'
            result['explanation'] = 'Test is quarantined (skipped) in main branch. Rerun passed because test was skipped.'
            result['action_required'] = False
            result['suggested_action'] = f'Monitor {", ".join(bug_refs) if bug_refs else "linked Jira issue"} for fix'
            result['emoji'] = '🔒'
            
        elif file_changed and ran_on_main:
            # Code changed between build and main, test passes on main
            result['classification'] = 'fixed_in_main'
            result['confidence'] = 'high'
            result['explanation'] = 'Test file changed since build. The issue appears to be fixed in main.'
            result['action_required'] = False
            result['suggested_action'] = 'Update image to pick up the fix'
            result['emoji'] = '✅'
            
        else:
            # Same code, test passes - truly flaky
            result['classification'] = 'flaky'
            result['confidence'] = 'medium' if ran_on_main else 'high'
            result['explanation'] = 'Test passed on rerun with same code. This is an intermittent/flaky failure.'
            result['action_required'] = True
            result['suggested_action'] = 'Investigate flaky test root cause (timing, race condition, etc.)'
            result['emoji'] = '🎲'
            
    else:
        # Test failed on rerun
        
        if needs_maintenance:
            result['classification'] = 'needs_maintenance'
            result['confidence'] = 'high'
            result['explanation'] = 'Test is marked as needing maintenance (@Maintain tag).'
            result['action_required'] = True
            result['suggested_action'] = f'Fix automation issue in {", ".join(bug_refs) if bug_refs else "test code"}'
            result['emoji'] = '🔧'
            
        elif file_changed and ran_on_main:
            # Code changed but still fails - different issue
            result['classification'] = 'still_failing_after_changes'
            result['confidence'] = 'medium'
            result['explanation'] = 'Test file changed but still fails. May be a different/new issue.'
            result['action_required'] = True
            result['suggested_action'] = 'Investigate if this is the same or a new failure'
            result['emoji'] = '⚠️'
            
        else:
            # Same code, same failure - consistent issue
            result['classification'] = 'consistent_failure'
            result['confidence'] = 'high'
            result['explanation'] = 'Test consistently fails on rerun. This is a real bug, not flaky.'
            result['action_required'] = True
            result['suggested_action'] = 'File Jira ticket and investigate root cause'
            result['emoji'] = '❌'
    
    return result


def extract_exception_type(error_message: str) -> str:
    """Extract the exception type from an error message for grouping"""
    if not error_message:
        return "UnknownError"

    # Common patterns
    if 'AssertionError' in error_message:
        return "AssertionError"
    elif 'TimeoutError' in error_message or 'Timed out' in error_message:
        return "TimeoutError"
    elif 'NetworkError' in error_message or 'ECONNREFUSED' in error_message:
        return "NetworkError"
    elif 'ElementNotFound' in error_message or 'Expected to find element' in error_message:
        return "ElementNotFoundError"
    elif 'CypressError' in error_message:
        return "CypressError"
    elif 'ReferenceError' in error_message:
        return "ReferenceError"
    elif 'TypeError' in error_message:
        return "TypeError"
    else:
        # Try to extract first line which usually contains the error type
        first_line = error_message.split('\n')[0].strip()
        if ':' in first_line:
            # Format like "AssertionError: message"
            potential_type = first_line.split(':')[0].strip()
            if 'Error' in potential_type:
                return potential_type
        return "GenericError"


def group_failures_by_exception(failures: list) -> dict:
    """Group test failures by their exception type"""
    groups = {}
    for failure in failures:
        exc_type = extract_exception_type(failure.error_message)
        if exc_type not in groups:
            groups[exc_type] = []
        groups[exc_type].append(failure)
    return groups


async def check_all_namespaces(inspector):
    """Check ALL namespaces for pod health"""
    import subprocess

    all_namespace_health = []

    try:
        result = subprocess.run(
            ['oc', 'get', 'namespaces', '-o', 'jsonpath={.items[*].metadata.name}'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            namespaces = result.stdout.strip().split()

            for ns in namespaces:
                pod_result = subprocess.run(
                    ['oc', 'get', 'pods', '-n', ns, '-o', 'json'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                if pod_result.returncode == 0:
                    import json
                    pods_data = json.loads(pod_result.stdout)

                    total_pods = len(pods_data.get('items', []))
                    running_pods = 0
                    failed_pods = 0
                    problems = []

                    for pod in pods_data.get('items', []):
                        pod_name = pod['metadata']['name']
                        status = pod.get('status', {})
                        phase = status.get('phase', 'Unknown')

                        if phase == 'Running':
                            running_pods += 1
                        elif phase in ['Failed', 'CrashLoopBackOff', 'Error']:
                            failed_pods += 1
                            problems.append({
                                'pod': pod_name,
                                'issue': f"Phase: {phase}"
                            })

                    is_cypress_related = 'cypress' in ns.lower()

                    if total_pods > 0:
                        all_namespace_health.append({
                            'namespace': ns,
                            'total_pods': total_pods,
                            'running_pods': running_pods,
                            'failed_pods': failed_pods,
                            'problems': problems,
                            'is_cypress_related': is_cypress_related,
                            'needs_cleanup': is_cypress_related and total_pods > 0
                        })

    except Exception as e:
        print(f"  Warning: Could not check all namespaces: {e}")

    return all_namespace_health


def detect_commit_sync_issues(console_output: str) -> dict:
    """
    Detect when dashboard commit cannot be determined and tests fall back to main.
    This is a CRITICAL issue that causes test/code mismatch.
    """
    issues = {
        'commit_detection_failed': False,
        'fell_back_to_main': False,
        'deployed_image_registry': None,
        'branch_used_for_tests': None,
        'warning_message': None,
        'severity': 'none'
    }

    if not console_output:
        return issues

    # Look for ERROR message about commit detection failure
    if '[ERROR] ODH-Dashboard commit' in console_output and 'could not be determined' in console_output:
        issues['commit_detection_failed'] = True
        issues['severity'] = 'critical'

        # Extract the image URI from error message
        error_pattern = r'\[ERROR\] ODH-Dashboard commit of \'([^\']+)\' could not be determined'
        match = re.search(error_pattern, console_output)
        if match:
            image_uri = match.group(1)
            issues['deployed_image_registry'] = image_uri

            # Detect registry type
            if 'registry.redhat.io' in image_uri:
                issues['warning_message'] = 'Production registry image (registry.redhat.io) lacks commit metadata - tracer cannot extract commit info'
            elif 'quay.io' in image_uri:
                issues['warning_message'] = 'Quay.io image should have metadata - investigate why tracer failed'

    # Look for WARN message about fallback to main
    if 'Fallback to default Branch (main)' in console_output or 'No ODH-Dashboard commit identified' in console_output:
        issues['fell_back_to_main'] = True
        issues['branch_used_for_tests'] = 'main'

        if not issues['warning_message']:
            issues['warning_message'] = 'Tests running against main branch but deployed image commit unknown'

    # If both conditions met, it's a critical sync issue
    if issues['commit_detection_failed'] and issues['fell_back_to_main']:
        issues['severity'] = 'critical'
        issues['warning_message'] = 'CRITICAL: Test/code mismatch - tests run from main but deployed image age unknown!'

    return issues


def analyze_image_registry_type(image_uri: str) -> dict:
    """Determine if image is from production or development registry"""
    registry_info = {
        'registry_type': 'unknown',
        'has_metadata': None,
        'tracer_compatible': None,
        'notes': None
    }

    if not image_uri:
        return registry_info

    if 'registry.redhat.io' in image_uri:
        registry_info['registry_type'] = 'production'
        registry_info['has_metadata'] = False
        registry_info['tracer_compatible'] = False
        registry_info['notes'] = 'Production registry images lack commit metadata for tracer'
    elif 'quay.io' in image_uri:
        registry_info['registry_type'] = 'development'
        registry_info['has_metadata'] = True
        registry_info['tracer_compatible'] = True
        registry_info['notes'] = 'Development registry with full metadata support'
    elif 'brew.registry.redhat.io' in image_uri:
        registry_info['registry_type'] = 'brew'
        registry_info['has_metadata'] = True
        registry_info['tracer_compatible'] = True
        registry_info['notes'] = 'Brew registry for IIB images'

    return registry_info


def _find_default_branch(repo_path: str) -> str:
    """Detect the default branch (master or main) in a repo."""
    for branch in ('master', 'main'):
        r = subprocess.run(
            ['git', 'rev-parse', '--verify', f'refs/heads/{branch}'],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return branch
    return 'master'


def _is_commit_on_branch(repo_path: str, sha: str, branch: str) -> bool:
    """Check if a commit is reachable from a branch."""
    r = subprocess.run(
        ['git', 'merge-base', '--is-ancestor', sha, branch],
        cwd=repo_path, capture_output=True, text=True, timeout=10,
    )
    return r.returncode == 0


def check_recent_commits_single_repo(repo_path: str, repo_name: str, hours_back: int = 24) -> list:
    """
    Check a single git repo for recent commits.

    Args:
        repo_path: Path to the git repository
        repo_name: Name/label for this repository (e.g., 'Dashboard', 'Jenkins')
        hours_back: How many hours back to check (default 24)

    Returns:
        List of recent commits with metadata
    """
    recent_commits = []

    try:
        default_branch = _find_default_branch(repo_path)

        # Get ALL commits (not just merges) with full info
        result = subprocess.run(
            ['git', 'log', f'--since={hours_back} hours ago',
             '--pretty=format:%H|%h|%an|%ae|%ai|%s|%b', '--no-decorate'],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                parts = line.split('|', 6)
                if len(parts) >= 6:
                    full_sha, short_sha, author, email, timestamp, subject = parts[:6]
                    body = parts[6] if len(parts) > 6 else ''

                    # Extract MR number from commit message if present
                    mr_number = None
                    mr_match = re.search(r'!(\d+)', subject)
                    if mr_match:
                        mr_number = mr_match.group(1)

                    merged = _is_commit_on_branch(repo_path, full_sha, default_branch)

                    # Check if commit relates to Jenkins/pipeline/tests
                    # For Jenkins repo: ALL commits are pipeline-related by definition
                    # For Dashboard repo: check for specific keywords
                    if repo_name == 'Jenkins':
                        # Jenkins config repo - everything is pipeline-related
                        is_pipeline_related = True
                    else:
                        # Dashboard repo - check for specific keywords
                        is_pipeline_related = any(keyword in subject.lower() or keyword in body.lower()
                                                 for keyword in ['jenkins', 'pipeline', 'ci', 'test', 'cypress',
                                                               'e2e', 'cluster', 'config', 'deploy', 'build'])

                    commit_info = {
                        'sha': short_sha,
                        'full_sha': full_sha,
                        'author': author,
                        'email': email,
                        'timestamp': timestamp,
                        'subject': subject,
                        'body': body[:200] if body else '',  # First 200 chars of body
                        'mr_number': mr_number,
                        'is_pipeline_related': is_pipeline_related,
                        'repository': repo_name,
                        'merged': merged,
                    }

                    recent_commits.append(commit_info)

        return recent_commits

    except Exception as e:
        print(f"  Warning: Could not check {repo_name} repo: {e}")
        return []


def check_recent_gitlab_merges(dashboard_repo_path: str, jenkins_repo_path: str, hours_back: int = 24) -> list:
    """
    Check BOTH Dashboard and Jenkins repos for recent commits that might have caused pipeline issues.

    Args:
        dashboard_repo_path: Path to the dashboard git repository
        jenkins_repo_path: Path to the Jenkins configuration git repository
        hours_back: How many hours back to check (default 24)

    Returns:
        Combined list of recent commits from both repos, sorted by timestamp (newest first)
    """
    all_commits = []

    # Check Dashboard repository
    print(f"      Checking Dashboard repository...")
    dashboard_commits = check_recent_commits_single_repo(dashboard_repo_path, 'Dashboard', hours_back)
    all_commits.extend(dashboard_commits)
    if dashboard_commits:
        print(f"         Found {len(dashboard_commits)} commit(s) in Dashboard repo")

    # Check Jenkins repository
    print(f"      Checking Jenkins configuration repository...")
    jenkins_commits = check_recent_commits_single_repo(jenkins_repo_path, 'Jenkins', hours_back)
    all_commits.extend(jenkins_commits)
    if jenkins_commits:
        print(f"         Found {len(jenkins_commits)} commit(s) in Jenkins repo")

    # Sort by timestamp (newest first)
    all_commits.sort(key=lambda x: x['timestamp'], reverse=True)

    return all_commits


def is_nightly_cron_build(console_output: str) -> dict:
    """
    Detect if this build was triggered by a nightly cron.

    The nightly cron builds are identified by CLUSTER_NAME parameter:
    - dash-e2e-rhoai for RHOAI nightly
    - dash-e2e-odh for ODH nightly

    Returns:
        dict with:
        - is_nightly: True if this is a nightly cron build
        - cluster_name: The cluster name (dash-e2e-rhoai or dash-e2e-odh)
    """
    result = {
        'is_nightly': False,
        'cluster_name': None,
    }

    if not console_output:
        return result

    # Look for CLUSTER_NAME parameter in console output
    cluster_patterns = [
        r'CLUSTER_NAME[=:]\s*["\']?(dash-e2e-rhoai)["\']?',
        r'CLUSTER_NAME[=:]\s*["\']?(dash-e2e-odh)["\']?',
        r'Running.*cluster.*["\']?(dash-e2e-rhoai)["\']?',
        r'Running.*cluster.*["\']?(dash-e2e-odh)["\']?',
    ]

    for pattern in cluster_patterns:
        match = re.search(pattern, console_output, re.IGNORECASE)
        if match:
            result['is_nightly'] = True
            result['cluster_name'] = match.group(1)
            break

    return result


async def main():
    import sys

    import argparse

    parser = argparse.ArgumentParser(
        description="Comprehensive RHOAI/ODH nightly build analyzer",
        usage="python comprehensive_analysis.py <build_number|latest> [odh|rhoai] [options]"
    )
    parser.add_argument('build', help="Build number or 'latest'")
    parser.add_argument('variant', nargs='?', default='ODH', help="Platform variant (default: ODH)")
    parser.add_argument('--enable-trend', action='store_true', help="Enable trend analysis")
    parser.add_argument('--no-artifacts-download', action='store_true', help="Skip downloading screenshots/videos")
    parser.add_argument('--skip-jira', action='store_true', help="Skip Jira lock ticket and publishing")
    parser.add_argument('--skip-rerun', action='store_true', help="Skip test reruns")
    parser.add_argument('--skip-slack', action='store_true', help="Skip Slack message posting")
    parser.add_argument('-y', '--yes', action='store_true', help="Auto-accept prompts (non-interactive mode)")

    args = parser.parse_args()

    build_arg = args.build
    variant = args.variant.upper()
    enable_trend_analysis = args.enable_trend
    download_artifacts = not args.no_artifacts_download
    skip_jira = args.skip_jira
    skip_rerun = args.skip_rerun
    skip_slack = args.skip_slack
    auto_yes = args.yes or not sys.stdin.isatty()
    
    # Create jenkins client for build lookup
    jenkins_cli = jenkins_client.JenkinsClient(
        jenkins_url=os.getenv("JENKINS_URL", "https://your-jenkins-url.example.com"),
        jenkins_token=os.getenv("JENKINS_TOKEN", ""),
        jenkins_username=os.getenv("JENKINS_USER", ""),
        jenkins_password=os.getenv("JENKINS_TOKEN", "")
    )
    
    build_time = None

    # Auto-detect latest build if requested
    if build_arg.lower() == "latest":
        print(f"🔍 Finding latest build for {variant}...")
        
        job_path = Config.DASHBOARD_TESTS_JOB_PATH
        
        # Get recent builds and find the latest for this variant
        async with httpx.AsyncClient(verify=Config.SSL_VERIFY, timeout=120.0) as client:
            url = f"{jenkins_cli.jenkins_url}/job/{job_path.replace('/', '/job/')}/api/json?tree=builds[number,timestamp,result,description]{{0,30}}"
            if ':' in jenkins_cli.jenkins_token:
                username, token = jenkins_cli.jenkins_token.split(':', 1)
                auth = (username, token)
                response = await client.get(url, auth=auth)
            else:
                headers = {"Authorization": f"Bearer {jenkins_cli.jenkins_token}"}
                response = await client.get(url, headers=headers)
            
            response.raise_for_status()
            data = response.json()
            
            # Find the most recent build for this variant
            target_description = f'dash-e2e-{variant.lower()}'
            latest_build = None
            
            for build in data.get('builds', []):
                description = (build.get('description') or '').lower()
                if target_description in description:
                    latest_build = build
                    break
            
            if not latest_build:
                print(f"❌ Could not find any {variant} builds in recent history")
                sys.exit(1)
            
            build_num = latest_build['number']
            build_time = datetime.fromtimestamp(latest_build['timestamp'] / 1000)
            hours_ago = (datetime.now() - build_time).total_seconds() / 3600
            
            print(f"✅ Found latest {variant} build: #{build_num}")
            print(f"   Build time: {build_time.strftime('%Y-%m-%d %H:%M:%S')} ({hours_ago:.1f} hours ago)")
            print()
    else:
        # Input validation: ensure build_arg is a valid positive integer
        if not build_arg.isdigit():
            print(f"❌ Error: Invalid build number '{build_arg}'")
            print("   Build number must be a positive integer or 'latest'")
            sys.exit(1)
        
        build_num = int(build_arg)
        
        if build_num <= 0:
            print(f"❌ Error: Build number must be a positive integer, got {build_num}")
            sys.exit(1)
        
        # SMART CHECK: Warn if build is old
        print(f"⚠️  Checking if build #{build_num} is recent...")
        
        try:
            build_data = await jenkins_cli.get_build(Config.DASHBOARD_TESTS_JOB_PATH, build_num)
            build_time = datetime.fromtimestamp(build_data['timestamp'] / 1000)
            hours_ago = (datetime.now() - build_time).total_seconds() / 3600
            description = (build_data.get('description') or '').lower()
            target_description = f'dash-e2e-{variant.lower()}'
            
            # Check if build is more than 48 hours old
            if hours_ago > 48:
                print(f"⚠️  WARNING: Build #{build_num} is {hours_ago:.1f} hours old ({build_time.strftime('%Y-%m-%d %H:%M')})")
                print(f"⚠️  This may not be 'last night's build'!")
                print()
                
                # Find the actual latest build
                async with httpx.AsyncClient(verify=Config.SSL_VERIFY, timeout=120.0) as client:
                    url = f"{jenkins_cli.jenkins_url}/job/{Config.DASHBOARD_TESTS_JOB_PATH.replace('/', '/job/')}/api/json?tree=builds[number,timestamp,description]{{0,20}}"
                    if ':' in jenkins_cli.jenkins_token:
                        username, token = jenkins_cli.jenkins_token.split(':', 1)
                        auth = (username, token)
                        response = await client.get(url, auth=auth)
                    else:
                        headers = {"Authorization": f"Bearer {jenkins_cli.jenkins_token}"}
                        response = await client.get(url, headers=headers)
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    # Find the most recent build for this variant
                    for build in data.get('builds', []):
                        desc = (build.get('description') or '').lower()
                        if target_description in desc:
                            latest_num = build['number']
                            latest_time = datetime.fromtimestamp(build['timestamp'] / 1000)
                            latest_hours_ago = (datetime.now() - latest_time).total_seconds() / 3600
                            
                            print(f"💡 The actual latest {variant} build is #{latest_num}")
                            print(f"   Build time: {latest_time.strftime('%Y-%m-%d %H:%M')} ({latest_hours_ago:.1f} hours ago)")
                            print()
                            print(f"💡 TIP: Use 'latest' to auto-find the newest build:")
                            print(f"   python comprehensive_analysis.py latest {variant.lower()}")
                            print()
                            break
                
                if auto_yes:
                    print("Auto-accepting old build (--yes / non-interactive mode)")
                else:
                    response = input("Continue with the old build anyway? [y/N]: ")
                    if response.lower() != 'y':
                        print("Cancelled. Run with 'latest' to auto-find the newest build.")
                        sys.exit(0)
            
            # Check if build matches the requested variant
            if target_description not in description:
                print(f"⚠️  WARNING: Build #{build_num} does not appear to be a {variant} build")
                print(f"   Description: {description}")
                print(f"   Expected to contain: {target_description}")
                print()
                
                if auto_yes:
                    print("Auto-accepting variant mismatch (--yes / non-interactive mode)")
                else:
                    response = input("Continue anyway? [y/N]: ")
                    if response.lower() != 'y':
                        print("Cancelled.")
                        sys.exit(0)
            else:
                print(f"✅ Build #{build_num} is a {variant} build from {hours_ago:.1f} hours ago")
                print()
        except Exception as e:
            print(f"⚠️  Could not verify build age: {e}")
            print("Continuing anyway...")
            print()

    jira = jira_client.JiraClient(
        base_url=Config.JIRA_URL,
        api_token=os.getenv("JIRA_TOKEN", "")
    )

    frontend_repo = os.getenv("FRONTEND_REPO_PATH", "/path/to/odh-dashboard")
    jenkins_repo = os.getenv("JENKINS_REPO_PATH", "/path/to/jenkins")

    # Both ODH and RHOAI builds are in the same dashboard-e2e-tests job
    # They are differentiated by build parameters (CLUSTER_NAME), not separate job paths
    job_path = Config.DASHBOARD_TESTS_JOB_PATH

    if variant == "ODH":
        name = "ODH"
        github_repo = "opendatahub-io/odh-dashboard"
        cluster_config = cluster_inspector.ClusterInspector.ODH_CONFIG
    else:  # RHOAI
        name = "RHOAI"
        github_repo = "opendatahub-io/odh-dashboard"
        cluster_config = cluster_inspector.ClusterInspector.RHOAI_CONFIG

    # --- Jira locking: prevent duplicate analysis ---
    from analyzer.jira_lock import check_or_create_lock

    build_date_str = (
        build_time.strftime('%Y%m%d') if build_time else datetime.now().strftime('%Y%m%d')
    )
    jira_enabled = True
    lock_ticket_key = None
    if skip_jira:
        print("⏭️  Skipping Jira lock (--skip-jira)")
    else:
        try:
            jira_enabled, lock_ticket_key = await check_or_create_lock(build_num, name, build_date_str, auto_yes=auto_yes)
        except Exception as e:
            print(f"⚠️  Jira lock check failed ({e}). Continuing without lock...")
    if lock_ticket_key:
        try:
            from pathlib import Path
            Path("/app/jira-ticket.txt").write_text(lock_ticket_key)
        except Exception:
            pass
    if skip_slack:
        print("⏭️  Skipping Slack (--skip-slack)")
    if skip_rerun:
        print("⏭️  Skipping test reruns (--skip-rerun)")

    if not jira_enabled:
        jira = None

    print("=" * 100)
    print(f"🚀 COMPREHENSIVE {name} ANALYSIS V3 - Build #{build_num}")
    print("=" * 100)
    print()

    # Step 1: Get build data and console
    print(f"[1/11] 📥 Fetching build data and console output...")
    build_data = await jenkins_cli.get_build(job_path, build_num)
    console_output = await jenkins_cli.get_console_output(job_path, build_num)
    print(f"   ✓ Console output: {len(console_output)} characters")

    # Step 1b: Check if this is a nightly cron build
    nightly_info = is_nightly_cron_build(console_output)

    if nightly_info['is_nightly']:
        print(f"   📅 Nightly cron build detected: {nightly_info['cluster_name']}")
    else:
        print(f"   ℹ️  Manual/ad-hoc build (not from nightly cron)")

    # Step 2: Extract ALL deployed images
    print(f"\n[2/11] 🔍 Extracting deployed images...")
    deployed_images = extract_all_deployed_images(console_output)
    for img_type, img_uri in deployed_images.items():
        if img_uri:
            print(f"   • {img_type}: {img_uri[:80]}...")

    # Step 3: Use tracer to get image metadata
    print(f"\n[3/11] 🔬 Running tracer on images...")
    image_metadata = {}
    for img_type, img_uri in deployed_images.items():
        if img_uri:
            print(f"   Tracing {img_type}...")
            metadata = get_image_metadata_with_tracer(img_uri)
            image_metadata[img_type] = metadata

            if metadata.get('error'):
                print(f"      ⚠ {metadata['error']}")
            else:
                if metadata.get('build_date'):
                    print(f"      Build date: {metadata['build_date']}")
                if metadata.get('commit_sha_full'):
                    print(f"      Commit: {metadata['commit_sha_full'][:12]}")

    # Fetch real git commit dates for FBC fragment components
    fbc_meta = image_metadata.get('fbc_fragment', {})
    if fbc_meta and fbc_meta.get('component_commits'):
        fetch_component_commit_dates(fbc_meta['component_commits'])

    # Enrich dashboard metadata from FBC fragment when tracer fails on dashboard image
    dash_meta = image_metadata.get('dashboard', {})
    if dash_meta and dash_meta.get('error') and fbc_meta and not fbc_meta.get('error'):
        fbc_components = fbc_meta.get('component_commits', {})
        dash_component = fbc_components.get('odh-dashboard', {})
        if dash_component:
            dash_meta['commit_sha_full'] = dash_component['sha']
            dash_meta['commit_url'] = dash_component['url']
            if dash_component.get('commit_date'):
                dash_meta['build_date'] = dash_component['commit_date']
        if not dash_meta.get('rhoai_version') and fbc_meta.get('rhoai_version'):
            dash_meta['rhoai_version'] = fbc_meta['rhoai_version']
        if dash_meta.get('commit_sha_full'):
            print(f"   ✅ Dashboard metadata enriched from FBC fragment: commit {dash_meta['commit_sha_full'][:12]}")

    # Fallback: extract dashboard commit from console
    dashboard_commit = extract_dashboard_commit(console_output)

    # Step 3b: Detect dashboard commit sync issues (CRITICAL!)
    print(f"\n[3b/11] 🔄 Checking test/code synchronization...")
    sync_issues = detect_commit_sync_issues(console_output)

    if sync_issues['severity'] == 'critical':
        print(f"   🚨 CRITICAL SYNC ISSUE DETECTED!")
        print(f"      {sync_issues['warning_message']}")
        if sync_issues['deployed_image_registry']:
            print(f"      Image: {sync_issues['deployed_image_registry'][:80]}...")
        if sync_issues['branch_used_for_tests']:
            print(f"      Test branch: {sync_issues['branch_used_for_tests']}")
    elif sync_issues['fell_back_to_main']:
        print(f"   ⚠️  Tests fell back to main branch")
    else:
        print(f"   ✅ No sync issues detected")

    # Step 3c: Analyze image registry types
    print(f"\n[3c/11] 🏷️  Analyzing image registry types...")
    registry_analyses = {}
    for img_type, img_uri in deployed_images.items():
        if img_uri:
            registry_info = analyze_image_registry_type(img_uri)
            registry_analyses[img_type] = registry_info
            if registry_info['registry_type'] != 'unknown':
                print(f"   • {img_type}: {registry_info['registry_type']} ({registry_info['notes']})")

    # Step 4: Analyze pipeline failures (GENERAL)
    print(f"\n[4/11] 🔧 Analyzing pipeline status...")
    build_result = build_data.get('result', 'UNKNOWN')
    pipeline_failure = analyze_pipeline_failure_general(console_output, build_result)

    # Step 4b: Search Jira for pipeline failure issues
    pipeline_jira_issues = []
    if pipeline_failure['is_deployment_failure']:
        category_labels = {
            'infra': 'Infrastructure / Deployment',
            'test_execution': 'Test Execution',
            'post_build': 'Post-Build',
        }
        category = pipeline_failure.get('failure_category')
        category_label = category_labels.get(category, 'Unknown')

        print(f"   ❌ Pipeline failure detected!")
        print(f"      Category: {category_label}")
        print(f"      Failed step: {pipeline_failure['failed_step']}")
        print(f"      Details: {pipeline_failure['error_details']}")

        if pipeline_failure.get('exception_type'):
            print(f"      Exception: {pipeline_failure['exception_type']}")
        if pipeline_failure.get('exception_location'):
            locations = pipeline_failure['exception_location']
            stack_lines = [f"{method}.call({file})" for method, file in locations]
            print(f"      Stack trace: {' → '.join(stack_lines)}")

        if pipeline_failure['all_failed_stages']:
            print(f"      Failed stage(s):")
            for stage_info in pipeline_failure['all_failed_stages']:
                stage_name = stage_info.get('stage', 'Unknown')
                error = stage_info.get('error', stage_info.get('exit_code', ''))
                # Classify stage
                if stage_name in PIPELINE_INFRA_STAGES:
                    stage_type = 'infra'
                elif stage_name in PIPELINE_TEST_STAGES:
                    stage_type = 'test'
                else:
                    stage_type = 'post-build'
                print(f"         • [{stage_type}] {stage_name}: {error}")

        # Smart Jira search for this pipeline failure
        print(f"   🔍 Searching Jira for '{pipeline_failure['failed_step']}' issues...")
        try:
            search_terms = []
            failed_step = pipeline_failure['failed_step']

            # Build smart search query based on failed step
            if 'Dashboard' in failed_step and 'Ready' in failed_step:
                search_terms = ['dashboard', 'ready', 'timeout', 'deployment']
            elif 'Cluster' in failed_step and 'Ready' in failed_step:
                search_terms = ['cluster', 'ready', 'timeout', 'openshift']
            elif 'Operator' in failed_step:
                search_terms = ['operator', 'install', 'deployment']
            else:
                # Generic search based on stage name words
                search_terms = failed_step.lower().split()

            # Search Jira using test_name parameter
            search_query = ' '.join(search_terms[:3])  # Use top 3 terms
            jira_results = await jira.search_issues(
                test_name=search_query,
                max_results=10
            )

            # Filter and rank results
            for issue in jira_results:
                # Skip CVEs
                if 'CVE' in issue.get('key', '') or 'CVE' in issue.get('summary', ''):
                    continue

                # Check relevance
                summary_lower = issue.get('summary', '').lower()
                relevance_score = sum(1 for term in search_terms if term.lower() in summary_lower)

                if relevance_score > 0:
                    pipeline_jira_issues.append({
                        'key': issue.get('key'),
                        'summary': issue.get('summary'),
                        'status': issue.get('status'),
                        'url': f"{Config.JIRA_URL}/browse/{issue.get('key')}",
                        'relevance': relevance_score
                    })

            # Sort by relevance
            pipeline_jira_issues.sort(key=lambda x: x['relevance'], reverse=True)
            pipeline_jira_issues = pipeline_jira_issues[:5]  # Top 5

            if pipeline_jira_issues:
                print(f"      Found {len(pipeline_jira_issues)} related Jira issue(s)")

        except Exception as e:
            print(f"      ⚠ Jira search failed: {e}")

        if pipeline_failure['known_issue']:
            print(f"      Known issue: {pipeline_failure['known_issue']}")
        if len(pipeline_failure['all_failed_stages']) > 1:
            print(f"      Total failed stages: {len(pipeline_failure['all_failed_stages'])}")

        # Step 4c: Check recent GitLab commits if pipeline failed (Dashboard AND Jenkins repos)
        # Calculate hours from build time to now (not just last 24 hours)
        build_timestamp = build_data.get('timestamp', 0) / 1000  # Convert from ms
        build_time = datetime.fromtimestamp(build_timestamp) if build_timestamp else datetime.now()
        hours_since_build = (datetime.now() - build_time).total_seconds() / 3600
        # Look for commits from 48 hours before build to now
        hours_to_check = int(hours_since_build + 48)
        
        print(f"   📋 Checking recent commits (GitHub Dashboard + GitLab Jenkins repos)...")
        print(f"      Build time: {build_time.strftime('%Y-%m-%d %H:%M')}, checking last {hours_to_check}h of commits...")
        recent_merges = check_recent_gitlab_merges(frontend_repo, jenkins_repo, hours_back=hours_to_check)
        
        # Separate Jenkins (can break pipeline) from Dashboard (can only break tests)
        jenkins_commits = [m for m in recent_merges if m['repository'] == 'Jenkins']
        dashboard_commits = [m for m in recent_merges if m['repository'] == 'Dashboard']

        if jenkins_commits:
            merged_j = [c for c in jenkins_commits if c.get('merged')]
            print(f"      Found {len(jenkins_commits)} Jenkins config change(s) ({len(merged_j)} merged, {len(jenkins_commits)-len(merged_j)} unmerged)")
            for commit in jenkins_commits[:3]:
                mr_display = f"!{commit['mr_number']}" if commit['mr_number'] else commit['sha']
                status = "✅" if commit.get('merged') else "⚠️ unmerged"
                print(f"         🚨 {mr_display} ({status}): {commit['subject'][:60]}...")

        if dashboard_commits:
            merged_d = [c for c in dashboard_commits if c.get('merged')]
            print(f"      Found {len(dashboard_commits)} Dashboard change(s) ({len(merged_d)} merged, {len(dashboard_commits)-len(merged_d)} unmerged)")
            for commit in dashboard_commits[:2]:
                mr_display = f"#{commit['mr_number']}" if commit['mr_number'] else commit['sha']
                status = "✅" if commit.get('merged') else "⚠️ unmerged"
                print(f"         📊 {mr_display} ({status}): {commit['subject'][:60]}...")
        else:
            print(f"      No merges in last 24 hours")
    elif pipeline_failure.get('is_post_test_failure'):
        print(f"   ⚠ Build {build_result} — tests ran but some failed (not a pipeline issue)")
        recent_merges = []
    else:
        print(f"   ✅ Pipeline completed successfully")
        recent_merges = []

    # Step 5: Connect to cluster
    print(f"\n[5/11] 🌐 Connecting to {name} cluster...")
    inspector = cluster_inspector.ClusterInspector(cluster_config)

    if not await inspector.login():
        print(f"   ❌ Failed to login")
        cluster_analysis = None
        all_namespaces = []
    else:
        print(f"   ✓ Connected")
        namespace = "opendatahub" if name == "ODH" else "redhat-ods-applications"
        cluster_analysis = await inspector.analyze_test_environment(namespace)
        print(f"   Pods in {namespace}: {cluster_analysis['pod_health']['total']} total")

        # Check ALL namespaces
        all_namespaces = await check_all_namespaces(inspector)

    # Step 5b: Detect version mismatch (expected vs installed operator)
    version_mismatch = {'has_mismatch': False}
    expected_version = extract_expected_version(deployed_images, image_metadata)
    if cluster_analysis and inspector.logged_in:
        try:
            operator_ns = "redhat-ods-operator" if name == "RHOAI" else "openshift-operators"
            installed_csv_version = await inspector.get_operator_csv_version(operator_ns)
            if installed_csv_version:
                version_mismatch = detect_version_mismatch(expected_version, installed_csv_version)
                if version_mismatch['has_mismatch']:
                    print(f"   🚨 VERSION MISMATCH: {version_mismatch['message']}")
                else:
                    print(f"   ✅ Operator version matches: {installed_csv_version}")
            else:
                print(f"   ⚠️  Could not determine installed operator version")
        except Exception as e:
            print(f"   ⚠️  Version check failed: {e}")

    # Step 5c: Inspect cluster image ages
    cluster_image_ages = []
    if cluster_analysis and inspector.logged_in:
        try:
            operator_ns = "redhat-ods-operator" if name == "RHOAI" else "openshift-operators"
            image_namespaces = list(dict.fromkeys([namespace, "redhat-ods-applications", operator_ns]))
            print(f"\n[5c/11] 🐳 Inspecting cluster image ages...")
            cluster_image_ages = await inspect_cluster_image_ages(
                inspector, image_namespaces
            )
            inspected = sum(1 for i in cluster_image_ages if i['build_date'])
            print(f"   ✅ Inspected {inspected}/{len(cluster_image_ages)} images")
            old_images = [i for i in cluster_image_ages if i['age_days'] is not None and i['age_days'] > 7]
            if old_images:
                print(f"   ⚠️  {len(old_images)} image(s) older than 7 days:")
                for img in sorted(old_images, key=lambda x: x['age_days'] or 0, reverse=True):
                    print(f"      • {img['component']}: {img['age_str']} (built {img['build_date']})")
        except Exception as e:
            print(f"   ⚠️  Image age inspection failed: {e}")

    # Step 6: Fetch test results, stages, and process failures
    print(f"\n[6/11] 📊 Processing test results and failures...")
    test_stages_ran = []
    failures = []
    git_analysis = {}

    # --- Discover test stages ---
    stage_pattern = re.compile(r'test-output/([A-Za-z]+Set\d+)/e2e/')
    console_stages = sorted(set(stage_pattern.findall(console_output)))

    artifacts = await jenkins_cli.list_artifacts(job_path, build_num)
    if not artifacts:
        print(f"\n   ❌ No artifacts found for build #{build_num}.")
        print(f"   Cannot perform a proper analysis without build artifacts (JUnit XML, screenshots, videos).")
        print(f"   Artifacts may have been deleted from Jenkins or the build may still be in progress.")
        print(f"\n{'='*100}")
        print(f"❌ ANALYSIS ABORTED — No artifacts available for build #{build_num}")
        print(f"{'='*100}")
        sys.exit(1)

    artifact_stages = set()
    for artifact in artifacts:
        m = stage_pattern.search(artifact.get('relativePath', ''))
        if m:
            artifact_stages.add(m.group(1))

    all_stages = sorted(set(console_stages) | artifact_stages)
    test_stages_ran = all_stages

    if all_stages:
        smoke_stages = [s for s in all_stages if s.startswith('Smoke')]
        sanity_stages = [s for s in all_stages if s.startswith('Sanity')]
        other_stages = [s for s in all_stages if not s.startswith('Smoke') and not s.startswith('Sanity')]
        for stage in smoke_stages + sanity_stages + other_stages:
            print(f"   • {stage}")
        print(f"   Total: {len(all_stages)} test stage(s)")
    else:
        print(f"   ⚠ No test stages found (tests may not have run)")

    # --- Classify artifacts by stage ---
    junit_artifacts_by_stage = {}  # {stage_name: [xml_paths]}
    screenshots_by_stage = {}     # {stage_name: {cy_file: [screenshot_paths]}}
    videos_by_stage = {}          # {stage_name: {cy_file: video_path}}
    merged_artifact = None
    for artifact in artifacts:
        rel_path = artifact.get('relativePath', '')
        if f'test-output/cypress-{build_num}-results.xml' in rel_path:
            merged_artifact = rel_path
            continue
        stage_match = re.match(r'test-output/([A-Za-z]+Set\d+)/', rel_path)
        if not stage_match:
            continue
        stage_name = stage_match.group(1)
        if rel_path.endswith('.xml') and '/junit/' in rel_path:
            junit_artifacts_by_stage.setdefault(stage_name, []).append(rel_path)
        elif '/screenshots/' in rel_path:
            sm = re.search(r'/screenshots/(.+?\.cy\.ts)/', rel_path)
            if sm:
                cy_file = sm.group(1)
                screenshots_by_stage.setdefault(stage_name, {}).setdefault(cy_file, []).append(rel_path)
        elif '/videos/' in rel_path and rel_path.endswith('.mp4'):
            vm = re.search(r'/videos/(.+?\.cy\.ts)\.mp4', rel_path)
            if vm:
                cy_file = vm.group(1)
                videos_by_stage.setdefault(stage_name, {})[cy_file] = rel_path

    # --- Parse test results from JUnit XMLs ---
    parser = artifact_parser.ArtifactParser()
    results_by_stage = {}
    all_tests_by_stage = {}  # {stage_name: [test_info]}
    parsed_results = {'total_tests': 0, 'passed_tests': 0, 'failed_tests': 0, 'skipped_tests': 0, 'failures': []}

    console_totals, console_by_stage = parse_cypress_console_results(console_output)

    if merged_artifact:
        print(f"   Found merged results: {merged_artifact}")
        xml_content = await jenkins_cli.get_artifact_content(job_path, build_num, merged_artifact)
        parsed_results = parser.parse_junit_xml(xml_content)
        results_by_stage = console_by_stage
        # Also parse per-stage XMLs for individual test names
        if junit_artifacts_by_stage:
            for sn, xml_paths in sorted(junit_artifacts_by_stage.items()):
                stage_tests = []
                for xml_path in xml_paths:
                    try:
                        xc = await jenkins_cli.get_artifact_content(job_path, build_num, xml_path)
                        single = parser.parse_junit_xml(xc)
                        stage_tests.extend(single.get('all_tests', []))
                    except Exception:
                        pass
                if stage_tests:
                    all_tests_by_stage[sn] = stage_tests
    elif junit_artifacts_by_stage:
        total_xmls = sum(len(v) for v in junit_artifacts_by_stage.values())
        print(f"   Found {total_xmls} JUnit XML file(s) across {len(junit_artifacts_by_stage)} stage(s)")
        for stage_name, xml_paths in sorted(junit_artifacts_by_stage.items()):
            stage_result = {'total_tests': 0, 'passed_tests': 0, 'failed_tests': 0, 'skipped_tests': 0, 'failures': []}
            stage_tests = []
            for xml_path in xml_paths:
                try:
                    xml_content = await jenkins_cli.get_artifact_content(job_path, build_num, xml_path)
                    single = parser.parse_junit_xml(xml_content)
                    stage_result['total_tests'] += single.get('total_tests', 0)
                    stage_result['passed_tests'] += single.get('passed_tests', 0)
                    stage_result['failed_tests'] += single.get('failed_tests', 0)
                    stage_result['skipped_tests'] += single.get('skipped_tests', 0)
                    stage_result['failures'].extend(single.get('failures', []))
                    stage_tests.extend(single.get('all_tests', []))
                except Exception as e:
                    print(f"   ⚠ Error parsing {os.path.basename(xml_path)}: {e}")
            results_by_stage[stage_name] = stage_result
            all_tests_by_stage[stage_name] = stage_tests
            for key in ['total_tests', 'passed_tests', 'failed_tests', 'skipped_tests']:
                parsed_results[key] += stage_result[key]
            parsed_results['failures'].extend(stage_result['failures'])
        for stage_name, stage_data in console_by_stage.items():
            if stage_name not in results_by_stage:
                results_by_stage[stage_name] = stage_data
    else:
        print(f"   ⚠ No JUnit XML artifacts found, parsing console output...")
        parsed_results = console_totals
        results_by_stage = console_by_stage
        if parsed_results.get('total_tests', 0) > 0:
            print(f"   ✓ Extracted results from console output")
        else:
            print(f"   ⚠ No test results found")

    # --- Detect stage-level timeouts ---
    stage_timeout_minutes = None
    timeout_match = re.search(r'Running NPM script with timeout of (\d+) minutes', console_output)
    if timeout_match:
        stage_timeout_minutes = int(timeout_match.group(1))

    timed_out_stages = set()
    if stage_timeout_minutes:
        for stage_name in test_stages_ran:
            completion_pattern = rf"Cypress Tests By Tag '{stage_name}'.*?Completed"
            if not re.search(completion_pattern, console_output):
                timed_out_stages.add(stage_name)

    if stage_timeout_minutes:
        print(f"   Stage timeout: {stage_timeout_minutes} min")
        if timed_out_stages:
            print(f"   ⏱️  Timed out stages: {', '.join(sorted(timed_out_stages))}")

    # --- Build XML failure lookup by suite/describe name for error enrichment ---
    # The merged XML has failures with suite names but NO spec file or stage info.
    # Screenshots are the source of truth for which files failed in which stage.
    # We match XML failures to screenshot-identified failures by describe name.
    xml_failures_by_suite = {}  # {suite_name_lower: [failure_data]}
    for failure_data in parsed_results.get('failures', []):
        suite = failure_data.get('suite', '').lower()
        if suite:
            xml_failures_by_suite.setdefault(suite, []).append(failure_data)

    # Build screenshot describe -> suite mapping for matching
    screenshot_describes = {}  # {describe_lower: (stage, cy_file)}
    for artifact in artifacts:
        rp = artifact.get('relativePath', '')
        if '/screenshots/' not in rp:
            continue
        stage_m = re.match(r'test-output/([A-Za-z]+Set\d+)/', rp)
        cy_m = re.search(r'/screenshots/(.+?\.cy\.ts)/', rp)
        if stage_m and cy_m:
            screenshot_name = rp.split(cy_m.group(1) + '/')[-1] if cy_m.group(1) in rp else ''
            describe = screenshot_name.split(' -- ')[0].strip() if ' -- ' in screenshot_name else ''
            if describe:
                screenshot_describes[describe.lower()] = (stage_m.group(1), cy_m.group(1))

    # --- Build failures from screenshot artifacts (source of truth) ---
    # screenshots_by_stage = {stage: {cy_file: [screenshot_paths]}}
    # Each cy_file with screenshots = a test that failed at least once
    failure_counter = 0
    commit_hash = dashboard_commit['commit_hash']
    if not commit_hash and image_metadata.get('dashboard', {}).get('commit_sha_full'):
        commit_hash = image_metadata['dashboard']['commit_sha_full'][:8]

    for stage_name in sorted(screenshots_by_stage.keys()):
        for cy_file, screenshot_paths in screenshots_by_stage[stage_name].items():
            full_cy_path = f"cypress/tests/e2e/{cy_file}"
            file_basename = os.path.basename(cy_file).replace('.cy.ts', '')

            # Determine if test passed on retry: if only "(failed).png" but no "(attempt 2)", retry likely passed
            has_retry_failure = any('attempt 2' in p for p in screenshot_paths)
            is_timed_out_stage = stage_name in timed_out_stages

            # Find matching XML failure for error details
            matched_xml = None
            matched_category = 'unknown'
            for describe_lower, (d_stage, d_cy) in screenshot_describes.items():
                if d_stage == stage_name and d_cy == cy_file:
                    # Find XML failure with matching or similar suite name
                    for suite_key, xml_list in xml_failures_by_suite.items():
                        if suite_key in describe_lower or describe_lower in suite_key:
                            if xml_list:
                                matched_xml = xml_list[0]
                                matched_category = parser.categorize_failure(matched_xml)
                            break
                    break

            error_msg = matched_xml.get('error', '') if matched_xml else ''
            stack_trace = matched_xml.get('stack', '') if matched_xml else ''
            suite_name_val = matched_xml.get('suite', '') if matched_xml else ''

            failure = artifact_parser.TestFailure(
                test_name=file_basename,
                test_file=full_cy_path,
                error_message=error_msg or 'No error details (screenshot-only failure)',
                stack_trace=stack_trace,
                suite=suite_name_val,
                duration=matched_xml.get('duration') if matched_xml else None
            )
            failure._category = matched_category
            failure._stage = stage_name
            failure._has_retry_failure = has_retry_failure
            failure._is_retry_pass = not has_retry_failure and not is_timed_out_stage
            failure._is_test_timeout = bool(re.search(r'exceeded \d+s?\b', error_msg, re.IGNORECASE))
            failure._screenshot_data = []
            failure._video_local = None

            failures.append(failure)

            git_info = check_git_diff_for_test(full_cy_path, commit_hash, frontend_repo)
            git_analysis[full_cy_path] = git_info

    # --- Display per-stage breakdown with nested failure details ---
    all_display_stages = sorted(set(list(results_by_stage.keys()) + test_stages_ran))
    for stage_name in all_display_stages:
        sr = results_by_stage.get(stage_name)
        failed_files = screenshots_by_stage.get(stage_name, {})
        is_timed_out = stage_name in timed_out_stages
        stage_failures = [f for f in failures if getattr(f, '_stage', '') == stage_name]
        all_passed_on_retry = stage_failures and all(f._is_retry_pass for f in stage_failures)

        if sr:
            real_fail_count = sum(1 for f in stage_failures if not f._is_retry_pass)
            total = sr['total_tests']
            skipped = sr.get('skipped_tests', 0)
            if stage_failures:
                failed = real_fail_count
                passed = total - failed - skipped
            else:
                passed = sr['passed_tests']
                failed = sr['failed_tests']
                if failed == 0 and failed_files:
                    failed = len(failed_files)
            if is_timed_out:
                icon = '⏱️'
            elif all_passed_on_retry:
                icon = '✅'
            elif failed > 0:
                icon = '❌'
            else:
                icon = '✅'
            line = f"   {icon} {stage_name}: {total} tests, {passed} passed, {failed} failed"
            if skipped:
                line += f", {skipped} skipped"
            if is_timed_out:
                line += f" (TIMEOUT - killed after {stage_timeout_minutes}min)"
            print(line)
        elif failed_files:
            timeout_suffix = f" (TIMEOUT - killed after {stage_timeout_minutes}min)" if is_timed_out else ""
            if is_timed_out:
                icon = '⏱️'
            elif all_passed_on_retry:
                icon = '✅'
            else:
                icon = '❌'
            print(f"   {icon} {stage_name}: {len(failed_files)} failing test(s) (no JUnit data){timeout_suffix}")
        else:
            if is_timed_out:
                print(f"   ⏱️ {stage_name}: TIMEOUT - killed after {stage_timeout_minutes}min (could not be fetched)")
            else:
                print(f"   ✅ {stage_name}")

        for f in stage_failures:
            failure_counter += 1
            file_short = os.path.basename(f.test_file).replace('.cy.ts', '')
            if f._is_retry_pass:
                icon = '⚠️'
                retry_tag = " (passed on retry)"
            else:
                icon = '❌'
                retry_tag = ""
            error_preview = f.error_message.split('\n')[0][:120] if f.error_message and f.error_message != 'No error details (screenshot-only failure)' else ''
            tt_tag = " [test-timeout]" if f._is_test_timeout else ""
            print(f"      {failure_counter}. {icon} {file_short} [{f._category}]{tt_tag}{retry_tag}")
            if error_preview:
                print(f"         {error_preview}")
            gi = git_analysis.get(f.test_file)
            if gi and gi.get('recently_changed'):
                print(f"         ⚠ File recently changed ({gi.get('days_since_change', '?')}d ago)")

    if not failures and not any(screenshots_by_stage.values()):
        print(f"   ✅ No failures to process")

    # Display totals
    if parsed_results.get('total_tests', 0) > 0:
        print(f"   ───")
        print(f"   Total: {parsed_results.get('total_tests', 0)}, "
              f"Passed: {parsed_results.get('passed_tests', 0)}, "
              f"Failed: {parsed_results.get('failed_tests', 0)}, "
              f"Skipped: {parsed_results.get('skipped_tests', 0)}")

    test_result = artifact_parser.TestResult(
        job_name=f"dash-e2e-{name.lower()}",
        build_number=build_num,
        build_url=f"{jenkins_cli.jenkins_url}/job/{job_path.replace('/', '/job/')}/{build_num}/",
        timestamp=build_data.get('timestamp', 0),
        status=build_data.get('result', 'UNKNOWN'),
        total_tests=parsed_results.get('total_tests', 0),
        passed_tests=parsed_results.get('passed_tests', 0),
        failed_tests=parsed_results.get('failed_tests', 0),
        skipped_tests=parsed_results.get('skipped_tests', 0),
        duration=parsed_results.get('duration', 0),
        failures=failures
    )

    # Determine the exact commit used in the nightly build (from downstream repo)
    build_commit = (
        image_metadata.get('fbc_fragment', {}).get('commit_sha_full')
        or image_metadata.get('dashboard', {}).get('commit_sha_full')
    )
    if build_commit:
        print(f"\n   📌 Build commit (downstream): {build_commit[:12]}")

    # Step 9: Analyze with Jira
    print(f"\n[7/11] 🐛 Searching Jira for related bugs...")
    analyzer = failure_analyzer.FailureAnalyzer(
        jira_client=jira,
        enable_test_rerun=False,
        frontend_repo_path=frontend_repo
    )

    analysis = await analyzer.analyze_test_result(test_result, cluster_analysis, cluster_name=name.lower())

    # Step 10: Decide on test reruns with selective/grouped approach
    should_rerun = False
    skip_reason = None
    failures_to_rerun = []

    # Only skip test reruns if it's a TRUE infra/deployment failure (before tests run)
    # Uses failure_category from analyze_pipeline_failure_general()
    is_pre_test_failure = (
        pipeline_failure['is_deployment_failure'] and
        pipeline_failure.get('failure_category') == 'infra'
    )
    
    # Check if this is a post-test failure (like Post Actions)
    is_post_test_failure = pipeline_failure.get('is_post_test_failure', False)
    
    # Exclude "passed on retry" tests — they already passed, no need to rerun
    real_failures = [f for f in failures if not getattr(f, '_is_retry_pass', False)]
    retry_passed = [f for f in failures if getattr(f, '_is_retry_pass', False)]
    if retry_passed:
        print(f"\n   ⏭ Excluding {len(retry_passed)} test(s) that passed on retry from reruns")

    if skip_rerun:
        skip_reason = "Reruns disabled (--skip-rerun)"
    elif is_pre_test_failure:
        skip_reason = f"Pipeline deployment failure: {pipeline_failure['failed_step']}"
    elif len(real_failures) == 0:
        skip_reason = "No real failures to rerun (all passed on retry)"
    elif len(real_failures) < 5:
        # Rerun all real failures when < 5
        should_rerun = True
        failures_to_rerun = real_failures
        print(f"\n[8/11] 🔄 Rerunning all {len(real_failures)} failing test(s)...")
    else:
        # When >= 5 real failures, group by exception and rerun one per group
        should_rerun = True
        exception_groups = group_failures_by_exception(real_failures)

        print(f"\n[8/11] 🔄 {len(real_failures)} failures detected - grouping by exception type...")
        print(f"   Found {len(exception_groups)} exception type(s):")

        for exc_type, group_failures in exception_groups.items():
            print(f"      • {exc_type}: {len(group_failures)} failure(s)")
            # Pick the first failure from each group to rerun
            failures_to_rerun.append(group_failures[0])

        print(f"   Rerunning {len(failures_to_rerun)} representative test(s) (one per exception type)...")

    if should_rerun and len(failures_to_rerun) > 0:
        # Create a modified test result with only the failures we want to rerun
        test_result_for_rerun = artifact_parser.TestResult(
            job_name=test_result.job_name,
            build_number=test_result.build_number,
            build_url=test_result.build_url,
            timestamp=test_result.timestamp,
            status=test_result.status,
            total_tests=test_result.total_tests,
            passed_tests=test_result.passed_tests,
            failed_tests=len(failures_to_rerun),
            skipped_tests=test_result.skipped_tests,
            duration=test_result.duration,
            failures=failures_to_rerun
        )

        # Create a fresh analyzer with reruns enabled, pinned to the build commit
        analyzer_with_reruns = failure_analyzer.FailureAnalyzer(
            jira_client=jira,
            enable_test_rerun=True,
            frontend_repo_path=frontend_repo,
            build_commit=build_commit
        )
        
        # Analyze only the failures we want to rerun
        analysis_with_reruns = await analyzer_with_reruns.analyze_test_result(
            test_result_for_rerun,
            cluster_analysis,
            cluster_name=name.lower()
        )
        
        # For the non-rerun failures, analyze them without rerunning
        if len(failures_to_rerun) < len(failures):
            analyzer_no_rerun = failure_analyzer.FailureAnalyzer(
                jira_client=jira,
                enable_test_rerun=False,
                frontend_repo_path=frontend_repo
            )
            
            non_rerun_failures = [f for f in failures if f not in failures_to_rerun]
            test_result_no_rerun = artifact_parser.TestResult(
                job_name=test_result.job_name,
                build_number=test_result.build_number,
                build_url=test_result.build_url,
                timestamp=test_result.timestamp,
                status=test_result.status,
                total_tests=test_result.total_tests,
                passed_tests=test_result.passed_tests,
                failed_tests=len(non_rerun_failures),
                skipped_tests=test_result.skipped_tests,
                duration=test_result.duration,
                failures=non_rerun_failures
            )
            
            analysis_no_rerun = await analyzer_no_rerun.analyze_test_result(
                test_result_no_rerun,
                cluster_analysis,
                cluster_name=name.lower()
            )
            
            # Merge the analyses
            analysis_with_reruns['failure_analyses'].extend(analysis_no_rerun['failure_analyses'])
    else:
        print(f"\n[8/11] ⏭ Skipping test reruns - {skip_reason}")
        analysis_with_reruns = analysis

    # Step 9: Download failure artifacts (screenshots + videos)
    failure_screenshots = {}
    if download_artifacts and failures:
        print(f"\n[9/11] 📸 Downloading failure artifacts...")
        for failure in failures:
            stage = getattr(failure, '_stage', '')
            cy_file_key = failure.test_file.replace('cypress/tests/e2e/', '') if failure.test_file.startswith('cypress/tests/e2e/') else ''

            # Download screenshots as base64
            ss_paths = screenshots_by_stage.get(stage, {}).get(cy_file_key, [])
            for p in sorted(ss_paths):
                try:
                    img_bytes = await jenkins_cli.get_artifact_bytes(job_path, build_num, p)
                    b64 = base64.b64encode(img_bytes).decode('ascii')
                    failure._screenshot_data.append({
                        'name': os.path.basename(p), 'data_uri': f"data:image/png;base64,{b64}",
                        'is_retry': 'attempt' in p.lower()
                    })
                except Exception:
                    failure._screenshot_data.append({
                        'name': os.path.basename(p), 'data_uri': '',
                        'is_retry': 'attempt' in p.lower()
                    })

            # Download video
            video_rel = videos_by_stage.get(stage, {}).get(cy_file_key)
            if video_rel:
                try:
                    vid_bytes = await jenkins_cli.get_artifact_bytes(job_path, build_num, video_rel)
                    vid_dir = f"reports/current/{name}/videos"
                    os.makedirs(vid_dir, exist_ok=True)
                    vid_filename = f"{stage}_{os.path.basename(cy_file_key)}.mp4"
                    vid_local_path = f"{vid_dir}/{vid_filename}"
                    with open(vid_local_path, 'wb') as vf:
                        vf.write(vid_bytes)
                    failure._video_local = f"videos/{vid_filename}"
                    failure._video_url = f"{jenkins_cli.jenkins_url}/job/{job_path.replace('/', '/job/')}/{build_num}/artifact/{video_rel}"
                except Exception:
                    pass

            ss_count = len([s for s in failure._screenshot_data if s.get('data_uri')])
            vid_label = " + video" if failure._video_local else ""
            if ss_count or failure._video_local:
                print(f"   • {Path(failure.test_file).name}: {ss_count} screenshot(s){vid_label}")

            # Also populate legacy failure_screenshots for markdown report
            screenshots = await get_test_failure_screenshots(jenkins_cli, job_path, build_num, failure.test_name, failure.test_file)
            if screenshots:
                failure_screenshots[failure.test_name] = screenshots
    elif not download_artifacts and failures:
        print(f"\n[9/11] ⏭ Skipping artifact downloads (--no-artifacts-download)")
        for failure in failures:
            screenshots = await get_test_failure_screenshots(jenkins_cli, job_path, build_num, failure.test_name, failure.test_file)
            if screenshots:
                failure_screenshots[failure.test_name] = screenshots
                print(f"   • {Path(failure.test_file).name}: {len(screenshots)} screenshot(s)")
    else:
        print(f"\n[9/11] ✅ No failures to fetch artifacts for")

    # Step 12: Generate comprehensive V2 report
    print(f"\n[10/11] 📄 Generating comprehensive V2 report...")

    lines = []

    # Header with emoji
    lines.append(f"# 🚀 {name} Nightly Analysis - Build #{build_num}")
    lines.append("")
    lines.append(f"**📅 Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**🔗 Build URL:** [{build_num}]({test_result.build_url})")
    
    if nightly_info['is_nightly']:
        lines.append(f"**📅 Nightly Cron:** `{nightly_info['cluster_name']}`")
    
    lines.append("")
    lines.append("---")
    lines.append("")

    # Quick Status Overview
    lines.append("## 📊 Quick Status Overview")
    lines.append("")
    total_tests = parsed_results.get('total_tests', 0)
    num_failures = len(analysis_with_reruns['failure_analyses'])

    # Distinguish "tests not executed" from "tests passed"
    has_pipeline_failure = pipeline_failure['is_deployment_failure']

    if has_pipeline_failure and total_tests == 0:
        lines.append("### ⚠️ **TESTS NOT EXECUTED**")
        lines.append("")
        lines.append(f"Tests did not run due to a deployment failure during the pipeline.")
        lines.append("")
        lines.append(f"See **Pipeline Failure Details** section below for complete error information.")
    elif num_failures == 0 and total_tests > 0:
        lines.append("### ✅ **ALL TESTS PASSED**")
        lines.append("")
        lines.append(f"- **Total Tests:** {total_tests}")
        lines.append(f"- **Passed:** {parsed_results.get('passed_tests', 0)}")
        lines.append(f"- **Failed:** 0")
    elif num_failures > 0:
        lines.append(f"### ❌ **{num_failures} TEST(S) FAILED**")
        lines.append("")
        lines.append(f"- **Total Tests:** {total_tests}")
        lines.append(f"- **Passed:** {parsed_results.get('passed_tests', 0)}")
        lines.append(f"- **Failed:** {num_failures}")
        lines.append("")
        lines.append("**Failed Tests:**")
        for i, fa in enumerate(analysis_with_reruns['failure_analyses'], 1):
            # Show the test file name (e.g., testConnectionCreation.cy.ts) not the JUnit test name
            test_file_name = Path(fa.failure.test_file).name if fa.failure.test_file else fa.failure.test_name
            lines.append(f"{i}. `{test_file_name}`")
    else:
        lines.append("### ⚠️ **NO TEST DATA AVAILABLE**")
        lines.append("")
        lines.append("Could not retrieve test execution results.")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Test/Code Synchronization Status (NEW!)
    if sync_issues['severity'] in ['critical', 'warning'] or sync_issues['fell_back_to_main']:
        lines.append("## 🔄 Test/Code Synchronization Status")
        lines.append("")

        if sync_issues['severity'] == 'critical':
            lines.append("### 🚨 **CRITICAL SYNC ISSUE DETECTED**")
            lines.append("")
            lines.append(f"**Problem:** {sync_issues['warning_message']}")
            lines.append("")

            if sync_issues['deployed_image_registry']:
                lines.append("**Deployed Image:**")
                lines.append("```")
                lines.append(f"{sync_issues['deployed_image_registry']}")
                lines.append("```")
                lines.append("")

            if sync_issues['branch_used_for_tests']:
                lines.append(f"- **Tests Executed From:** `{sync_issues['branch_used_for_tests']}` branch")
                lines.append(f"- **Image Commit:** Could not be determined")
                lines.append("")

            lines.append("**⚠️ Impact:**")
            lines.append("- Test failures may be FALSE POSITIVES due to test/code mismatch")
            lines.append("- Tests run against NEWER code than deployed in cluster")
            lines.append("- Do not treat failures as confirmed product bugs without verification")
            lines.append("")

            lines.append("**🔧 Recommended Actions:**")
            lines.append("1. Investigate why commit metadata is missing from deployed image")
            lines.append("2. Verify image registry type (production vs development)")
            lines.append("3. Consider retesting with correctly synchronized branches")
            lines.append("4. Review individual test failures with extra caution")
            lines.append("")

        elif sync_issues['fell_back_to_main']:
            lines.append("### ⚠️ **Tests Fell Back to Main Branch**")
            lines.append("")
            lines.append(f"- **Tests Executed From:** `main` branch (fallback)")
            lines.append(f"- **Reason:** Dashboard commit could not be determined from deployed image")
            lines.append("")
            if sync_issues['warning_message']:
                lines.append(f"**Note:** {sync_issues['warning_message']}")
                lines.append("")

        # Show registry analysis for deployed images
        if registry_analyses:
            lines.append("### 📋 Image Registry Analysis")
            lines.append("")
            for img_type, reg_info in registry_analyses.items():
                if reg_info and reg_info['registry_type'] != 'unknown':
                    tracer_status = "✅ Compatible" if reg_info['tracer_compatible'] else "❌ Not compatible"
                    lines.append(f"**{img_type.replace('_', ' ').title()}:**")
                    lines.append(f"- Registry Type: `{reg_info['registry_type']}`")
                    lines.append(f"- Tracer Tool: {tracer_status}")
                    if reg_info['notes']:
                        lines.append(f"- Note: {reg_info['notes']}")
                    lines.append("")

        lines.append("---")
        lines.append("")

    # Deployment Information with Tracer Data
    lines.append("## 🐳 Deployment & Image Information")
    lines.append("")

    # Show ALL images (FBC, IIB, Dashboard) - even if tracer failed
    if deployed_images:
        for img_type, img_uri in deployed_images.items():
            if img_uri:
                lines.append(f"### {img_type.replace('_', ' ').title()}")
                lines.append("")

                # Always show the image URI
                lines.append(f"**Image URI:**")
                lines.append(f"```")
                lines.append(f"{img_uri}")
                lines.append(f"```")
                lines.append("")

                # If we have metadata from tracer (or enriched from FBC fallback), include it
                metadata = image_metadata.get(img_type, {})
                has_data = metadata and (metadata.get('build_date') or metadata.get('commit_sha_full'))
                if has_data:
                    if metadata.get('build_date'):
                        age_warning = ""
                        if img_type in ("operator_bundle", "dashboard"):
                            try:
                                bd = datetime.fromisoformat(metadata['build_date'].replace("Z", "+00:00"))
                                delta = datetime.now(tz=timezone.utc) - bd
                                hours = int(delta.total_seconds() // 3600)
                                if hours < 1:
                                    age_warning = " (< 1h ago)"
                                elif hours < 24:
                                    age_warning = f" ({hours}h ago)"
                                else:
                                    days = hours // 24
                                    age_warning = f" 🚨 **{days}d {hours % 24}h old — STALE** 🚨"
                            except (ValueError, TypeError):
                                pass
                        lines.append(f"- 📅 **Build Date:** `{metadata['build_date']}`{age_warning}")

                    if metadata.get('rhoai_version'):
                        lines.append(f"- 🏷️ **RHOAI Version:** `{metadata['rhoai_version']}`")

                    if metadata.get('commit_sha_full'):
                        commit_short = metadata['commit_sha_full'][:8]
                        if metadata.get('commit_url'):
                            lines.append(f"- 🔗 **Commit:** [`{commit_short}`]({metadata['commit_url']})")
                            lines.append(f"  - Full SHA: `{metadata['commit_sha_full']}`")
                        else:
                            lines.append(f"- 🔗 **Commit:** `{metadata['commit_sha_full']}`")

                    if metadata.get('error'):
                        lines.append(f"- ℹ️ _Metadata from FBC fragment (direct tracer failed)_")
                elif metadata and metadata.get('error'):
                    lines.append(f"- ⚠️ **Tracer Error:** {metadata['error']}")

                lines.append("")

    # Component commits from FBC fragment
    fbc_comp = (image_metadata.get('fbc_fragment') or {}).get('component_commits', {})
    if fbc_comp:
        seen_md = {}
        for comp_name, info in fbc_comp.items():
            key = (info.get('repo_name', ''), info.get('sha', ''))
            if key not in seen_md:
                seen_md[key] = {'info': info, 'names': []}
            seen_md[key]['names'].append(comp_name)
        def md_sort_key(item):
            d = item[1]['info'].get('commit_date')
            return d if d else '9999'
        sorted_md = sorted(seen_md.items(), key=md_sort_key)
        lines.append("### 📦 Operator Component Commits")
        lines.append("")
        lines.append(f"<details><summary>{len(sorted_md)} repos (sorted oldest → newest)</summary>")
        lines.append("")
        lines.append("| Component(s) | Repo | Commit | Commit Date | Age |")
        lines.append("|---|---|---|---|---|")
        now_md = datetime.now(tz=timezone.utc)
        for (repo_name, sha), group in sorted_md:
            info = group['info']
            names = sorted(group['names'])
            if len(names) > 3:
                names_str = f"`{names[0]}` +{len(names)-1} more"
            else:
                names_str = ", ".join(f"`{n}`" for n in names)
            commit_link = f"[`{sha[:12]}`]({info['url']})" if info.get('url') else f"`{sha[:12]}`"
            date_str = info.get('commit_date', '')
            age_str = ""
            if date_str:
                try:
                    cd = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    delta = now_md - cd
                    hours = int(delta.total_seconds() // 3600)
                    if hours < 24:
                        age_str = f"{hours}h"
                    else:
                        days = hours // 24
                        age_str = f"**{days}d {hours % 24}h** 🚨" if days > 0 else f"{hours}h"
                except (ValueError, TypeError):
                    pass
                date_display = date_str[:19]
            else:
                date_display = "unknown"
            lines.append(f"| {names_str} | {repo_name} | {commit_link} | `{date_display}` | {age_str} |")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    # Git comparison
    main_commit = None
    commits_behind = 0
    for git_info in git_analysis.values():
        if git_info.get('main_commit'):
            main_commit = git_info['main_commit']
            commits_behind = git_info.get('commits_behind', 0)
            break

    if main_commit:
        main_github_url = f"https://github.com/{github_repo}/commit/{main_commit}"
        lines.append(f"### Main Branch Comparison")
        lines.append("")
        lines.append(f"- **Main Branch HEAD:** [`{main_commit}`]({main_github_url})")

        # CRITICAL FIX: Check if we have a valid image commit before comparing
        # If there's a sync issue, we don't have a valid commit to compare against
        if sync_issues['severity'] == 'critical' or (not dashboard_commit['commit_hash'] and not image_metadata.get('fbc_fragment', {}).get('commit_sha_full')):
            lines.append(f"- ❌ **Cannot compare - image commit unknown due to sync issue**")
            lines.append(f"- ⚠️ **Tests may be running against newer code than deployed**")
        elif commits_behind > 0:
            lines.append(f"- ⚠️ **Image is {commits_behind} commits behind main branch**")
        else:
            lines.append(f"- ✅ Image commit matches current main branch")
        lines.append("")

    if version_mismatch.get('has_mismatch'):
        lines.append("### 🚨 Version Mismatch")
        lines.append("")
        lines.append(f"| | Version |")
        lines.append(f"|---|---|")
        lines.append(f"| **Expected (FBC fragment)** | `{version_mismatch['expected_version']}` |")
        lines.append(f"| **Installed (operator CSV)** | `{version_mismatch['installed_version']}` |")
        lines.append("")
        lines.append(f"> ⚠️ {version_mismatch['message']}")
        lines.append("")

    if cluster_image_ages:
        inspected = [i for i in cluster_image_ages if i.get('build_date')]
        if inspected:
            lines.append("### 🐳 Cluster Image Ages")
            lines.append("")
            lines.append("| Component | Build Date | Age | Commit |")
            lines.append("|---|---|---|---|")
            for img in sorted(inspected, key=lambda x: -(x.get('age_days') or 0)):
                age_flag = " ⚠️" if (img.get('age_days') or 0) > 7 else ""
                lines.append(
                    f"| **{img['component']}** | `{img['build_date']}` "
                    f"| {img['age_str']}{age_flag} | `{img.get('commit', '')}` |"
                )
            lines.append("")

    lines.append("---")
    lines.append("")

    # Trend Analysis (compare with previous build) - ONLY for automated nightly runs
    if enable_trend_analysis and pipeline_failure['is_deployment_failure'] and build_num > 1:
        try:
            # Fetch previous build for same cluster type
            prev_build_num = build_num - 1
            while prev_build_num > 0:
                try:
                    prev_build = await jenkins_cli.get_build(job_path, prev_build_num)
                    prev_description = (prev_build.get('description') or '').lower()
                    
                    # Check if this is the same cluster type
                    current_is_rhoai = variant.upper() == "RHOAI"
                    prev_is_rhoai = 'dash-e2e-rhoai' in prev_description
                    
                    if current_is_rhoai == prev_is_rhoai:
                        # Found matching cluster type
                        prev_result = prev_build.get('result', '')
                        
                        if prev_result == 'FAILURE':
                            # Get previous console to compare errors
                            prev_console = await jenkins_cli.get_console_output(job_path, prev_build_num)
                            prev_failure = analyze_pipeline_failure_general(prev_console, prev_result)
                            
                            if prev_failure['is_deployment_failure']:
                                lines.append("## 📈 Trend Analysis")
                                lines.append("")
                                lines.append(f"**Comparing with previous {variant} build #{prev_build_num}:**")
                                lines.append("")
                                
                                current_error = pipeline_failure['exception_message'] or pipeline_failure['error_details']
                                prev_error = prev_failure['exception_message'] or prev_failure['error_details']
                                
                                if current_error != prev_error:
                                    lines.append(f"⚠️ **Error message has CHANGED:**")
                                    lines.append("")
                                    lines.append(f"**Previous build #{prev_build_num}:**")
                                    lines.append(f"```")
                                    lines.append(prev_error[:200])
                                    lines.append(f"```")
                                    lines.append("")
                                    lines.append(f"**Current build #{build_num}:**")
                                    lines.append(f"```")
                                    lines.append(current_error[:200])
                                    lines.append(f"```")
                                    lines.append("")
                                    
                                    # Highlight key differences
                                    if "''" in prev_error and "''" not in current_error:
                                        lines.append(f"💡 **Key change:** Previous build had empty string `''`, current build has actual cluster name. This suggests partial fix!")
                                        lines.append("")
                                elif prev_failure['failed_step'] == pipeline_failure['failed_step']:
                                    lines.append(f"🔁 **Same failure as build #{prev_build_num}:** `{pipeline_failure['failed_step']}`")
                                    lines.append("")
                                
                                lines.append("---")
                                lines.append("")
                        
                        break  # Found matching cluster, stop searching
                    
                    prev_build_num -= 1
                    if build_num - prev_build_num > 10:  # Don't search too far back
                        break
                        
                except Exception:
                    prev_build_num -= 1
                    if build_num - prev_build_num > 10:
                        break
        except Exception as e:
            # Silently skip if trend analysis fails
            pass

    # Pipeline Status
    if pipeline_failure['is_deployment_failure']:
        lines.append("## 🚨 Pipeline Failure Details")
        lines.append("")
        lines.append(f"**Failed Step:** `{pipeline_failure['failed_step']}`")
        lines.append("")
        lines.append(f"**Error:** {pipeline_failure['error_details']}")
        lines.append("")

        # Add Java/Groovy exception details if available
        if pipeline_failure.get('exception_type'):
            lines.append(f"**Exception Type:** `{pipeline_failure['exception_type']}`")
            lines.append("")

            if pipeline_failure.get('exception_message'):
                lines.append(f"**Exception Message:**")
                lines.append("```")
                lines.append(pipeline_failure['exception_message'])
                lines.append("```")
                lines.append("")

            if pipeline_failure.get('exception_location'):
                lines.append("**Stack Trace (Top 3 calls):**")
                for method, file_info in pipeline_failure['exception_location']:
                    lines.append(f"- `{method}.call({file_info})`")
                lines.append("")

        if pipeline_failure['known_issue']:
            lines.append(f"🐛 **Known Issue:** [{pipeline_failure['known_issue']}]({Config.JIRA_URL}/browse/{pipeline_failure['known_issue']})")
            lines.append("")

        if len(pipeline_failure['all_failed_stages']) > 1:
            lines.append(f"**All Failed Stages ({len(pipeline_failure['all_failed_stages'])}):**")
            for stage_info in pipeline_failure['all_failed_stages']:
                lines.append(f"- `{stage_info['stage']}` (exit code: {stage_info['exit_code']})")
            lines.append("")

        # Add related Jira issues for pipeline failure
        if pipeline_jira_issues:
            lines.append(f"### 🔍 Related Jira Issues for This Pipeline Failure")
            lines.append("")
            lines.append(f"Found **{len(pipeline_jira_issues)}** potentially related issue(s):")
            lines.append("")

            for idx, issue in enumerate(pipeline_jira_issues, 1):
                lines.append(f"{idx}. [{issue['key']}]({issue['url']}) - `{issue['status']}`")
                lines.append(f"   - {issue['summary']}")
                lines.append(f"   - Relevance: {issue['relevance']} matching term(s)")
                lines.append("")
        else:
            lines.append("### 🔍 Related Jira Issues")
            lines.append("")
            lines.append("No related Jira issues found for this pipeline failure.")
            lines.append("")

        # Add recent commits section (GitHub Dashboard + GitLab Jenkins)
        if recent_merges:
            lines.append("### 📋 Recent Commits (GitHub + GitLab)")
            lines.append("")

            # Separate GitLab (pipeline) from Dashboard (test/app) commits
            jenkins_commits = [m for m in recent_merges if m['repository'] == 'Jenkins']
            dashboard_commits = [m for m in recent_merges if m['repository'] == 'Dashboard']

            if jenkins_commits:
                merged_j = [c for c in jenkins_commits if c.get('merged')]
                unmerged_j = [c for c in jenkins_commits if not c.get('merged')]
                lines.append(f"**🚨 GitLab Jenkins Changes ({len(jenkins_commits)}) - Can Break Pipeline:**")
                lines.append("")
                lines.append("These Jenkins configuration changes may have caused the pipeline failure:")
                lines.append("")
                for commit in jenkins_commits:
                    if commit['mr_number']:
                        mr_link = f"[!{commit['mr_number']}](https://gitlab.cee.redhat.com/ods/jenkins/-/merge_requests/{commit['mr_number']})"
                    else:
                        mr_link = f"[{commit['sha']}](https://gitlab.cee.redhat.com/ods/jenkins/-/commit/{commit['full_sha']})"

                    merge_badge = "✅ merged" if commit.get('merged') else "⚠️ unmerged"
                    lines.append(f"- **{mr_link}** ({merge_badge}) by {commit['author']}")
                    lines.append(f"  - {commit['subject']}")
                    lines.append(f"  - Committed: {commit['timestamp']}")
                    if commit['body']:
                        lines.append(f"  - Details: {commit['body'][:100]}...")
                    lines.append("")
                if unmerged_j:
                    lines.append(f"> ⚠️ **{len(unmerged_j)}** commit(s) are NOT merged to master and did NOT affect this build.")
                    lines.append("")

            if dashboard_commits:
                lines.append(f"**📊 GitHub Dashboard Changes ({len(dashboard_commits)}) - Can Break E2E Tests Only:**")
                lines.append("")
                lines.append("These Dashboard changes do NOT cause pipeline failures, only E2E test failures:")
                lines.append("")

                for commit in dashboard_commits[:5]:  # Limit to 5 for brevity
                    if commit['mr_number']:
                        pr_link = f"[#{commit['mr_number']}](https://github.com/opendatahub-io/odh-dashboard/pull/{commit['mr_number']})"
                    else:
                        pr_link = f"[{commit['sha']}](https://github.com/opendatahub-io/odh-dashboard/commit/{commit['full_sha']})"

                    merge_badge = "✅" if commit.get('merged') else "⚠️ unmerged"
                    lines.append(f"- **{pr_link}** ({merge_badge}) by {commit['author']}: {commit['subject'][:80]}")

                if len(dashboard_commits) > 5:
                    lines.append(f"- ... and {len(dashboard_commits) - 5} more Dashboard commit(s)")
                lines.append("")
            if not jenkins_commits and not dashboard_commits:
                lines.append("No recent commits found before this build.")
                lines.append("")
        else:
            lines.append("### 📋 Recent Commits (GitHub + GitLab)")
            lines.append("")
            lines.append("No recent commits found before this build.")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Cluster Health - ALWAYS show this section
    has_pod_issues = False
    if cluster_analysis and cluster_analysis['pod_health'].get('problems'):
        has_pod_issues = True
    cypress_ns = [ns for ns in all_namespaces if ns['is_cypress_related']]
    problem_ns = [ns for ns in all_namespaces if ns['problems']]

    lines.append("## 🏥 Cluster Health")
    lines.append("")

    # Show explanation if cluster analysis is not available
    if not cluster_analysis and not cypress_ns and not problem_ns:
        lines.append("⚠️ **Cluster health data not available**")
        lines.append("")
        lines.append("Cluster analysis requires OpenShift login credentials. Configure the following in your `.env` file:")
        lines.append("")
        lines.append(f"```")
        lines.append(f"{name}_API_SERVER=https://api.your-{name.lower()}-cluster.example.com:6443")
        lines.append(f"{name}_USERNAME=cluster-admin")
        lines.append(f"{name}_PASSWORD=your-password")
        lines.append(f"```")
        lines.append("")
        lines.append("---")
        lines.append("")

    if cluster_analysis:
        pod_health = cluster_analysis['pod_health']
        lines.append(f"### Primary Namespace: `{cluster_analysis['namespace']}`")
        lines.append(f"- **Total Pods:** {pod_health['total']}")
        lines.append(f"- **Running:** {pod_health['running']} ✅")
        lines.append(f"- **Failed:** {pod_health['failed']} ❌")
        lines.append("")

        if pod_health.get('problems'):
            lines.append("**Pod Issues:**")
            for problem in pod_health['problems']:
                lines.append(f"- 🔴 **{problem['pod']}**: {problem['issue']}")
            lines.append("")

    if cypress_ns or problem_ns:
        lines.append(f"### Namespace Issues")
        lines.append("")

        if cypress_ns:
            lines.append(f"**⚠️ Cypress-Related Namespaces (cleanup recommended):**")
            for ns in cypress_ns:
                lines.append(f"- `{ns['namespace']}`: {ns['total_pods']} pods ({ns['running_pods']} running, {ns['failed_pods']} failed)")
            lines.append("")

        if problem_ns:
            lines.append(f"**🔴 Namespaces with Pod Issues:**")
            for ns in problem_ns:
                lines.append(f"- `{ns['namespace']}`: {len(ns['problems'])} issue(s)")
                for problem in ns['problems'][:3]:
                    lines.append(f"  - {problem['pod']}: {problem['issue']}")
            lines.append("")

        lines.append("---")
        lines.append("")

    # Test Rerun Results - dedicated section
    reruns_attempted = [
        fa for fa in analysis_with_reruns['failure_analyses']
        if hasattr(fa, 'rerun_result') and fa.rerun_result and fa.rerun_result.get('attempted')
    ]
    reruns_not_attempted = [
        fa for fa in analysis_with_reruns['failure_analyses']
        if not (hasattr(fa, 'rerun_result') and fa.rerun_result and fa.rerun_result.get('attempted'))
    ]

    lines.append("## 🔄 Test Rerun Results")
    lines.append("")

    if not should_rerun:
        lines.append(f"⏭ **Reruns skipped:** {skip_reason}")
        lines.append("")
    elif not reruns_attempted:
        lines.append("⚠️ **No reruns were executed**")
        lines.append("")
    else:
        # Build commit info
        if build_commit:
            lines.append(f"**📌 Rerun commit:** `{build_commit[:12]}` (downstream)")
        else:
            lines.append("**📌 Rerun commit:** `main` branch (no build commit available)")
        lines.append("")

        # Strategy
        if len(real_failures) >= 5:
            exception_groups = group_failures_by_exception(real_failures)
            lines.append(f"**Strategy:** {len(real_failures)} real failures grouped into {len(exception_groups)} exception type(s) — one representative test rerun per group")
        else:
            lines.append(f"**Strategy:** All {len(real_failures)} real failure(s) rerun individually")
        if retry_passed:
            lines.append(f"- {len(retry_passed)} test(s) excluded (passed on retry)")
        lines.append("")

        # Summary table
        rerun_passed = [fa for fa in reruns_attempted if fa.rerun_result.get('success')]
        rerun_failed = [fa for fa in reruns_attempted if not fa.rerun_result.get('success')]
        lines.append(f"| Metric | Count |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Tests rerun | {len(reruns_attempted)} |")
        lines.append(f"| Passed on rerun | {len(rerun_passed)} |")
        lines.append(f"| Failed on rerun | {len(rerun_failed)} |")
        lines.append("")

        # Per-test rerun details
        lines.append("### Rerun Details")
        lines.append("")

        for fa in reruns_attempted:
            rerun = fa.rerun_result
            test_file_name = Path(fa.failure.test_file).name if fa.failure.test_file else fa.failure.test_name
            git_info = git_analysis.get(fa.failure.test_file, {})
            classification = classify_failure_result(rerun, git_info)

            if rerun.get('success'):
                status_icon = "✅"
                status_text = f"PASSED ({rerun.get('duration', 0):.1f}s)"
            elif rerun.get('exit_code') == -1:
                status_icon = "⏱"
                status_text = f"TIMED OUT ({rerun.get('duration', 0):.0f}s)"
            else:
                status_icon = "❌"
                status_text = f"FAILED (exit code {rerun.get('exit_code', 'N/A')}, {rerun.get('duration', 0):.1f}s)"

            lines.append(f"**{status_icon} {test_file_name}** — {status_text}")
            lines.append(f"- {classification['emoji']} **Classification:** {classification['classification'].upper().replace('_', ' ')}")
            lines.append(f"- {classification['explanation']}")
            lines.append(f"- **Confidence:** {classification['confidence']}")

            if classification['action_required']:
                lines.append(f"- **Action:** {classification['suggested_action']}")

            # Error comparison for failed reruns
            if not rerun.get('success'):
                rerun_error = rerun.get('error_output', '')
                if rerun_error:
                    comparison = compare_errors(fa.failure.error_message, rerun_error)
                    if comparison['same_error']:
                        lines.append(f"- **Error match:** Same as original (consistent)")
                    else:
                        lines.append(f"- **Error match:** Different from original")
                        lines.append(f"  ```")
                        lines.append(f"  {rerun_error[:300]}")
                        lines.append(f"  ```")

            lines.append("")

        # Non-rerun real failures (when grouped strategy skipped some)
        grouped_not_rerun = [
            fa for fa in reruns_not_attempted
            if not getattr(fa.failure, '_is_retry_pass', False)
        ]
        if grouped_not_rerun:
            lines.append("### Not Rerun")
            lines.append("")
            lines.append("These tests were not rerun (grouped by exception type — a representative was rerun instead):")
            lines.append("")
            for fa in grouped_not_rerun:
                test_file_name = Path(fa.failure.test_file).name if fa.failure.test_file else fa.failure.test_name
                exc_type = extract_exception_type(fa.failure.error_message)
                lines.append(f"- `{test_file_name}` ({exc_type})")
            lines.append("")

    lines.append("---")
    lines.append("")

    # Detailed Test Failures
    if num_failures > 0:
        lines.append("## 🔍 Detailed Test Failure Analysis")
        lines.append("")

        for i, fa in enumerate(analysis_with_reruns['failure_analyses'], 1):
            # Use the test file name as the heading (e.g., testConnectionCreation.cy.ts)
            test_file_name = Path(fa.failure.test_file).name if fa.failure.test_file else fa.failure.test_name
            is_rp = getattr(fa.failure, '_is_retry_pass', False)
            status_tag = " ⚠️ *(passed on retry)*" if is_rp else ""
            rerun = getattr(fa, 'rerun_result', None)
            if rerun and isinstance(rerun, dict) and rerun.get('attempted') and rerun.get('success'):
                status_tag += " 🟢 **Pass on re-run**"
            lines.append(f"### {i}. {test_file_name}{status_tag}")
            lines.append("")
            lines.append(f"**📁 File:** `{fa.failure.test_file}`")
            lines.append("")

            # Git analysis
            git_info = git_analysis.get(fa.failure.test_file, {})

            if git_info:
                lines.append("**📝 Code Analysis:**")
                if git_info.get('quarantined'):
                    quarantine_method = git_info.get('quarantine_method', '@Bug tag')
                    
                    # IMPROVED: Show if quarantined after build vs at build time
                    if git_info.get('quarantined_in_main_only'):
                        lines.append(f"- 🔒 **Test QUARANTINED in main (AFTER this build)** ({quarantine_method})")
                        lines.append(f"  - ⚠️ Test was NOT quarantined when this build ran - failure was real")
                    elif git_info.get('was_quarantined_at_build'):
                        lines.append(f"- 🔒 **Test QUARANTINED** ({quarantine_method}) - was already quarantined at build time")
                    else:
                        lines.append(f"- 🔒 **Test QUARANTINED in main** ({quarantine_method})")
                    
                    if git_info.get('bug_references'):
                        for bug in git_info['bug_references']:
                            lines.append(f"  - 🐛 Linked Bug: [{bug}]({Config.JIRA_URL}/browse/{bug})")

                if git_info.get('needs_maintenance'):
                    lines.append(f"- 🔧 **Test NEEDS MAINTENANCE** (`@Maintain` tag)")

                if git_info.get('file_changed'):
                    lines.append(f"- 📝 **Test file changed** between image commit and main")
                elif commits_behind > 0:
                    lines.append(f"- ✅ Test file unchanged (image {commits_behind} commits behind)")

                lines.append("")

            # Original error
            lines.append("**❌ Original Error:**")
            lines.append("```")
            lines.append(fa.failure.error_message[:600])
            lines.append("```")
            lines.append("")

            # Screenshots (NEW!)
            if fa.failure.test_name in failure_screenshots:
                screenshots = failure_screenshots[fa.failure.test_name]
                lines.append("**📸 Failure Screenshots:**")
                lines.append("")

                # If we extracted a test file from screenshot path, update the file path
                if screenshots and screenshots[0].get('test_file'):
                    test_file_from_screenshot = screenshots[0]['test_file']
                    # Update the test file in the report header
                    for i, line in enumerate(lines):
                        if f"**📁 File:** `{fa.failure.test_file}`" in line:
                            lines[i] = f"**📁 File:** `{test_file_from_screenshot}`"
                            break

                from urllib.parse import quote
                for screenshot in screenshots[:3]:  # Limit to 3 screenshots per test
                    retry_tag = " (Retry)" if screenshot['is_retry'] else ""
                    # URL-encode the screenshot URL (spaces break markdown links)
                    encoded_url = screenshot['url'].replace(' ', '%20')
                    lines.append(f"- [{screenshot['name']}{retry_tag}]({encoded_url})")
                lines.append("")

            # Test rerun results with IMPROVED classification
            if hasattr(fa, 'rerun_result') and fa.rerun_result and fa.rerun_result.get('attempted'):
                rerun = fa.rerun_result
                
                # Use the new classification logic
                classification = classify_failure_result(rerun, git_info)
                
                ran_on = "main branch" if rerun.get('ran_on_main', True) else f"build commit {rerun.get('ran_at_commit', 'unknown')[:8]}"
                lines.append(f"**🔄 Test Rerun Analysis ({ran_on}):**")
                
                if rerun.get('success'):
                    lines.append(f"- ✅ **PASSED** on rerun (took {rerun.get('duration', 0):.1f}s)")
                else:
                    lines.append(f"- ❌ **FAILED** on rerun (exit code: {rerun.get('exit_code', 'N/A')})")
                    
                    # Show error comparison for failed reruns
                    rerun_error = rerun.get('error_output', '')
                    if rerun_error:
                        comparison = compare_errors(fa.failure.error_message, rerun_error)
                        if comparison['same_error']:
                            lines.append(f"- **Error comparison:** ✅ Same error (consistent failure)")
                        else:
                            lines.append(f"- **Error comparison:** ⚠️ Different error")
                            lines.append(f"- **Rerun error (first 300 chars):**")
                            lines.append("  ```")
                            lines.append(f"  {rerun_error[:300]}")
                            lines.append("  ```")
                
                # Show the classification result
                lines.append(f"")
                lines.append(f"**{classification['emoji']} Classification: {classification['classification'].upper().replace('_', ' ')}**")
                lines.append(f"- {classification['explanation']}")
                lines.append(f"- **Confidence:** {classification['confidence']}")
                
                if classification['action_required']:
                    lines.append(f"- **⚠️ Action Required:** {classification['suggested_action']}")
                else:
                    lines.append(f"- **✅ No action required:** {classification['suggested_action']}")
                
                # Special handling for quarantined-after-build
                if classification['classification'] == 'quarantined_after_build':
                    lines.append(f"")
                    lines.append(f"> **Note:** This test passed on rerun because it was quarantined in main AFTER this build was created.")
                    lines.append(f"> The original failure in this build was real - the test was not yet quarantined at build time.")
                
                lines.append("")

            # Jira
            if hasattr(fa, 'jira_issues') and fa.jira_issues:
                lines.append("**🐛 Potential Related Jira Issues:**")
                lines.append("")
                shown = 0
                for issue in fa.jira_issues:
                    if 'CVE' in issue['key'] or 'CVE' in issue['summary']:
                        continue
                    if shown >= 3:
                        break
                    lines.append(f"- [{issue['key']}]({issue['url']}) - `{issue['status']}`")
                    lines.append(f"  {issue['summary']}")
                    shown += 1
                lines.append("")
            else:
                lines.append("**🐛 Jira:** No related issues found")
                lines.append("")

            lines.append("---")
            lines.append("")

    report_content = "\n".join(lines)

    # Step 13: Save report
    print(f"\n[11/11] 💾 Saving report...")
    os.makedirs(f"reports/current/{name}", exist_ok=True)
    os.makedirs("reports/historical", exist_ok=True)

    # Current report (Markdown)
    current_report_path = f"reports/current/{name}/latest-build-{build_num}.md"
    with open(current_report_path, 'w') as f:
        f.write(report_content)

    # Historical copy (Markdown)
    date_str = datetime.now().strftime('%Y-%m-%d')
    historical_report_path = f"reports/historical/{date_str}-{name}-build-{build_num}-v2.md"
    with open(historical_report_path, 'w') as f:
        f.write(report_content)

    # HTML report
    html_content = generate_html_report(
        name=name, build_num=build_num,
        build_url=f"{jenkins_cli.jenkins_url}/job/{job_path.replace('/', '/job/')}/{build_num}/",
        nightly_info=nightly_info, parsed_results=parsed_results, failures=failures,
        pipeline_failure=pipeline_failure, image_metadata=image_metadata,
        screenshots_by_stage=screenshots_by_stage, timed_out_stages=timed_out_stages,
        stage_timeout_minutes=stage_timeout_minutes, results_by_stage=results_by_stage,
        test_stages_ran=test_stages_ran, analysis_with_reruns=analysis_with_reruns,
        cluster_analysis=cluster_analysis, recent_merges=recent_merges, git_analysis=git_analysis,
        all_tests_by_stage=all_tests_by_stage, version_mismatch=version_mismatch,
        cluster_image_ages=cluster_image_ages,
    )
    html_report_path = f"reports/current/{name}/latest-build-{build_num}.html"
    with open(html_report_path, 'w') as f:
        f.write(html_content)

    # Final summary
    print()
    print("=" * 100)
    print(f"✅ COMPREHENSIVE ANALYSIS V2 COMPLETE")
    print("=" * 100)
    print()
    print(f"📄 Markdown: {current_report_path}")
    print(f"🌐 HTML: {html_report_path}")
    print(f"📄 Historical: {historical_report_path}")
    print()
    print(f"🐳 Images analyzed: {len([m for m in image_metadata.values() if m and not m.get('error')])}")
    print(f"🧪 Tests executed: {total_tests}")
    print(f"❌ Tests failed: {num_failures}")
    if pipeline_failure['is_deployment_failure']:
        print(f"🚨 Pipeline failure: {pipeline_failure['failed_step']}")
    if version_mismatch.get('has_mismatch'):
        print(f"🚨 Version mismatch: {version_mismatch['message']}")
    print()

    # Publish results to Jira lock ticket
    if lock_ticket_key and jira_enabled:
        from analyzer.jira_lock import publish_results

        retry_passed = sum(1 for f in failures if getattr(f, '_is_retry_pass', False))
        failure_names = [fa.failure.test_name
                         for fa in analysis_with_reruns.get('failure_analyses', [])
                         if hasattr(fa, 'failure') and hasattr(fa.failure, 'test_name')]

        print("📤 Publishing results to Jira...")
        try:
            await publish_results(
                issue_key=lock_ticket_key,
                build_num=build_num,
                platform=name,
                total_tests=total_tests,
                passed_tests=parsed_results.get('passed_tests', 0),
                failed_tests=num_failures,
                num_retries_passed=retry_passed,
                pipeline_failure=pipeline_failure,
                failure_names=failure_names,
                md_report_path=current_report_path,
                html_report_path=html_report_path,
                version_mismatch=version_mismatch,
            )
        except Exception as e:
            import traceback
            print(f"   ⚠️  Jira publish failed: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
