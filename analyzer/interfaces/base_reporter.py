"""
Base interface for report generators

Provides common interface for generating test analysis reports
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime


class BaseReporter(ABC):
    """Abstract base class for report generators"""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize reporter with component configuration

        Args:
            config: Component configuration from component.json
        """
        self.config = config
        self.reporting_config = config.get('reporting', {})
        self.component_name = config.get('name', 'unknown')

    @abstractmethod
    def generate_report(self, analysis_results: Dict[str, Any],
                       output_path: Optional[Path] = None) -> str:
        """
        Generate test analysis report

        Args:
            analysis_results: Complete analysis results
            output_path: Optional path to save report (if None, return as string)

        Returns:
            Generated report content (markdown, HTML, etc.)
        """
        pass

    def get_default_output_path(self, build_number: int,
                                variant: str = 'default') -> Path:
        """
        Get default output path for a report

        Args:
            build_number: Jenkins build number
            variant: Build variant (rhoai, odh, etc.)

        Returns:
            Path object for report file
        """
        reports_dir = Path(self.config.get('REPORT_OUTPUT_DIR', './reports'))
        component_dir = reports_dir / 'by-component' / self.component_name

        # Create directory if it doesn't exist
        component_dir.mkdir(parents=True, exist_ok=True)

        filename = f"build-{build_number}-{variant}.md"
        return component_dir / filename

    def format_summary_section(self, results: Dict[str, Any]) -> str:
        """
        Format test summary section

        Args:
            results: Parsed test results

        Returns:
            Formatted markdown summary
        """
        total = results.get('total', 0)
        passed = results.get('passed', 0)
        failed = results.get('failed', 0)
        skipped = results.get('skipped', 0)

        pass_rate = (passed / total * 100) if total > 0 else 0
        fail_rate = (failed / total * 100) if total > 0 else 0

        return f"""## Test Summary

| Metric | Count | Percentage |
|--------|-------|------------|
| Total Tests | {total} | 100% |
| Passed | {passed} | {pass_rate:.1f}% |
| Failed | {failed} | {fail_rate:.1f}% |
| Skipped | {skipped} | {skipped / total * 100 if total > 0 else 0:.1f}% |
"""

    def format_failure_section(self, failures: List[Dict[str, Any]],
                               max_failures: Optional[int] = None) -> str:
        """
        Format failures section

        Args:
            failures: List of analyzed failures
            max_failures: Maximum number of failures to include (None = all)

        Returns:
            Formatted markdown failures section
        """
        max_display = max_failures or self.reporting_config.get('max_failures_to_display', 50)
        display_failures = failures[:max_display]

        sections = ["## Test Failures\n"]

        for i, failure in enumerate(display_failures, 1):
            test_name = failure.get('test_name', 'Unknown')
            category = failure.get('category', 'unknown')
            severity = failure.get('severity', 'medium')
            error_msg = failure.get('error_message', 'No error message')

            sections.append(f"""### {i}. {test_name}

**Category**: {category}
**Severity**: {severity}
**Is Flaky**: {'Yes' if failure.get('is_flaky', False) else 'No'}

**Error Message**:
```
{error_msg}
```

**Root Cause**:
{failure.get('root_cause', 'Unable to determine')}

**Recommended Actions**:
{self._format_action_list(failure.get('recommended_actions', []))}

**Rerun Command**:
```bash
{failure.get('rerun_command', 'N/A')}
```

---
""")

        if len(failures) > max_display:
            sections.append(f"\n*Showing {max_display} of {len(failures)} failures. See full results for more.*\n")

        return '\n'.join(sections)

    def format_category_breakdown(self, by_category: Dict[str, List[Dict[str, Any]]]) -> str:
        """
        Format failure category breakdown

        Args:
            by_category: Failures grouped by category

        Returns:
            Formatted markdown category breakdown
        """
        sections = ["## Failure Breakdown by Category\n"]

        # Sort categories by failure count (descending)
        sorted_categories = sorted(by_category.items(),
                                  key=lambda x: len(x[1]),
                                  reverse=True)

        for category, category_failures in sorted_categories:
            count = len(category_failures)
            sections.append(f"### {category.replace('_', ' ').title()} ({count})\n")

            # List tests in this category
            for failure in category_failures[:10]:  # Show max 10 per category
                test_name = failure.get('test_name', 'Unknown')
                sections.append(f"- {test_name}")

            if len(category_failures) > 10:
                sections.append(f"- *... and {len(category_failures) - 10} more*\n")

            sections.append("")

        return '\n'.join(sections)

    def format_cluster_health_section(self, cluster_state: Optional[Dict[str, Any]]) -> str:
        """
        Format cluster health section

        Args:
            cluster_state: Cluster health state

        Returns:
            Formatted markdown cluster health section
        """
        if not cluster_state:
            return "## Cluster Health\n\n*Cluster health inspection not performed*\n"

        sections = ["## Cluster Health\n"]

        for namespace, health in cluster_state.items():
            if isinstance(health, dict):
                sections.append(f"### {namespace}\n")
                sections.append(f"- **Total Pods**: {health.get('total_pods', 0)}")
                sections.append(f"- **Pods Ready**: {health.get('pods_ready', 0)}")
                sections.append(f"- **Pods Not Ready**: {health.get('pods_not_ready', 0)}")
                sections.append(f"- **Failed Pods**: {health.get('failed_pods', 0)}")
                sections.append(f"- **Recent Events**: {health.get('recent_events', 0)}\n")

        return '\n'.join(sections)

    def _format_action_list(self, actions: List[str]) -> str:
        """Format list of actions as markdown"""
        if not actions:
            return "*No specific actions recommended*"
        return '\n'.join(f"- {action}" for action in actions)

    def get_report_metadata(self, build_number: int, build_url: str,
                           status: str) -> Dict[str, Any]:
        """
        Generate report metadata

        Args:
            build_number: Jenkins build number
            build_url: Jenkins build URL
            status: Build status

        Returns:
            Metadata dictionary
        """
        return {
            'component_name': self.component_name,
            'framework': self.config.get('framework', 'unknown'),
            'build_number': build_number,
            'build_url': build_url,
            'status': status,
            'analysis_date': datetime.utcnow().isoformat(),
            'generated_by': 'RHOAI Test Analysis Platform'
        }
