"""
Parser Contract Tests

Tests for pytest parser to ensure correctness across edge cases:
- Multi-suite XML aggregation
- No tests collected detection
- Errors vs failures tracking
- Console parsing fallback
- Inconclusive state handling
"""
import pytest
from analyzer.parsers.pytest_parser import PytestDefaultParser


class TestPytestParser:
    """Test suite for pytest parser contract"""

    def setup_method(self):
        """Setup test fixtures"""
        self.parser = PytestDefaultParser({})

    def test_multi_suite_aggregation(self):
        """
        Verify ALL testsuite nodes are aggregated when root is <testsuites>

        This is the critical bug fix from PR #5 review.
        """
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
    <testsuite name="suite1" tests="10" failures="2" errors="1" skipped="1">
        <testcase name="test1" classname="test_module1" time="0.5">
            <failure message="AssertionError">Stack trace 1</failure>
        </testcase>
        <testcase name="test2" classname="test_module1" time="0.3">
            <error message="SetupError">Setup failed</error>
        </testcase>
    </testsuite>
    <testsuite name="suite2" tests="15" failures="1" errors="0" skipped="2">
        <testcase name="test3" classname="test_module2" time="1.2">
            <failure message="ValueError">Stack trace 2</failure>
        </testcase>
    </testsuite>
</testsuites>"""

        result = self.parser.parse_artifact(xml_content, 'xml')

        # Should aggregate ALL suites
        assert result['total'] == 25, f"Expected 25 total (10+15), got {result['total']}"
        assert result['failed'] == 3, f"Expected 3 failures (2+1), got {result['failed']}"
        assert result['errors'] == 1, f"Expected 1 error (1+0), got {result['errors']}"
        assert result['skipped'] == 3, f"Expected 3 skipped (1+2), got {result['skipped']}"
        assert result['passed'] == 18, f"Expected 18 passed (25-3-1-3), got {result['passed']}"

        # Should extract failures from both suites
        assert len(result['failures']) == 3, f"Expected 3 failure details, got {len(result['failures'])}"

    def test_single_suite_xml(self):
        """Verify single testsuite (without testsuites wrapper) still works"""
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="5" failures="1" errors="0" skipped="0">
    <testcase name="test1" classname="test_module" time="0.5">
        <failure message="AssertionError">Stack trace</failure>
    </testcase>
</testsuite>"""

        result = self.parser.parse_artifact(xml_content, 'xml')

        assert result['total'] == 5
        assert result['failed'] == 1
        assert result['errors'] == 0
        assert result['passed'] == 4
        assert len(result['failures']) == 1

    def test_no_tests_collected_console(self):
        """
        Detect when tests exist but all are deselected

        This should NOT be reported as "0 tests = success"
        """
        console_log = """
============================= test session starts ==============================
platform linux -- Python 3.14.0, pytest-8.0.0
collected 231 items / 231 deselected

=========================== 231 deselected in 0.76s ============================
"""

        result = self.parser.parse_console_output(console_log)

        assert result['total'] == 0
        assert result['evidence_status'] == 'no_tests_collected'
        assert any('deselected' in w.lower() for w in result['warnings'])
        assert '231' in str(result['warnings'][0])

    def test_errors_vs_failures_console(self):
        """Track setup/teardown errors separately from assertion failures"""
        console_log = """
============================= test session starts ==============================
test_module.py::test1 PASSED
test_module.py::test2 FAILED
test_module.py::test3 ERROR

===================== 1 failed, 1 passed, 1 error in 2.5s =====================
"""

        result = self.parser.parse_console_output(console_log)

        assert result['total'] == 3
        assert result['passed'] == 1
        assert result['failed'] == 1
        assert result['errors'] == 1
        assert result['evidence_status'] == 'console'

    def test_errors_in_xml(self):
        """Verify errors (not just failures) are extracted from XML"""
        xml_content = """<?xml version="1.0" encoding="utf-8"?>
<testsuite name="pytest" tests="3" failures="1" errors="1" skipped="0">
    <testcase name="test_fail" classname="test_module" time="0.5">
        <failure message="AssertionError">Assertion failed</failure>
    </testcase>
    <testcase name="test_error" classname="test_module" time="0.3">
        <error message="SetupError">Fixture setup failed</error>
    </testcase>
</testsuite>"""

        result = self.parser.parse_artifact(xml_content, 'xml')

        assert result['total'] == 3
        assert result['failed'] == 1
        assert result['errors'] == 1
        assert result['passed'] == 1

        # Should have 2 failure entries (1 failure + 1 error)
        assert len(result['failures']) == 2
        failure_types = [f['type'] for f in result['failures']]
        assert 'failure' in failure_types
        assert 'error' in failure_types

    def test_console_parsing_with_summary(self):
        """Verify console parsing works when no XML available"""
        console_log = """
============================= test session starts ==============================
test_create.py::test_success PASSED                                      [ 50%]
test_create.py::test_invalid FAILED                                      [100%]

=================================== FAILURES ===================================
_______________________________ test_invalid ___________________________________

    def test_invalid():
>       assert response.status == 200
E       AssertionError: assert 422 == 200

========================= 1 failed, 1 passed in 2.5s ==========================
"""

        result = self.parser.parse_console_output(console_log)

        assert result['total'] == 2
        assert result['passed'] == 1
        assert result['failed'] == 1
        assert result['evidence_status'] == 'console'
        assert 'console_summary' in result['evidence_sources']

    def test_console_parsing_all_passed(self):
        """Test alternate summary format (all passed)"""
        console_log = """
============================= test session starts ==============================
test_smoke.py::test1 PASSED
test_smoke.py::test2 PASSED

=============================== 2 passed in 1.23s ===============================
"""

        result = self.parser.parse_console_output(console_log)

        assert result['total'] == 2
        assert result['passed'] == 2
        assert result['failed'] == 0
        assert result['evidence_status'] == 'console'

    def test_no_console_output(self):
        """Verify inconclusive state when no console output"""
        result = self.parser.parse_console_output('')

        assert result['total'] == 0
        assert result['evidence_status'] == 'inconclusive'
        assert any('no console' in w.lower() for w in result['warnings'])

    def test_no_summary_in_console(self):
        """Partial evidence when console has no pytest summary line"""
        console_log = """
Some Jenkins output
But no pytest summary line
Just random text
"""

        result = self.parser.parse_console_output(console_log)

        assert result['evidence_status'] == 'partial'
        assert any('no pytest summary' in w.lower() for w in result['warnings'])

    def test_complex_summary_with_skipped(self):
        """Test full summary with all components"""
        console_log = """
===================== 5 failed, 10 passed, 3 skipped, 2 errors in 45.2s =====================
"""

        result = self.parser.parse_console_output(console_log)

        assert result['total'] == 20  # 5 + 10 + 3 + 2
        assert result['passed'] == 10
        assert result['failed'] == 5
        assert result['skipped'] == 3
        assert result['errors'] == 2
        assert result['duration'] == '45.2'

    def test_malformed_xml(self):
        """Parser should handle malformed XML gracefully"""
        xml_content = """This is not valid XML <broken>"""

        result = self.parser.parse_artifact(xml_content, 'xml')

        # Should return empty results, not crash
        assert result['total'] == 0
        assert result['passed'] == 0
        assert result['failed'] == 0

    def test_empty_xml(self):
        """Empty XML should return zero results"""
        xml_content = """<?xml version="1.0"?><testsuites></testsuites>"""

        result = self.parser.parse_artifact(xml_content, 'xml')

        assert result['total'] == 0
        assert result['passed'] == 0

    def test_non_xml_artifact_type(self):
        """Non-XML artifact types should return empty results"""
        result = self.parser.parse_artifact("some content", 'json')

        assert result['total'] == 0


if __name__ == '__main__':
    # Allow running directly with pytest
    pytest.main([__file__, '-v'])
