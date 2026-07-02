"""
Base interface for test result parsers

Each test framework (Cypress, pytest, Jest, etc.) should implement this interface
to provide consistent parsing capabilities across the platform.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional


class BaseTestParser(ABC):
    """Abstract base class for test framework parsers"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize parser with component configuration

        Args:
            config: Component configuration from component.json
        """
        self.config = config
        self.framework = config.get('test_framework', {})

    @abstractmethod
    def parse_console_output(self, console_log: str) -> Dict[str, Any]:
        """
        Parse test results from Jenkins console output

        Args:
            console_log: Raw console output from Jenkins build

        Returns:
            Dictionary containing:
                - total: Total number of tests
                - passed: Number of passed tests
                - failed: Number of failed tests
                - skipped: Number of skipped tests
                - duration: Test execution duration (if available)
                - failures: List of failure dictionaries
                - metadata: Any additional metadata
        """
        pass

    @abstractmethod
    def parse_artifact(self, artifact_content: str, artifact_type: str) -> Dict[str, Any]:
        """
        Parse test results from artifact files (XML, JSON, HTML, etc.)

        Args:
            artifact_content: Content of the artifact file
            artifact_type: Type of artifact ('xml', 'json', 'html', etc.)

        Returns:
            Dictionary with same structure as parse_console_output
        """
        pass

    @abstractmethod
    def extract_failures(self, parsed_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract individual test failures with full details

        Args:
            parsed_results: Output from parse_console_output or parse_artifact

        Returns:
            List of failure dictionaries, each containing:
                - test_name: Name of the failed test
                - test_file: Path to test file
                - suite_name: Test suite/describe block name (if applicable)
                - error_message: Error message
                - stack_trace: Stack trace (if available)
                - category: Failure category (timeout, assertion, etc.)
                - duration: Test duration (if available)
                - screenshots: List of screenshot paths (if applicable)
                - metadata: Any additional metadata
        """
        pass

    @abstractmethod
    def get_rerun_command(self, failure: Dict[str, Any]) -> str:
        """
        Generate command to rerun a specific failed test

        Args:
            failure: Failure dictionary from extract_failures

        Returns:
            Shell command string to rerun the specific test
        """
        pass

    def parse_build(self, console_log: str, artifacts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        Convenience method to parse both console output and artifacts

        Args:
            console_log: Jenkins console output
            artifacts: List of artifacts, each with 'content' and 'type' keys

        Returns:
            Merged results from all sources
        """
        # Parse console output first
        results = self.parse_console_output(console_log)

        # Parse artifacts if available
        if artifacts:
            for artifact in artifacts:
                artifact_results = self.parse_artifact(
                    artifact.get('content', ''),
                    artifact.get('type', 'unknown')
                )
                # Merge results (prefer artifact data as it's usually more structured)
                results = self._merge_results(results, artifact_results)

        return results

    def _merge_results(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge two result dictionaries, preferring override values

        Args:
            base: Base results dictionary
            override: Override results dictionary

        Returns:
            Merged dictionary
        """
        merged = base.copy()

        # Override numeric fields if they exist in override
        for key in ['total', 'passed', 'failed', 'skipped', 'duration']:
            if key in override and override[key] is not None:
                merged[key] = override[key]

        # Merge failure lists (avoid duplicates)
        base_failures = merged.get('failures', [])
        override_failures = override.get('failures', [])

        # Simple deduplication by test name
        seen_tests = {f.get('test_name') for f in base_failures}
        for failure in override_failures:
            if failure.get('test_name') not in seen_tests:
                base_failures.append(failure)

        merged['failures'] = base_failures

        # Merge metadata
        base_metadata = merged.get('metadata', {})
        override_metadata = override.get('metadata', {})
        merged['metadata'] = {**base_metadata, **override_metadata}

        return merged

    def validate_results(self, results: Dict[str, Any]) -> bool:
        """
        Validate that parsed results have required fields

        Args:
            results: Parsed results dictionary

        Returns:
            True if valid, False otherwise
        """
        required_fields = ['total', 'passed', 'failed']
        return all(field in results for field in required_fields)
