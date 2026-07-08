"""
Test Evidence Ingestion Strategy

Implements a 4-tier strategy for collecting test evidence with explicit fallback behavior:

1. Primary: JUnit XML artifacts (most reliable)
2. Secondary: Jenkins Test Report API (aggregated view)
3. Tertiary: Console output parsing (fallback)
4. Inconclusive: No parseable evidence found

This ensures we never silently under-report test results or failures.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
from enum import Enum


class EvidenceStatus(Enum):
    """Status of test evidence collection"""
    COMPLETE = 'complete'                    # All tests accounted for via XML artifacts
    PARTIAL = 'partial'                      # Only console or incomplete XML
    NO_TESTS_COLLECTED = 'no_tests_collected'  # Tests exist but all deselected
    INCONCLUSIVE = 'inconclusive'            # No parseable evidence found


@dataclass
class IngestionResult:
    """
    Result of test evidence ingestion

    Tracks test counts, evidence quality, and sources used.
    """
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0

    evidence_status: EvidenceStatus = EvidenceStatus.INCONCLUSIVE
    evidence_sources: List[str] = field(default_factory=list)

    warnings: List[str] = field(default_factory=list)
    failures: List[Dict[str, Any]] = field(default_factory=list)

    duration: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for compatibility with existing code"""
        return {
            'total': self.total,
            'passed': self.passed,
            'failed': self.failed,
            'skipped': self.skipped,
            'errors': self.errors,
            'evidence_status': self.evidence_status.value,
            'evidence_sources': self.evidence_sources,
            'warnings': self.warnings,
            'failures': self.failures,
            'duration': self.duration,
            'metadata': self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IngestionResult':
        """Create from dictionary"""
        evidence_status_str = data.get('evidence_status', 'inconclusive')
        try:
            evidence_status = EvidenceStatus(evidence_status_str)
        except ValueError:
            evidence_status = EvidenceStatus.INCONCLUSIVE

        return cls(
            total=data.get('total', 0),
            passed=data.get('passed', 0),
            failed=data.get('failed', 0),
            skipped=data.get('skipped', 0),
            errors=data.get('errors', 0),
            evidence_status=evidence_status,
            evidence_sources=data.get('evidence_sources', []),
            warnings=data.get('warnings', []),
            failures=data.get('failures', []),
            duration=data.get('duration'),
            metadata=data.get('metadata', {})
        )


class IngestionStrategy:
    """
    Orchestrates test evidence collection across multiple sources

    Implements 4-tier fallback strategy with explicit evidence tracking.
    """

    def __init__(self, parser, jenkins_client=None, component_config=None):
        """
        Initialize ingestion strategy

        Args:
            parser: Test parser instance (e.g., PytestDefaultParser)
            jenkins_client: Optional Jenkins client for Test Report API
            component_config: Optional component configuration dict (for suite filtering)
        """
        self.parser = parser
        self.jenkins_client = jenkins_client
        self.component_config = component_config or {}

    async def collect_evidence(
        self,
        console_log: str,
        artifacts: List[Dict[str, Any]],
        jenkins_job: str = None,
        build_number: int = None
    ) -> IngestionResult:
        """
        Collect test evidence from all available sources

        Args:
            console_log: Jenkins console output
            artifacts: List of artifact dictionaries with 'content' and 'type'
            jenkins_job: Optional Jenkins job path (for Test Report API)
            build_number: Optional build number (for Test Report API)

        Returns:
            IngestionResult with aggregated evidence and status
        """
        result = IngestionResult()

        # Tier 1: Try XML artifacts first (primary source)
        if artifacts:
            artifact_result = self._collect_from_artifacts(artifacts)
            if artifact_result.total > 0:
                result = artifact_result
                result.evidence_status = EvidenceStatus.COMPLETE
                result.evidence_sources.append('xml_artifacts')
                return result

        # Tier 2: Try Jenkins Test Report API (secondary source)
        print(f"   Tier 2: Trying Test Report API (jenkins_client={self.jenkins_client is not None}, job={jenkins_job}, build={build_number})")
        if self.jenkins_client and jenkins_job and build_number:
            api_result = await self._collect_from_test_report_api(jenkins_job, build_number)
            if api_result and api_result.total > 0:
                result = api_result
                result.evidence_status = EvidenceStatus.PARTIAL
                result.evidence_sources.append('jenkins_test_api')
                result.warnings.append('Using Jenkins Test Report API (XML artifacts not available)')
                print(f"   ✅ Test Report API succeeded: {api_result.total} tests")
                return result
            else:
                print(f"   ⓘ Test Report API returned no data")
        else:
            print(f"   ⓘ Test Report API skipped (missing client or job info)")

        # Tier 3: Try console output parsing (tertiary source)
        if console_log:
            console_result = self._collect_from_console(console_log)
            if console_result.total > 0 or console_result.evidence_status == EvidenceStatus.NO_TESTS_COLLECTED:
                result = console_result
                if result.evidence_status != EvidenceStatus.NO_TESTS_COLLECTED:
                    result.evidence_status = EvidenceStatus.PARTIAL
                    result.warnings.append('Console parsing only (no XML artifacts or Test Report API)')
                result.evidence_sources.append('console_output')
                return result

        # Tier 4: No parseable evidence found
        result.evidence_status = EvidenceStatus.INCONCLUSIVE
        result.warnings.append('No parseable test evidence found (no XML, no Test Report API, no console summary)')
        return result

    def _collect_from_artifacts(self, artifacts: List[Dict[str, Any]]) -> IngestionResult:
        """
        Collect evidence from XML artifacts

        Args:
            artifacts: List of dicts with 'content' and 'type' keys

        Returns:
            IngestionResult aggregated across all artifacts
        """
        aggregated = IngestionResult()

        for artifact in artifacts:
            content = artifact.get('content', '')
            artifact_type = artifact.get('type', 'xml')

            parsed = self.parser.parse_artifact(content, artifact_type)

            # Aggregate counts
            aggregated.total += parsed.get('total', 0)
            aggregated.passed += parsed.get('passed', 0)
            aggregated.failed += parsed.get('failed', 0)
            aggregated.skipped += parsed.get('skipped', 0)
            aggregated.errors += parsed.get('errors', 0)

            # Merge failures
            aggregated.failures.extend(parsed.get('failures', []))

        return aggregated

    def _collect_from_console(self, console_log: str) -> IngestionResult:
        """
        Collect evidence from console output

        Args:
            console_log: Jenkins console output

        Returns:
            IngestionResult from console parsing
        """
        parsed = self.parser.parse_console_output(console_log)
        return IngestionResult.from_dict(parsed)

    async def _collect_from_test_report_api(
        self,
        jenkins_job: str,
        build_number: int
    ) -> Optional[IngestionResult]:
        """
        Collect evidence from Jenkins Test Report API

        Args:
            jenkins_job: Jenkins job path
            build_number: Build number

        Returns:
            IngestionResult or None if API not available
        """
        if not self.jenkins_client:
            return None

        try:
            test_report = await self.jenkins_client.get_test_report(jenkins_job, build_number)
            if not test_report:
                return None

            result = IngestionResult()

            # Get suite filter from component config
            suite_filter = self.component_config.get('jenkins', {}).get('test_suite_filter')

            # Filter suites if configured
            suites = test_report.get('suites', [])
            if suite_filter:
                suites = [s for s in suites if s.get('name') == suite_filter]
                if not suites:
                    print(f"   ⚠️  Suite filter '{suite_filter}' matched no suites")
                    return None
                else:
                    print(f"   ✅ Filtered to suite: '{suite_filter}'")

            # Calculate totals from filtered suites
            total_tests = 0
            total_failed = 0
            total_skipped = 0
            total_passed = 0

            for suite in suites:
                for case in suite.get('cases', []):
                    status = case.get('status')
                    total_tests += 1
                    if status in ['FAILED', 'REGRESSION']:
                        total_failed += 1
                    elif status == 'SKIPPED':
                        total_skipped += 1
                    elif status in ['PASSED', 'FIXED']:
                        total_passed += 1

            result.total = total_tests
            result.failed = total_failed
            result.skipped = total_skipped
            result.passed = total_passed

            # Extract failures from filtered suites
            for suite in suites:
                for case in suite.get('cases', []):
                    if case.get('status') in ['FAILED', 'REGRESSION']:
                        error_details = case.get('errorDetails', 'No message')
                        stack_trace = case.get('errorStackTrace', '')

                        # If errorDetails is too generic, use stack trace instead
                        if error_details in ['Failed', 'No message', '', None] and stack_trace:
                            error_message = stack_trace
                        else:
                            error_message = error_details

                        result.failures.append({
                            'test_name': case.get('name', 'unknown'),
                            'test_file': case.get('className', 'unknown'),
                            'error_message': error_message,
                            'stack_trace': stack_trace,
                            'duration': str(case.get('duration', 0)),
                            'type': 'failure'
                        })

            return result

        except Exception as e:
            print(f"Warning: Failed to fetch Test Report API: {e}")
            return None
