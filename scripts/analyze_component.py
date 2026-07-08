#!/usr/bin/env python3
"""
Component Build Analyzer

Analyzes a specific component's test results from a Jenkins build.
Uses the component registry to load appropriate parsers and analyzers.

Usage:
    python3 scripts/analyze_component.py model-registry pytest --build 28
    python3 scripts/analyze_component.py dashboard cypress --build 3695 --job "cypress/dashboard-tests"
"""
import sys
import argparse
import os
import asyncio
from pathlib import Path
from datetime import datetime

# Add parent directory to path
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

from analyzer.registry import ComponentRegistry

# Default XML artifact patterns for test results
# These patterns cover common naming conventions across different test frameworks
DEFAULT_XML_PATTERNS = [
    '*xunit*.xml',          # pytest: cluster-health_xunit.xml, xunit_report.xml, xunit_test_result.xml
    '*junit*.xml',          # Maven/Gradle style: junit-results.xml
    'test-results*.xml',    # Common alternative: test-results.xml, test-results-pytest.xml
    'test-report*.xml',     # Common alternative: test-report.xml
]

# Try to import existing modules (may not be in venv)
try:
    from analyzer import jenkins_client, cluster_inspector
    from analyzer.config import Config
    HAS_JENKINS_CLIENT = True
except ImportError:
    HAS_JENKINS_CLIENT = False
    print("⚠️  Warning: Jenkins client not available (run from venv)")
    print("   This is a demo of the integration flow")

    # Mock config for demo
    class Config:
        JENKINS_URL = "https://jenkins-example.com"


async def analyze_component_build(component_name: str, framework: str,
                                  build_number: int, jenkins_job: str = None,
                                  variant: str = 'rhoai'):
    """
    Analyze a component's test results from Jenkins build

    Args:
        component_name: Component name (e.g., 'model-registry')
        framework: Test framework (e.g., 'pytest')
        build_number: Jenkins build number
        jenkins_job: Optional Jenkins job path (uses component.json if not provided)
        variant: Cluster variant (rhoai or odh)
    """

    print("=" * 80)
    print(f"Analyzing {component_name}/{framework} - Build #{build_number}")
    print("=" * 80)

    # Step 1: Load component from registry
    print("\n📋 Loading component configuration...")
    registry = ComponentRegistry()
    config = registry.get_component(component_name, framework)

    if not config:
        print(f"❌ Error: Component {component_name}/{framework} not found in registry")
        print(f"\nAvailable components:")
        for comp in registry.list_components():
            print(f"  - {comp['key']}")
        return None

    print(f"✅ Loaded: {config['name']}/{config['framework']}")
    print(f"   Description: {config.get('description', 'N/A')}")
    print(f"   Maintainers: {', '.join(config.get('maintainers', []))}")

    # Step 2: Get Jenkins job path
    if not jenkins_job:
        job_paths = config.get('jenkins', {}).get('job_paths', [])
        if not job_paths:
            print(f"❌ Error: No Jenkins job paths configured for this component")
            return None
        jenkins_job = job_paths[0]
        print(f"\n🔍 Using Jenkins job: {jenkins_job}")

    # Step 3: Fetch build from Jenkins
    print(f"\n📥 Fetching build from Jenkins...")
    print(f"   Job: {jenkins_job}")
    print(f"   Build: #{build_number}")

    try:
        # Use existing jenkins_client
        jc = jenkins_client.JenkinsClient(
            jenkins_url=Config.JENKINS_URL,
            jenkins_token=Config.JENKINS_TOKEN,
            jenkins_username=Config.JENKINS_USERNAME,
            jenkins_password=Config.JENKINS_TOKEN  # Token is used as password
        )

        # Get build info (store relative path, not full URL)
        build_path = f"{jenkins_job}/{build_number}"
        print(f"   Job path: {build_path}")

        # Fetch console output (async)
        console_log = await jc.get_console_output(jenkins_job, build_number)
        if not console_log:
            print(f"❌ Error: Could not fetch console output")
            return None

        print(f"✅ Fetched console output ({len(console_log)} bytes)")

        # Get build result (async)
        build_info = await jc.get_build(jenkins_job, build_number)
        build_result = build_info.get('result', 'UNKNOWN')
        print(f"   Build result: {build_result}")

    except Exception as e:
        print(f"❌ Error fetching build: {e}")
        return None

    # Step 4: Load parser
    print(f"\n🔧 Loading parser...")
    parser = registry.load_parser(component_name, framework)

    if not parser:
        print(f"⚠️  No component-specific parser found")
        print(f"   Trying default parser for {framework}...")

        # Fallback to default parser
        from analyzer.parsers import get_default_parser
        parser_class = get_default_parser(framework)

        if parser_class:
            parser = parser_class(config)
            print(f"✅ Using default {framework} parser")
        else:
            print(f"❌ Error: No parser available for framework '{framework}'")
            print(f"\nAvailable frameworks with default parsers:")
            print(f"  - pytest")
            print(f"\nComponent needs to provide custom parser at:")
            print(f"  .claude/components/{component_name}/{framework}/parser.py")
            return None
    else:
        print(f"✅ Using component-specific parser")

    # Step 5: Parse test results using ingestion strategy
    print(f"\n📊 Collecting test evidence...")
    try:
        from analyzer.ingestion_strategy import IngestionStrategy

        # Fetch and parse artifacts (JUnit XML, HTML reports, etc.)
        print(f"\n📦 Fetching artifacts...")
        raw_artifacts = await jc.list_artifacts(jenkins_job, build_number)

        # Get artifact patterns from config
        artifact_patterns = config.get('jenkins', {}).get('artifact_patterns', [])

        # Filter and download artifacts
        from fnmatch import fnmatch
        artifacts_with_content = []

        if raw_artifacts:
            print(f"   Found {len(raw_artifacts)} artifacts")

            # Filter artifacts based on patterns
            filtered_artifacts = []
            for artifact in raw_artifacts:
                artifact_path = artifact.get('relativePath', '')
                # If patterns specified, only include matching artifacts
                if artifact_patterns:
                    if any(fnmatch(artifact_path, pattern) for pattern in artifact_patterns):
                        filtered_artifacts.append(artifact)
                # Otherwise use default patterns (covers xunit, junit, test-results, test-report)
                else:
                    if any(fnmatch(artifact_path, pattern) for pattern in DEFAULT_XML_PATTERNS):
                        filtered_artifacts.append(artifact)

            if filtered_artifacts:
                print(f"   Filtered to {len(filtered_artifacts)} artifacts matching patterns")

            # Download artifact content
            for artifact in filtered_artifacts:
                artifact_path = artifact.get('relativePath', '')
                if artifact_path.endswith('.xml'):
                    print(f"   Downloading: {artifact_path}")
                    try:
                        content = await jc.get_artifact_content(jenkins_job, build_number, artifact_path)
                        artifacts_with_content.append({
                            'path': artifact_path,
                            'content': content,
                            'type': 'xml'
                        })
                    except Exception as e:
                        print(f"   ⚠️  Could not download {artifact_path}: {e}")

        # Step 5b: Check for must-gather archives
        print(f"\n🔍 Checking for must-gather archives...")
        must_gather_data = None
        must_gather_archives = [
            a for a in raw_artifacts
            if 'must-gather' in a.get('relativePath', '').lower() and
               a.get('relativePath', '').endswith(('.tar.gz', '.tgz')) and
               not a.get('relativePath', '').endswith(('.sh', '.robot', '.py'))
        ] if raw_artifacts else []

        if must_gather_archives:
            print(f"   Found {len(must_gather_archives)} must-gather archive(s)")
            # Download first must-gather archive
            mg_artifact = must_gather_archives[0]
            mg_path = mg_artifact.get('relativePath', '')
            print(f"   Downloading: {mg_path}")

            try:
                from analyzer.must_gather_parser import MustGatherParser
                import tempfile

                # Download to temp file
                mg_content = await jc.get_artifact_bytes(jenkins_job, build_number, mg_path)
                with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as tmp:
                    tmp.write(mg_content)
                    tmp_path = tmp.name

                # Parse must-gather
                namespaces = config.get('cluster', {}).get('namespaces', [])
                parser_mg = MustGatherParser(tmp_path)
                must_gather_data = parser_mg.parse(target_namespaces=namespaces if namespaces else None)

                print(f"   ✅ Extracted must-gather data:")
                print(f"      Pod logs: {sum(len(pods) for pods in must_gather_data.pod_logs.values())} logs")
                print(f"      Events: {sum(len(events) for events in must_gather_data.events.values())} events")

                # Cleanup temp file
                import os
                os.unlink(tmp_path)

            except Exception as e:
                print(f"   ⚠️  Failed to parse must-gather: {e}")
                must_gather_data = None
        else:
            print(f"   ⓘ No must-gather archives found")

        # Step 5c: Check for cluster logs
        print(f"\n📋 Checking for cluster logs...")
        cluster_logs = []
        cluster_log_patterns = config.get('jenkins', {}).get('cluster_log_patterns', [])

        if cluster_log_patterns and raw_artifacts:
            for pattern in cluster_log_patterns:
                matching = [a for a in raw_artifacts if a.get('relativePath', '') == pattern]
                if matching:
                    log_path = matching[0].get('relativePath', '')
                    print(f"   Downloading: {log_path}")
                    try:
                        log_content = await jc.get_artifact_content(jenkins_job, build_number, log_path)
                        cluster_logs.append({
                            'path': log_path,
                            'content': log_content,
                            'size': len(log_content)
                        })
                        print(f"   ✅ Downloaded {len(log_content)} bytes")
                    except Exception as e:
                        print(f"   ⚠️  Failed to download {log_path}: {e}")

        if not cluster_logs and not cluster_log_patterns:
            print(f"   ⓘ No cluster log patterns configured")
        elif not cluster_logs:
            print(f"   ⓘ No cluster logs found matching patterns")

        # Use ingestion strategy to collect evidence from all sources
        strategy = IngestionStrategy(parser, jenkins_client=jc, component_config=config)
        ingestion_result = await strategy.collect_evidence(
            console_log=console_log,
            artifacts=artifacts_with_content,
            jenkins_job=jenkins_job,
            build_number=build_number
        )

        # Convert to dict for compatibility with existing code
        results = ingestion_result.to_dict()

        # Display evidence quality
        print(f"\n📋 Evidence Status: {results['evidence_status']}")
        if results['evidence_sources']:
            print(f"   Sources: {', '.join(results['evidence_sources'])}")
        if results.get('warnings'):
            for warning in results['warnings']:
                print(f"   ⚠️  {warning}")

        print(f"\nTest Results Summary:")
        print(f"  Total:   {results.get('total', 0)}")
        print(f"  Passed:  {results.get('passed', 0)}")
        print(f"  Failed:  {results.get('failed', 0)}")
        print(f"  Skipped: {results.get('skipped', 0)}")

        if results.get('duration'):
            print(f"  Duration: {results.get('duration')}s")

        # Evidence status is shown earlier, no need to show artifact breakdown
        # (ingestion strategy handles aggregation internally)

    except Exception as e:
        print(f"❌ Error parsing results: {e}")
        import traceback
        traceback.print_exc()
        return None

    # Step 6: Extract failures and enrich with metadata
    failures = parser.extract_failures(results)

    # Enrich failures with rerun commands
    for failure in failures:
        if 'rerun_command' not in failure or not failure['rerun_command']:
            failure['rerun_command'] = parser.get_rerun_command(failure)

    if failures:
        print(f"\n🔴 Failures detected: {len(failures)}")
        for i, failure in enumerate(failures[:5], 1):  # Show first 5
            print(f"\n{i}. {failure.get('test_name', 'Unknown test')}")
            print(f"   File: {failure.get('test_file', 'Unknown')}")
            error = failure.get('error_message', 'No error message')
            # Truncate long errors
            if len(error) > 100:
                error = error[:100] + '...'
            print(f"   Error: {error}")

            # Rerun command
            rerun = parser.get_rerun_command(failure)
            print(f"   Rerun: {rerun}")

        if len(failures) > 5:
            print(f"\n   ... and {len(failures) - 5} more failures")
    else:
        print(f"\n✅ No failures detected!")

    # Step 7: Perform intelligent failure analysis
    print(f"\n🔍 Analyzing failures...")
    analyzer_module = registry.load_analyzer(component_name, framework)

    # Use default analyzer if no custom one provided
    if not analyzer_module:
        print(f"⚠️  No component-specific analyzer - using intelligent default analyzer")
        from analyzer.default_failure_analyzer import DefaultFailureAnalyzer
        default_analyzer = DefaultFailureAnalyzer(config)

        # Perform cluster-based analysis
        build_info = {
            'build_number': build_number,
            'job': jenkins_job,
            'variant': variant,
            'result': build_result,
            'cluster': config.get('cluster', {})
        }

        try:
            analysis_results = await default_analyzer.analyze_failures(
                failures, build_info, must_gather_data=must_gather_data
            )
            analyzed_failures = analysis_results
            print(f"✅ Analyzed {analysis_results.get('total_failures', 0)} failures")
            print(f"   Found {len(analysis_results.get('failure_clusters', []))} failure clusters")
            print(f"   Generated {len(analysis_results.get('recommendations', []))} recommendations")
        except Exception as e:
            print(f"⚠️  Analysis failed: {e}")
            analyzed_failures = {'total_failures': len(failures), 'failure_clusters': [], 'recommendations': []}
    else:
        print(f"✅ Using component-specific analyzer")
        analyzed_failures = []
        try:
            # Analyze each failure
            for failure in failures:
                analyzed = analyzer_module.analyze_failure(failure)
                analyzed_failures.append(analyzed)

            # Show categories
            categories = {}
            for f in analyzed_failures:
                cat = f.get('category', 'unknown')
                categories[cat] = categories.get(cat, 0) + 1

            if categories:
                print(f"\nFailure Categories:")
                for cat, count in sorted(categories.items(), key=lambda x: x[1], reverse=True):
                    print(f"  {cat}: {count}")

        except Exception as e:
            print(f"⚠️  Error during analysis: {e}")
            analyzed_failures = failures  # Use unanalyzed

    # Step 8: Cluster inspection (optional)
    cluster_state = None
    namespaces = config.get('cluster', {}).get('namespaces', [])

    if namespaces and results.get('failed', 0) > 0:
        print(f"\n☸️  Inspecting cluster health...")
        print(f"   Namespaces: {', '.join(namespaces)}")

        try:
            inspector = cluster_inspector.ClusterInspector(variant=variant)

            # Try to login
            if inspector.login():
                print(f"✅ Logged into {variant} cluster")

                cluster_state = {}
                for ns in namespaces:
                    try:
                        health = inspector.get_namespace_health(ns)
                        cluster_state[ns] = health

                        print(f"\n   {ns}:")
                        print(f"     Pods ready: {health.get('pods_ready', 0)}/{health.get('total_pods', 0)}")
                        if health.get('failed_pods', 0) > 0:
                            print(f"     ⚠️  Failed pods: {health.get('failed_pods', 0)}")
                    except:
                        print(f"   ⚠️  Could not inspect {ns}")
            else:
                print(f"⚠️  Could not login to cluster (credentials not configured)")

        except Exception as e:
            print(f"⚠️  Cluster inspection skipped: {e}")

    # Step 9: Generate report
    print(f"\n📝 Generating report...")

    reporter = registry.load_reporter(component_name, framework)

    if not reporter:
        print(f"⚠️  No reporter configured, using default")
        from analyzer.interfaces.base_reporter import BaseReporter

        # Simple default reporter
        class DefaultReporter(BaseReporter):
            def generate_report(self, analysis_results, output_path=None):
                lines = []
                lines.append(f"# {self.component_name.title()} Test Analysis")
                lines.append(f"\n**Build**: #{analysis_results['metadata']['build_number']}")
                lines.append(f"**Status**: {analysis_results['metadata']['status']}")
                lines.append(f"**Date**: {analysis_results['metadata']['analysis_date']}\n")

                # Summary
                tr = analysis_results['test_results']
                lines.append(f"## Summary")
                lines.append(f"- Total: {tr['total']}")
                lines.append(f"- Passed: {tr['passed']}")
                lines.append(f"- Failed: {tr['failed']}")
                lines.append(f"- Skipped: {tr['skipped']}\n")

                # Failures
                if analysis_results.get('analyzed_failures'):
                    lines.append(f"## Failures\n")
                    for i, f in enumerate(analysis_results['analyzed_failures'], 1):
                        lines.append(f"### {i}. {f['test_name']}")
                        lines.append(f"**File**: {f.get('test_file', 'Unknown')}")
                        lines.append(f"**Error**: {f.get('error_message', 'No message')}\n")

                report = '\n'.join(lines)

                if output_path:
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    output_path.write_text(report)

                return report

        reporter = DefaultReporter(config)

    # Prepare analysis results
    analysis_results = {
        'metadata': {
            'component_name': component_name,
            'framework': framework,
            'build_number': build_number,
            'build_path': build_path,  # Relative path, not full URL
            'jenkins_job': jenkins_job,
            'status': build_result,
            'analysis_date': datetime.utcnow().isoformat(),
        },
        'test_results': results,
        'analyzed_failures': analyzed_failures,
        'cluster_state': cluster_state,
        'must_gather': must_gather_data,
        'cluster_logs': cluster_logs,
    }

    # Generate report (format depends on component's reporter)
    output_path = reporter.get_default_output_path(build_number, variant)

    # Try HTML first (if component has HTML reporter), fallback to markdown
    if hasattr(reporter, 'generate_html_report'):
        report = reporter.generate_html_report(analysis_results, output_path)
        print(f"✅ HTML Report generated: {output_path}")
    else:
        report = reporter.generate_report(analysis_results, output_path)
        print(f"✅ Report generated: {output_path}")

    # Also print to console
    print("\n" + "=" * 80)
    print("REPORT PREVIEW")
    print("=" * 80)
    print(report[:1000])  # First 1000 chars
    if len(report) > 1000:
        print(f"\n... (see full report at {output_path})")

    print("\n" + "=" * 80)
    print("✅ Analysis Complete")
    print("=" * 80)

    return analysis_results


def main():
    parser = argparse.ArgumentParser(
        description='Analyze component test results from Jenkins build',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze model-registry pytest tests
  python3 scripts/analyze_component.py model-registry pytest --build 28

  # Analyze dashboard Cypress tests
  python3 scripts/analyze_component.py dashboard cypress --build 3695

  # Specify custom Jenkins job
  python3 scripts/analyze_component.py model-registry pytest --build 28 \\
    --job "rhoai/3.4/selfmanaged/cli/aws/rhoai-sanity"

  # Analyze on ODH cluster instead of RHOAI
  python3 scripts/analyze_component.py dashboard cypress --build 100 --variant odh
        """
    )

    parser.add_argument('component', help='Component name (e.g., model-registry)')
    parser.add_argument('framework', help='Test framework (e.g., pytest, cypress)')
    parser.add_argument('--build', required=True, type=int, help='Jenkins build number')
    parser.add_argument('--job', help='Jenkins job path (uses component.json if not specified)')
    parser.add_argument('--variant', default='rhoai', choices=['rhoai', 'odh'],
                       help='Cluster variant (default: rhoai)')

    args = parser.parse_args()

    result = asyncio.run(analyze_component_build(
        args.component,
        args.framework,
        args.build,
        args.job,
        args.variant
    ))

    if result:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
