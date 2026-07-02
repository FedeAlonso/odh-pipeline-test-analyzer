"""
Base HTML Reporter - Generate rich HTML reports like dashboard analyzer

This provides the foundation for component teams to generate impressive
HTML reports similar to the dashboard team's output.
"""
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime


class BaseHTMLReporter:
    """Base class for generating rich HTML reports"""

    def __init__(self, component_config: Dict[str, Any]):
        """
        Initialize HTML reporter

        Args:
            component_config: Component configuration from component.json
        """
        self.component_name = component_config.get('name', 'Unknown')
        self.framework = component_config.get('framework', 'Unknown')
        self.config = component_config

    def generate_html_report(
        self,
        analysis_results: Dict[str, Any],
        output_path: Optional[Path] = None
    ) -> str:
        """
        Generate rich HTML report

        Args:
            analysis_results: Analysis results from analyze_component.py
            output_path: Where to save the HTML file

        Returns:
            HTML content as string
        """
        html_parts = []

        # HTML structure
        html_parts.append(self._generate_html_header())
        html_parts.append(self._generate_css())
        html_parts.append('</head><body>')
        html_parts.append(self._generate_page_header(analysis_results))
        html_parts.append(self._generate_summary_cards(analysis_results))
        html_parts.append(self._generate_test_breakdown(analysis_results))
        html_parts.append(self._generate_failures_section(analysis_results))
        html_parts.append(self._generate_cluster_health(analysis_results))
        html_parts.append(self._generate_footer())
        html_parts.append('</body></html>')

        html_content = '\n'.join(html_parts)

        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(html_content)

        return html_content

    def _generate_html_header(self) -> str:
        """Generate HTML header with metadata"""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{self.component_name.title()} Test Report</title>"""

    def _generate_css(self) -> str:
        """Generate CSS styling (based on dashboard's impressive dark theme)"""
        return """<style>
:root {
  --bg: #1a1a2e; --bg2: #16213e; --bg3: #0f3460; --card: #1e2745; --border: #2a3a5c;
  --text: #e0e0e0; --text2: #a0aec0; --green: #48bb78; --red: #fc5c65;
  --yellow: #f6e05e; --blue: #63b3ed; --orange: #ed8936;
  --mono: "SFMono-Regular",Consolas,"Liberation Mono",Menlo,monospace;
}
*,*::before,*::after { box-sizing:border-box; }
body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  background:var(--bg); color:var(--text); line-height:1.6; }
.container { max-width:1100px; margin:0 auto; padding:1rem; }
.header { background:linear-gradient(135deg,var(--bg2),var(--bg3)); padding:2rem 1.5rem;
  border-bottom:2px solid var(--border); text-align:center; }
.header h1 { margin:0 0 .5rem; font-size:1.8rem; }
.header-meta { color:var(--text2); font-size:0.95rem; }
.badge { display:inline-block; padding:4px 14px; border-radius:20px; font-weight:700;
  font-size:0.85rem; margin-left:8px; letter-spacing:.5px; }
.badge-pass { background:var(--green); color:#1a1a2e; }
.badge-fail { background:var(--red); color:#fff; }
.summary-row { display:flex; gap:1rem; flex-wrap:wrap; margin:1rem 0; }
.summary-card { flex:1; min-width:120px; background:var(--card); border:1px solid var(--border);
  border-radius:10px; padding:1rem; text-align:center; transition:transform .15s; }
.summary-card:hover { transform:translateY(-2px); }
.summary-value { font-size:2rem; font-weight:700; }
.summary-label { color:var(--text2); font-size:0.85rem; text-transform:uppercase; letter-spacing:.5px; }
.card-pass-bg { border-color:var(--green); }
.card-pass-bg .summary-value { color:var(--green); }
.card-fail-bg { border-color:var(--red); }
.card-fail-bg .summary-value { color:var(--red); }
.section { margin:2rem 0; }
.section h2 { font-size:1.3rem; border-bottom:1px solid var(--border); padding-bottom:.4rem; }
.card { background:var(--card); border:1px solid var(--border); border-radius:10px;
  padding:1rem 1.2rem; margin:.8rem 0; transition:border-color .15s; }
.card:hover { border-color:var(--blue); }
.card-fail { border-left:4px solid var(--red); }
code { font-family:var(--mono); font-size:0.9em; background:var(--bg2); padding:2px 6px; border-radius:4px; }
.test-name { color:var(--blue); font-weight:600; }
.error-message { color:var(--red); margin:.5rem 0; padding:.5rem; background:var(--bg2); border-radius:6px; }
.rerun-cmd { color:var(--yellow); font-family:var(--mono); font-size:0.85rem; }
.icon-pass { color:var(--green); }
.icon-fail { color:var(--red); }
.footer { margin-top:3rem; padding:1.5rem; text-align:center; color:var(--text2); font-size:0.85rem; border-top:1px solid var(--border); }
table { width:100%; border-collapse:collapse; margin:1rem 0; }
th, td { padding:0.75rem; text-align:left; border-bottom:1px solid var(--border); }
th { background:var(--bg3); color:var(--text); font-weight:600; }
tr:hover { background:var(--bg2); }
.progress-bar { display:flex; height:8px; border-radius:4px; overflow:hidden; margin:.5rem 0; background:var(--bg3); }
.progress-pass { background:var(--green); }
.progress-fail { background:var(--red); }
.progress-skip { background:var(--text2); }
</style>"""

    def _generate_page_header(self, analysis_results: Dict[str, Any]) -> str:
        """Generate page header"""
        metadata = analysis_results.get('metadata', {})
        build_num = metadata.get('build_number', 'N/A')
        status = metadata.get('status', 'UNKNOWN')
        date = metadata.get('analysis_date', 'N/A')

        badge_class = 'badge-pass' if status == 'SUCCESS' else 'badge-fail'

        return f"""<div class="header">
  <h1>{self.component_name.title()} Test Report</h1>
  <div class="header-meta">
    <strong>Build #{build_num}</strong>
    <span class="badge {badge_class}">{status}</span>
    <br>
    <small>Generated: {date}</small>
  </div>
</div>
<div class="container">"""

    def _generate_summary_cards(self, analysis_results: Dict[str, Any]) -> str:
        """Generate summary cards (like dashboard's impressive cards)"""
        test_results = analysis_results.get('test_results', {})
        total = test_results.get('total', 0)
        passed = test_results.get('passed', 0)
        failed = test_results.get('failed', 0)
        skipped = test_results.get('skipped', 0)

        pass_pct = (passed / total * 100) if total > 0 else 0
        fail_pct = (failed / total * 100) if total > 0 else 0

        return f"""<div class="summary-row">
  <div class="summary-card">
    <div class="summary-value">{total}</div>
    <div class="summary-label">Total Tests</div>
  </div>
  <div class="summary-card card-pass-bg">
    <div class="summary-value">{passed}</div>
    <div class="summary-label">Passed ({pass_pct:.1f}%)</div>
  </div>
  <div class="summary-card card-fail-bg">
    <div class="summary-value">{failed}</div>
    <div class="summary-label">Failed ({fail_pct:.1f}%)</div>
  </div>
  <div class="summary-card">
    <div class="summary-value">{skipped}</div>
    <div class="summary-label">Skipped</div>
  </div>
</div>"""

    def _generate_test_breakdown(self, analysis_results: Dict[str, Any]) -> str:
        """Generate test suite breakdown table"""
        test_results = analysis_results.get('test_results', {})
        artifact_results = test_results.get('artifact_results', {})

        if not artifact_results:
            return ""

        rows = []
        for suite_name, suite_data in sorted(artifact_results.items()):
            total = suite_data.get('total', 0)
            passed = suite_data.get('passed', 0)
            failed = suite_data.get('failed', 0)
            skipped = suite_data.get('skipped', 0)

            pass_pct = (passed / total * 100) if total > 0 else 0
            fail_pct = (failed / total * 100) if total > 0 else 0
            skip_pct = (skipped / total * 100) if total > 0 else 0

            rows.append(f"""  <tr>
    <td><strong>{suite_name}</strong></td>
    <td>{total}</td>
    <td class="icon-pass">{passed} ({pass_pct:.0f}%)</td>
    <td class="icon-fail">{failed} ({fail_pct:.0f}%)</td>
    <td>{skipped} ({skip_pct:.0f}%)</td>
  </tr>""")

        table_rows = '\n'.join(rows)

        return f"""<div class="section">
  <h2>Test Suite Breakdown</h2>
  <table>
    <thead>
      <tr>
        <th>Suite</th>
        <th>Total</th>
        <th>Passed</th>
        <th>Failed</th>
        <th>Skipped</th>
      </tr>
    </thead>
    <tbody>
{table_rows}
    </tbody>
  </table>
</div>"""

    def _generate_failures_section(self, analysis_results: Dict[str, Any]) -> str:
        """Generate failures section with details"""
        failures = analysis_results.get('analyzed_failures', [])

        if not failures:
            return """<div class="section">
  <h2>✅ No Failures Detected</h2>
  <div class="card">
    <p>All tests passed successfully!</p>
  </div>
</div>"""

        failure_cards = []
        for i, failure in enumerate(failures, 1):
            test_name = failure.get('test_name', 'Unknown')
            test_file = failure.get('test_file', 'Unknown')
            error_msg = failure.get('error_message', 'No error message')
            category = failure.get('category', 'unknown')
            rerun_cmd = failure.get('rerun_command', 'N/A')

            # Truncate long error messages
            if len(error_msg) > 500:
                error_msg = error_msg[:500] + '...'

            failure_cards.append(f"""  <div class="card card-fail">
    <h3>{i}. <span class="test-name">{test_name}</span></h3>
    <p><strong>File:</strong> <code>{test_file}</code></p>
    <p><strong>Category:</strong> <span class="badge">{category}</span></p>
    <div class="error-message">
      <strong>Error:</strong><br>
      <code>{error_msg}</code>
    </div>
    <p><strong>Rerun:</strong> <code class="rerun-cmd">{rerun_cmd}</code></p>
  </div>""")

        cards_html = '\n'.join(failure_cards)

        return f"""<div class="section">
  <h2>🔴 Failures ({len(failures)})</h2>
{cards_html}
</div>"""

    def _generate_cluster_health(self, analysis_results: Dict[str, Any]) -> str:
        """Generate cluster health section (if available)"""
        cluster_state = analysis_results.get('cluster_state')

        if not cluster_state:
            return ""

        namespace_cards = []
        for namespace, health in cluster_state.items():
            total_pods = health.get('total_pods', 0)
            ready_pods = health.get('pods_ready', 0)
            failed_pods = health.get('failed_pods', 0)

            health_status = "✅" if failed_pods == 0 else "⚠️"

            namespace_cards.append(f"""  <div class="card">
    <h3>{health_status} {namespace}</h3>
    <p><strong>Pods Ready:</strong> {ready_pods}/{total_pods}</p>
    {f'<p class="icon-fail"><strong>Failed Pods:</strong> {failed_pods}</p>' if failed_pods > 0 else ''}
  </div>""")

        cards_html = '\n'.join(namespace_cards)

        return f"""<div class="section">
  <h2>☸️ Cluster Health</h2>
{cards_html}
</div>"""

    def _generate_footer(self) -> str:
        """Generate footer"""
        return f"""<div class="footer">
  <p>Generated by {self.component_name.title()} Test Analysis Platform</p>
  <p>Powered by Claude Code</p>
</div>
</div>"""

    def get_default_output_path(
        self,
        build_number: int,
        variant: str = 'rhoai'
    ) -> Path:
        """Get default output path for HTML report"""
        return Path(f"reports/by-component/{self.component_name}/build-{build_number}-{variant}.html")
