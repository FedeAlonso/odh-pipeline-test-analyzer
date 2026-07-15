"""
Default Failure Analyzer

Provides intelligent failure analysis for component teams.
This is the default analyzer used when teams don't provide their own.

Teams can copy and customize this for their specific needs.
"""
import re
from typing import Dict, List, Any, Optional
from pathlib import Path


class DefaultFailureAnalyzer:
    """
    Default failure analyzer with intelligent investigation

    Performs:
    - Root cause analysis from error messages
    - GitHub PR correlation
    - Jira ticket search
    - Cluster health checks
    - Recommendations
    """

    def __init__(self, component_config: Dict[str, Any]):
        """
        Initialize analyzer

        Args:
            component_config: Component configuration from component.json
        """
        self.component_name = component_config.get('name', 'unknown')
        self.config = component_config
        self.repo_url = component_config.get('repository', {}).get('url', '')

    def analyze_failures(
        self,
        failures: List[Dict[str, Any]],
        build_info: Dict[str, Any],
        must_gather_data: Any = None
    ) -> Dict[str, Any]:
        """
        Analyze test failures and provide insights

        Args:
            failures: List of test failures
            build_info: Build metadata (build number, job, cluster, etc.)
            must_gather_data: Optional must-gather diagnostic data

        Returns:
            Dict with analysis results
        """
        if not failures:
            return {'analysis': 'No failures to analyze', 'recommendations': []}

        analysis_results = {
            'total_failures': len(failures),
            'failure_clusters': [],
            'root_causes': [],
            'related_prs': [],
            'related_jiras': [],
            'cluster_issues': [],
            'recommendations': [],
            'must_gather_insights': []
        }

        # Cluster failures by similarity
        clusters = self._cluster_failures(failures)
        analysis_results['failure_clusters'] = clusters

        # Correlate with must-gather data if available
        if must_gather_data:
            mg_insights = self._correlate_with_must_gather(failures, must_gather_data)
            analysis_results['must_gather_insights'] = mg_insights

        # Analyze each cluster
        for cluster in clusters:
            root_cause = self._analyze_cluster(cluster, build_info, must_gather_data)
            if root_cause:
                analysis_results['root_causes'].append(root_cause)

        # Generate recommendations
        analysis_results['recommendations'] = self._generate_recommendations(
            analysis_results
        )

        return analysis_results

    def _cluster_failures(self, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Cluster failures by error similarity

        Groups failures with similar error messages together.
        """
        clusters = []
        clustered_indices = set()

        for i, failure in enumerate(failures):
            if i in clustered_indices:
                continue

            # Start new cluster
            cluster = {
                'representative': failure,
                'count': 1,
                'failures': [failure],
                'error_pattern': self._extract_error_pattern(failure)
            }

            # Find similar failures
            for j, other in enumerate(failures[i+1:], start=i+1):
                if j in clustered_indices:
                    continue

                if self._are_similar(failure, other):
                    cluster['failures'].append(other)
                    cluster['count'] += 1
                    clustered_indices.add(j)

            clustered_indices.add(i)
            clusters.append(cluster)

        # Sort by count (largest clusters first)
        clusters.sort(key=lambda x: x['count'], reverse=True)
        return clusters

    def _extract_error_pattern(self, failure: Dict[str, Any]) -> str:
        """Extract the core error pattern from a failure"""
        error_msg = failure.get('error_message', '')

        # Common patterns
        patterns = [
            (r'AssertionError: (.+)', 'assertion'),
            (r'TimeoutError: (.+)', 'timeout'),
            (r'ConnectionError: (.+)', 'connection'),
            (r'.*Error: (.+)', 'error'),
            (r'failed on setup with "(.+)"', 'setup'),
        ]

        for pattern, category in patterns:
            match = re.search(pattern, error_msg, re.IGNORECASE)
            if match:
                return f"{category}: {match.group(1)[:100]}"

        return error_msg[:100] if error_msg else 'unknown'

    def _are_similar(self, f1: Dict[str, Any], f2: Dict[str, Any]) -> bool:
        """Check if two failures are similar enough to cluster"""
        # Same error pattern
        p1 = self._extract_error_pattern(f1)
        p2 = self._extract_error_pattern(f2)

        if p1 == p2:
            return True

        # Similar error messages
        e1 = f1.get('error_message', '')
        e2 = f2.get('error_message', '')

        # Extract key phrases
        key1 = ' '.join(re.findall(r'\b[A-Z][a-z]+(?:Error|Exception)\b', e1))
        key2 = ' '.join(re.findall(r'\b[A-Z][a-z]+(?:Error|Exception)\b', e2))

        if key1 and key1 == key2:
            return True

        return False

    def _correlate_with_must_gather(
        self,
        failures: List[Dict[str, Any]],
        must_gather_data: Any
    ) -> List[str]:
        """
        Correlate test failures with must-gather diagnostics

        Returns:
            List of insights from must-gather correlation
        """
        insights = []

        # Get pod failures from must-gather
        from analyzer.must_gather_parser import MustGatherParser
        parser = MustGatherParser(must_gather_data.archive_path)
        failing_pods = parser.get_failing_pods(must_gather_data)
        pod_log_errors = parser.get_pod_failures(must_gather_data)

        # Check if any test failures correlate with pod failures
        if failing_pods:
            insights.append(f"Found {len(failing_pods)} pods in failed/error state during test execution")

            # Try to match test names with pod names
            for pod in failing_pods[:5]:
                pod_name = pod.get('name', '')
                reason = pod.get('reason', 'Unknown')
                insights.append(f"  - {pod.get('namespace')}/{pod_name}: {reason}")

        # Check for log errors
        if pod_log_errors:
            insights.append(f"Found {len(pod_log_errors)} errors in pod logs")

            # Group by namespace
            namespaces = {}
            for error in pod_log_errors:
                ns = error.get('namespace')
                if ns not in namespaces:
                    namespaces[ns] = 0
                namespaces[ns] += 1

            for ns, count in sorted(namespaces.items(), key=lambda x: x[1], reverse=True)[:3]:
                insights.append(f"  - {ns}: {count} log errors")

        # Check events
        if must_gather_data.events:
            total_events = sum(len(e) for e in must_gather_data.events.values())
            insights.append(f"Found {total_events} cluster warning/error events")

        return insights

    def _analyze_cluster(
        self,
        cluster: Dict[str, Any],
        build_info: Dict[str, Any],
        must_gather_data: Any = None
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze a cluster of failures to find root cause

        Returns root cause analysis with:
        - Category (setup, assertion, timeout, infrastructure)
        - Probable cause
        - Related PRs (if GitHub available)
        - Related Jiras (if Jira available)
        - Fix suggestions
        """
        representative = cluster['representative']
        error_msg = representative.get('error_message', '')
        test_name = representative.get('test_name', 'unknown')

        root_cause = {
            'cluster_size': cluster['count'],
            'error_pattern': cluster['error_pattern'],
            'category': 'unknown',
            'probable_cause': '',
            'affected_tests': [f['test_name'] for f in cluster['failures'][:10]],
            'suggestions': []
        }

        # Categorize the failure
        if 'AssertionError' in error_msg and 'No validated catalog models' in error_msg:
            root_cause['category'] = 'test_data'
            root_cause['probable_cause'] = 'Missing test data: No validated catalog models with benchmark metadata available'
            root_cause['suggestions'] = [
                'Check if model catalog service is running',
                'Verify test data setup in the pod',
                'Review model-catalog-pod configuration',
                'Check CATALOG_CONTAINER environment variable'
            ]

        elif 'timeout' in error_msg.lower():
            root_cause['category'] = 'timeout'
            root_cause['probable_cause'] = 'Operation exceeded time limit'
            root_cause['suggestions'] = [
                'Check cluster resource availability',
                'Review pod startup times',
                'Investigate slow API endpoints',
                'Consider increasing timeout values if consistently slow'
            ]

        elif 'setup' in error_msg.lower() or 'fixture' in error_msg.lower():
            root_cause['category'] = 'setup'
            root_cause['probable_cause'] = 'Test setup/fixture failure'
            root_cause['suggestions'] = [
                'Review test fixture configuration',
                'Check prerequisite services are running',
                'Verify test environment setup',
                'Check database/cluster initialization'
            ]

        elif 'connection' in error_msg.lower() or 'network' in error_msg.lower():
            root_cause['category'] = 'network'
            root_cause['probable_cause'] = 'Network connectivity issue'
            root_cause['suggestions'] = [
                'Verify service endpoints are accessible',
                'Check network policies and routes',
                'Review pod-to-pod communication',
                'Check DNS resolution'
            ]

        elif 'permission' in error_msg.lower() or 'forbidden' in error_msg.lower() or '403' in error_msg:
            root_cause['category'] = 'permissions'
            root_cause['probable_cause'] = 'Authorization/permissions failure'
            root_cause['suggestions'] = [
                'Review RBAC configuration',
                'Check service account permissions',
                'Verify API tokens are valid',
                'Review namespace access controls'
            ]

        else:
            root_cause['category'] = 'assertion'
            root_cause['probable_cause'] = 'Test assertion failure - unexpected behavior'
            root_cause['suggestions'] = [
                'Review test expectations vs actual behavior',
                'Check for recent code changes',
                'Verify API response format',
                'Review application logs for errors'
            ]

        # Add GitHub search hint if repo available
        if self.repo_url:
            repo_parts = self.repo_url.rstrip('/').split('/')
            if len(repo_parts) >= 2:
                owner_repo = f"{repo_parts[-2]}/{repo_parts[-1].replace('.git', '')}"
                root_cause['github_search'] = {
                    'repo': owner_repo,
                    'test_file_search': f"repo:{owner_repo} {test_name}",
                    'error_search': f"repo:{owner_repo} {self._extract_search_terms(error_msg)}"
                }

        return root_cause

    def _extract_search_terms(self, error_msg: str) -> str:
        """Extract key search terms from error message"""
        # Remove stack traces and common noise
        msg = re.sub(r'File ".*?"', '', error_msg)
        msg = re.sub(r'line \d+', '', msg)
        msg = re.sub(r'0x[0-9a-f]+', '', msg)

        # Extract key error terms
        terms = re.findall(r'\b[A-Z][a-z]+(?:Error|Exception)\b', msg)
        terms += re.findall(r'\b(?:assert|timeout|connection|failed|missing)\b', msg, re.IGNORECASE)

        return ' '.join(set(terms[:5]))

    def _generate_recommendations(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate actionable recommendations based on analysis"""
        recommendations = []

        # High-level recommendations based on root causes
        categories = [rc['category'] for rc in analysis['root_causes']]

        if 'test_data' in categories:
            recommendations.append(
                '🔍 **Test Data Issue**: Multiple tests failing due to missing catalog models. '
                'Priority: Investigate model catalog initialization.'
            )

        if 'setup' in categories or 'fixture' in categories:
            recommendations.append(
                '⚙️ **Setup Failures**: Tests failing during setup phase. '
                'Priority: Review test fixtures and environment configuration.'
            )

        if 'network' in categories or 'connection' in categories:
            recommendations.append(
                '🌐 **Network Issues**: Connection failures detected. '
                'Priority: Check service health and network policies.'
            )

        if 'timeout' in categories:
            recommendations.append(
                '⏱️ **Timeout Failures**: Operations exceeding time limits. '
                'Priority: Check cluster resources and pod performance.'
            )

        # Cluster size recommendations
        largest_cluster = max((rc['cluster_size'] for rc in analysis['root_causes']), default=0)
        if largest_cluster > 10:
            recommendations.append(
                f'📊 **High Impact**: {largest_cluster} tests failing with similar error. '
                'This suggests a systemic issue rather than individual test problems.'
            )

        # GitHub investigation
        if any('github_search' in rc for rc in analysis['root_causes']):
            recommendations.append(
                '💻 **Next Steps**: Search GitHub for related PRs and issues using the provided search terms.'
            )

        return recommendations if recommendations else [
            '✅ Analysis complete. Review failure clusters and suggestions above.'
        ]


# Default instance for quick usage
def analyze_failures(failures: List[Dict[str, Any]], config: Dict[str, Any], build_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience function for default analysis

    Args:
        failures: List of test failures
        config: Component configuration
        build_info: Build metadata

    Returns:
        Analysis results
    """
    analyzer = DefaultFailureAnalyzer(config)
    return analyzer.analyze_failures(failures, build_info)
