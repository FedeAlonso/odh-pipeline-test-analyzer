"""
Base interface for failure analyzers

Provides common interface for analyzing test failures and determining root causes
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional


class BaseFailureAnalyzer(ABC):
    """Abstract base class for test failure analyzers"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize analyzer with component configuration

        Args:
            config: Component configuration from component.json
        """
        self.config = config
        self.analysis_config = config.get('analysis', {})
        self.failure_categories = self.analysis_config.get('failure_categories', [])

    @abstractmethod
    def categorize_failure(self, failure: Dict[str, Any]) -> str:
        """
        Categorize a test failure based on error message and stack trace

        Args:
            failure: Failure dictionary containing test_name, error_message, stack_trace

        Returns:
            Category string (must be one of self.failure_categories)
        """
        pass

    @abstractmethod
    def determine_root_cause(self, failure: Dict[str, Any],
                            cluster_state: Optional[Dict[str, Any]] = None) -> str:
        """
        Analyze failure with optional cluster context to determine root cause

        Args:
            failure: Failure dictionary
            cluster_state: Optional cluster health state

        Returns:
            Human-readable root cause description
        """
        pass

    @abstractmethod
    def get_recommended_actions(self, failure: Dict[str, Any]) -> List[str]:
        """
        Generate list of recommended debugging actions for this failure

        Args:
            failure: Failure dictionary

        Returns:
            List of actionable debugging steps
        """
        pass

    def analyze_failure(self, failure: Dict[str, Any],
                       cluster_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Comprehensive failure analysis

        Args:
            failure: Failure dictionary
            cluster_state: Optional cluster health state

        Returns:
            Enhanced failure dictionary with analysis results:
                - category: Failure category
                - root_cause: Root cause description
                - recommended_actions: List of debugging actions
                - severity: Severity level (critical, high, medium, low)
                - is_flaky: Boolean indicating if failure appears flaky
        """
        analysis = {
            'category': self.categorize_failure(failure),
            'root_cause': self.determine_root_cause(failure, cluster_state),
            'recommended_actions': self.get_recommended_actions(failure),
            'severity': self.assess_severity(failure),
            'is_flaky': self.is_likely_flaky(failure)
        }

        # Merge with original failure data
        return {**failure, **analysis}

    def assess_severity(self, failure: Dict[str, Any]) -> str:
        """
        Assess failure severity

        Args:
            failure: Failure dictionary

        Returns:
            Severity level: 'critical', 'high', 'medium', or 'low'
        """
        category = failure.get('category', 'unknown')

        # Default severity mapping (can be overridden)
        severity_map = {
            'auth': 'critical',
            'authorization': 'critical',
            'resource': 'high',
            'api_error': 'high',
            'database_error': 'high',
            'timeout': 'medium',
            'assertion': 'medium',
            'network': 'medium',
            'element_not_found': 'low',
            'unknown': 'medium'
        }

        return severity_map.get(category, 'medium')

    def is_likely_flaky(self, failure: Dict[str, Any]) -> bool:
        """
        Determine if failure is likely flaky based on patterns

        Args:
            failure: Failure dictionary

        Returns:
            True if failure appears flaky, False otherwise
        """
        error_msg = failure.get('error_message', '').lower()
        category = failure.get('category', '')

        # Common flaky patterns
        flaky_indicators = [
            'timeout',
            'timed out',
            'intermittent',
            'connection refused',
            'temporarily unavailable',
            'race condition',
            'network',
            'eventually failed'
        ]

        # Timeouts are often flaky
        if category == 'timeout':
            return True

        # Check for flaky patterns in error message
        return any(indicator in error_msg for indicator in flaky_indicators)

    def correlate_with_cluster(self, failure: Dict[str, Any],
                               cluster_state: Dict[str, Any]) -> List[str]:
        """
        Find correlations between test failure and cluster state

        Args:
            failure: Failure dictionary
            cluster_state: Cluster health state

        Returns:
            List of correlation findings
        """
        correlations = []

        if not cluster_state:
            return correlations

        category = failure.get('category', '')

        # Check for pod issues
        if cluster_state.get('pods_not_ready', 0) > 0:
            correlations.append(
                f"Found {cluster_state['pods_not_ready']} pods not ready - may be related to {category} failures"
            )

        # Check for recent events
        if cluster_state.get('recent_errors', 0) > 0:
            correlations.append(
                f"Found {cluster_state['recent_errors']} recent error events in cluster"
            )

        # Check for resource issues
        if cluster_state.get('resource_issues', []):
            for issue in cluster_state['resource_issues']:
                correlations.append(f"Resource issue: {issue}")

        return correlations

    def analyze_batch(self, failures: List[Dict[str, Any]],
                     cluster_state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Analyze multiple failures and generate summary statistics

        Args:
            failures: List of failure dictionaries
            cluster_state: Optional cluster health state

        Returns:
            Analysis summary:
                - analyzed_failures: List of failures with analysis
                - by_category: Failures grouped by category
                - by_severity: Failures grouped by severity
                - total_failures: Total count
                - flaky_count: Count of likely flaky failures
                - correlations: Cluster correlations
        """
        analyzed_failures = [
            self.analyze_failure(f, cluster_state) for f in failures
        ]

        # Group by category
        by_category = {}
        for failure in analyzed_failures:
            category = failure.get('category', 'unknown')
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(failure)

        # Group by severity
        by_severity = {}
        for failure in analyzed_failures:
            severity = failure.get('severity', 'medium')
            if severity not in by_severity:
                by_severity[severity] = []
            by_severity[severity].append(failure)

        # Count flaky failures
        flaky_count = sum(1 for f in analyzed_failures if f.get('is_flaky', False))

        # Find correlations
        correlations = []
        if cluster_state and analyzed_failures:
            correlations = self.correlate_with_cluster(analyzed_failures[0], cluster_state)

        return {
            'analyzed_failures': analyzed_failures,
            'by_category': by_category,
            'by_severity': by_severity,
            'total_failures': len(analyzed_failures),
            'flaky_count': flaky_count,
            'correlations': correlations
        }
