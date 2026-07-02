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
            'duration': None,
            'failures': [],
            'metadata': {}
        }

        if not console_log:
            return results

        # Pattern 1: Summary line (most reliable)
        # "=== 1 failed, 2 passed, 1 skipped in 3.45s ==="
        summary_pattern = r'=+\s*(\d+)\s+failed.*?(\d+)\s+passed(?:.*?(\d+)\s+skipped)?.*?in\s+([\d.]+)s?\s*=+'
        summary_match = re.search(summary_pattern, console_log)

        if summary_match:
            results['failed'] = int(summary_match.group(1))
            results['passed'] = int(summary_match.group(2))
            results['skipped'] = int(summary_match.group(3) or 0)
            results['duration'] = summary_match.group(4)
            results['total'] = results['passed'] + results['failed'] + results['skipped']
        else:
            # Pattern 2: Alternate summary format
            # "=== 2 passed in 1.23s ==="
            alt_summary = r'=+\s*(\d+)\s+passed.*?in\s+([\d.]+)s?\s*=+'
            alt_match = re.search(alt_summary, console_log)
            if alt_match:
                results['passed'] = int(alt_match.group(1))
                results['duration'] = alt_match.group(2)
                results['total'] = results['passed']

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

        Example JUnit XML:
        <testsuite name="pytest" tests="2" failures="1" skipped="0">
            <testcase classname="test_create_model" name="test_create_model_success" time="0.5"/>
            <testcase classname="test_create_model" name="test_create_model_invalid" time="0.3">
                <failure message="AssertionError: assert 422 == 200">
                    [stack trace]
                </failure>
            </testcase>
        </testsuite>
        """
        results = {
            'total': 0,
            'passed': 0,
            'failed': 0,
            'skipped': 0,
            'duration': None,
            'failures': []
        }

        if artifact_type != 'xml':
            return results

        try:
            root = ET.fromstring(artifact_content)

            # Get testsuite element
            testsuite = root if root.tag == 'testsuite' else root.find('.//testsuite')

            if testsuite is not None:
                results['total'] = int(testsuite.get('tests', 0))
                results['failed'] = int(testsuite.get('failures', 0))
                results['skipped'] = int(testsuite.get('skipped', 0))
                results['passed'] = results['total'] - results['failed'] - results['skipped']

                # Extract individual test cases
                for testcase in testsuite.findall('.//testcase'):
                    failure = testcase.find('failure')
                    if failure is not None:
                        results['failures'].append({
                            'test_file': testcase.get('classname', 'unknown') + '.py',
                            'test_name': testcase.get('name', 'unknown'),
                            'error_message': failure.get('message', 'No message'),
                            'stack_trace': failure.text or '',
                            'duration': testcase.get('time', '0')
                        })

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

    parser = ModelRegistryPytestParser({})
    results = parser.parse_console_output(sample_output)

    print("Results:", results)
    print("\nFailures:")
    for failure in parser.extract_failures(results):
        print(f"  - {failure['test_name']}: {failure['error_message']}")
        print(f"    Rerun: {parser.get_rerun_command(failure)}")
