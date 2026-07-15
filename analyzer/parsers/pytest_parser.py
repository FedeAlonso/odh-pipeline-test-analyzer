"""
Default pytest Parser

Works for ANY pytest project - parses standard pytest output format.
Can be used as-is or customized for component-specific needs.
"""
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Any
from pathlib import Path

from analyzer.interfaces.base_parser import BaseTestParser


class PytestDefaultParser(BaseTestParser):
    """
    Default parser for pytest test results

    Works for ANY project using standard pytest output format.
    Parses both console output and JUnit XML artifacts.
    """

    def parse_console_output(self, console_log: str) -> Dict[str, Any]:
        """
        Parse pytest results from Jenkins console output

        Example pytest output:
        ============================= test session starts ==============================
        test_create_model.py::test_create_model_success PASSED                  [ 50%]
        test_create_model.py::test_create_model_invalid FAILED                  [100%]

        =================================== FAILURES ===================================
        _______________________ test_create_model_invalid __________________________

            def test_create_model_invalid():
        >       assert response.status_code == 200
        E       AssertionError: assert 422 == 200

        ========================= 1 failed, 1 passed in 2.5s ==========================
        """
        results = {
            'total': 0,
            'passed': 0,
            'failed': 0,
            'skipped': 0,
            'errors': 0,
            'duration': None,
            'evidence_status': 'none',
            'evidence_sources': [],
            'warnings': [],
            'failures': [],
            'metadata': {}
        }

        if not console_log:
            results['evidence_status'] = 'inconclusive'
            results['warnings'].append('No console output available')
            return results

        # Find the pytest summary line: "=== 1 failed, 2 passed, 1 skipped in 3.45s ==="
        summary_line_match = re.search(r'(=+\s*.+?\s+in\s+[\d.]+s?\s*=+)', console_log)

        if summary_line_match:
            summary_line = summary_line_match.group(1)
            counts = re.findall(r'(\d+)\s+(passed|failed|skipped|errors?)', summary_line)
            for count, status in counts:
                if status == 'passed':
                    results['passed'] = int(count)
                elif status == 'failed':
                    results['failed'] = int(count)
                elif status == 'skipped':
                    results['skipped'] = int(count)
                elif status.startswith('error'):
                    results['errors'] = int(count)

            duration_match = re.search(r'in\s+([\d.]+)s?', summary_line)
            if duration_match:
                results['duration'] = duration_match.group(1)

            results['total'] = results['passed'] + results['failed'] + results['skipped'] + results['errors']
            results['evidence_status'] = 'console'
            results['evidence_sources'].append('console_summary')
        else:
            results['evidence_status'] = 'partial'
            results['warnings'].append('No pytest summary line found in console output')

        # Extract individual test results
        # Pattern: "test_file.py::test_name PASSED/FAILED/SKIPPED [XX%]"
        test_pattern = r'([\w/]+\.py)::([\w_]+)\s+(PASSED|FAILED|SKIPPED|ERROR)'
        test_matches = re.findall(test_pattern, console_log)

        # Track test files and names
        test_files = set()
        for test_file, test_name, status in test_matches:
            test_files.add(test_file)

            if status in ['FAILED', 'ERROR']:
                # This is a failure - we'll extract details below
                results['failures'].append({
                    'test_file': test_file,
                    'test_name': test_name,
                    'status': status,
                    'error_message': '',  # Will be filled by _extract_failure_details
                    'stack_trace': ''
                })

        # Extract failure details
        if results['failures']:
            results['failures'] = self._extract_failure_details(console_log, results['failures'])

        # Detect "no tests collected" state
        # This happens when tests exist but are all deselected by markers or filters
        # Pattern: "=== N deselected in X.XXs ==="
        deselected_only_pattern = r'=+\s*(\d+)\s+deselected\s+in\s+[\d.]+s?\s*=+'
        deselected_match = re.search(deselected_only_pattern, console_log)

        if deselected_match and results['total'] == 0:
            # Only deselected tests, no actual execution
            results['evidence_status'] = 'no_tests_collected'
            results['warnings'].append(
                f"{deselected_match.group(1)} tests were deselected - no actual test execution occurred"
            )

        # Metadata
        results['metadata'] = {
            'test_files': list(test_files),
            'pytest_version': self._extract_pytest_version(console_log)
        }

        return results

    def _extract_failure_details(self, console_log: str, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Extract detailed error messages and stack traces for failures

        Example failure section:
        =================================== FAILURES ===================================
        _______________________ test_create_model_invalid __________________________

            def test_create_model_invalid():
        >       assert response.status_code == 200
        E       AssertionError: assert 422 == 200
        """
        # Find the FAILURES section
        failures_section = re.search(
            r'=+ FAILURES =+(.+?)(?:=+ |$)',
            console_log,
            re.DOTALL
        )

        if not failures_section:
            return failures

        failures_text = failures_section.group(1)

        for failure in failures:
            test_name = failure['test_name']

            # Find this test's failure block
            # Pattern: _____ test_name _____ ... next _____ or end
            failure_pattern = rf'_+ {re.escape(test_name)} _+(.+?)(?:_{{20,}}|$)'
            failure_match = re.search(failure_pattern, failures_text, re.DOTALL)

            if failure_match:
                failure_text = failure_match.group(1).strip()

                # Extract error message (lines starting with 'E ')
                error_lines = re.findall(r'^E\s+(.+)$', failure_text, re.MULTILINE)
                if error_lines:
                    failure['error_message'] = '\n'.join(error_lines)
                else:
                    # Fallback: first non-empty line
                    lines = [l.strip() for l in failure_text.split('\n') if l.strip()]
                    failure['error_message'] = lines[0] if lines else 'Unknown error'

                # Stack trace is the whole block
                failure['stack_trace'] = failure_text[:500]  # Limit to 500 chars

        return failures

    def _extract_pytest_version(self, console_log: str) -> str:
        """Extract pytest version from output"""
        version_match = re.search(r'pytest[- ](\d+\.\d+\.\d+)', console_log)
        return version_match.group(1) if version_match else 'unknown'

    def parse_artifact(self, artifact_content: str, artifact_type: str) -> Dict[str, Any]:
        """
        Parse test results from artifact files (JUnit XML)

        Supports both single testsuite and multiple testsuites formats:

        Single suite:
        <testsuite name="pytest" tests="2" failures="1" skipped="0">
            <testcase classname="test_create_model" name="test_create_model_success" time="0.5"/>
            ...
        </testsuite>

        Multiple suites:
        <testsuites>
            <testsuite name="suite1" tests="10" failures="2" errors="1"/>
            <testsuite name="suite2" tests="15" failures="1" errors="0"/>
        </testsuites>
        """
        results = {
            'total': 0,
            'passed': 0,
            'failed': 0,
            'skipped': 0,
            'errors': 0,
            'duration': None,
            'failures': []
        }

        if artifact_type != 'xml':
            return results

        try:
            root = ET.fromstring(artifact_content)

            # Handle both <testsuite> (single) and <testsuites> (multiple) root elements
            # This fixes the bug where only the first suite was parsed
            if root.tag == 'testsuites':
                # Multiple suites - aggregate all
                all_suites = root.findall('.//testsuite')
            elif root.tag == 'testsuite':
                # Single suite
                all_suites = [root]
            else:
                # Unknown format
                all_suites = []

            # Aggregate totals across ALL suites
            for suite in all_suites:
                results['total'] += int(suite.get('tests', 0))
                results['failed'] += int(suite.get('failures', 0))
                results['skipped'] += int(suite.get('skipped', 0))
                results['errors'] += int(suite.get('errors', 0))

                # Extract individual test cases from each suite
                for testcase in suite.findall('.//testcase'):
                    # Check for failure
                    failure = testcase.find('failure')
                    if failure is not None:
                        results['failures'].append({
                            'test_file': testcase.get('classname', 'unknown') + '.py',
                            'test_name': testcase.get('name', 'unknown'),
                            'error_message': failure.get('message', 'No message'),
                            'stack_trace': failure.text or '',
                            'duration': testcase.get('time', '0'),
                            'type': 'failure'
                        })

                    # Check for error (setup/teardown failures)
                    error = testcase.find('error')
                    if error is not None:
                        results['failures'].append({
                            'test_file': testcase.get('classname', 'unknown') + '.py',
                            'test_name': testcase.get('name', 'unknown'),
                            'error_message': error.get('message', 'No message'),
                            'stack_trace': error.text or '',
                            'duration': testcase.get('time', '0'),
                            'type': 'error'
                        })

            # Calculate passed count
            results['passed'] = results['total'] - results['failed'] - results['skipped'] - results['errors']

        except ET.ParseError as e:
            print(f"Error parsing XML artifact: {e}")

        return results

    def extract_failures(self, parsed_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract individual test failures with full details"""
        return parsed_results.get('failures', [])

    def get_rerun_command(self, failure: Dict[str, Any]) -> str:
        """
        Generate command to rerun a specific failed test

        Example: pytest -v test/integration/test_create_model.py::test_create_model_invalid
        """
        test_file = failure.get('test_file', '')
        test_name = failure.get('test_name', '')

        # Construct pytest command
        if test_file and test_name:
            # Full path to specific test
            return f"pytest -v {test_file}::{test_name}"
        elif test_file:
            # Just the file
            return f"pytest -v {test_file}"
        else:
            return "pytest -v"


# Example usage for testing
if __name__ == '__main__':
    # Test with sample console output
    sample_output = """
    ============================= test session starts ==============================
    platform linux -- Python 3.11.0, pytest-7.4.0
    test_create_model.py::test_create_model_success PASSED                  [ 50%]
    test_create_model.py::test_create_model_invalid FAILED                  [100%]

    =================================== FAILURES ===================================
    _______________________ test_create_model_invalid __________________________

        def test_create_model_invalid():
    >       assert response.status_code == 200
    E       AssertionError: assert 422 == 200

    ========================= 1 failed, 1 passed in 2.5s ==========================
    """

    parser = PytestDefaultParser({})
    results = parser.parse_console_output(sample_output)

    print("Results:", results)
    print("\nFailures:")
    for failure in parser.extract_failures(results):
        print(f"  - {failure['test_name']}: {failure['error_message']}")
        print(f"    Rerun: {parser.get_rerun_command(failure)}")
