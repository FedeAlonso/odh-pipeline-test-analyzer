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
        html_parts.append(self._generate_evidence_banner(analysis_results))
        html_parts.append(self._generate_summary_cards(analysis_results))
        html_parts.append(self._generate_intelligent_analysis(analysis_results))
        html_parts.append(self._generate_test_breakdown(analysis_results))
        html_parts.append(self._generate_failures_section(analysis_results))
        html_parts.append(self._generate_cluster_health(analysis_results))
        html_parts.append(self._generate_must_gather_section(analysis_results))
        html_parts.append(self._generate_cluster_logs_section(analysis_results))
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
.evidence-banner { margin:1rem 0; padding:1rem 1.5rem; border-radius:10px; border-left:4px solid; }
.evidence-banner.warning { background:rgba(246,224,94,0.1); border-left-color:var(--yellow); }
.evidence-banner.error { background:rgba(252,92,101,0.1); border-left-color:var(--red); }
.evidence-banner.info { background:rgba(99,179,237,0.1); border-left-color:var(--blue); }
.evidence-banner-content { display:flex; gap:1rem; align-items:flex-start; }
.evidence-banner-icon { font-size:1.5rem; }
.evidence-banner-title { font-weight:700; font-size:1.1rem; margin-bottom:0.5rem; }
.evidence-warnings { margin:0.5rem 0; padding-left:1.5rem; }
.evidence-warnings li { margin:0.25rem 0; }
.evidence-sources { color:var(--text2); font-size:0.9rem; margin-top:0.5rem; font-style:italic; }
.recommendations-box { background:rgba(72,187,120,0.1); border:1px solid var(--green); border-radius:10px; padding:1rem; }
.recommendation { margin:0.75rem 0; padding:0.75rem 1rem; background:var(--card); border-left:3px solid var(--green); border-radius:6px; }
.analysis-cluster { margin:1.5rem 0; }
.cluster-header { display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem; }
.cluster-badge { display:inline-block; padding:4px 12px; border-radius:16px; font-weight:700; font-size:0.75rem; color:#fff; letter-spacing:0.5px; }
.cluster-count { margin-left:1rem; color:var(--text2); font-size:0.9rem; }
.cluster-cause { margin:0.75rem 0; padding:0.75rem; background:var(--bg2); border-radius:6px; }
.affected-tests { margin:0.75rem 0; padding:0.5rem; background:var(--bg3); border-radius:6px; font-size:0.9rem; }
.suggestions { margin:0.75rem 0; }
.suggestions ul { margin:0.5rem 0; padding-left:1.5rem; }
.suggestions li { margin:0.25rem 0; }
.github-hints { margin:0.75rem 0; padding:0.75rem; background:var(--bg3); border-radius:6px; font-size:0.85rem; }
.search-hint { display:block; margin:0.25rem 0; padding:0.5rem; background:var(--bg2); border-radius:4px; font-size:0.8rem; overflow-x:auto; }
.text-muted { color:var(--text2); }
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

    def _generate_evidence_banner(self, analysis_results: Dict[str, Any]) -> str:
        """Generate evidence quality warning banner if needed"""
        test_results = analysis_results.get('test_results', {})
        evidence_status = test_results.get('evidence_status', 'complete')
        evidence_sources = test_results.get('evidence_sources', [])
        warnings = test_results.get('warnings', [])

        # No banner needed if evidence is complete
        if evidence_status == 'complete':
            return ''

        # Determine banner style based on status
        if evidence_status == 'inconclusive':
            banner_class = 'error'
            icon = '❌'
            title = 'Inconclusive Evidence'
        elif evidence_status == 'no_tests_collected':
            banner_class = 'warning'
            icon = '⚠️'
            title = 'No Tests Executed'
        elif evidence_status == 'partial':
            banner_class = 'warning'
            icon = '⚠️'
            title = 'Partial Evidence'
        else:
            banner_class = 'info'
            icon = 'ℹ️'
            title = 'Evidence Quality Notice'

        # Build warning list
        warning_items = ''.join([f'<li>{w}</li>' for w in warnings]) if warnings else '<li>Evidence quality reduced</li>'

        # Build sources info
        sources_text = f"Evidence sources: {', '.join(evidence_sources)}" if evidence_sources else "No evidence sources available"

        return f"""
<div class="evidence-banner {banner_class}">
  <div class="evidence-banner-content">
    <div class="evidence-banner-icon">{icon}</div>
    <div>
      <div class="evidence-banner-title">{title}</div>
      <ul class="evidence-warnings">
        {warning_items}
      </ul>
      <div class="evidence-sources">{sources_text}</div>
    </div>
  </div>
</div>
"""

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

    def _generate_intelligent_analysis(self, analysis_results: Dict[str, Any]) -> str:
        """Generate intelligent failure analysis section"""
        analyzed = analysis_results.get('analyzed_failures', {})

        # Check if we have intelligent analysis results (dict with clusters)
        if not isinstance(analyzed, dict) or not analyzed.get('failure_clusters'):
            return ''  # No intelligent analysis available

        clusters = analyzed.get('failure_clusters', [])
        root_causes = analyzed.get('root_causes', [])
        recommendations = analyzed.get('recommendations', [])

        html_parts = []

        # Recommendations section (most important - show first)
        if recommendations:
            html_parts.append('<div class="section">')
            html_parts.append('<h2>💡 Key Recommendations</h2>')
            html_parts.append('<div class="recommendations-box">')
            for rec in recommendations:
                html_parts.append(f'<div class="recommendation">{rec}</div>')
            html_parts.append('</div>')
            html_parts.append('</div>')

        # Failure clusters section
        if root_causes:
            html_parts.append('<div class="section">')
            html_parts.append(f'<h2>🔍 Failure Analysis ({len(root_causes)} clusters identified)</h2>')

            for i, rc in enumerate(root_causes[:10], 1):  # Show top 10 clusters
                cluster_size = rc.get('cluster_size', 1)
                category = rc.get('category', 'unknown')
                probable_cause = rc.get('probable_cause', 'Unknown cause')
                suggestions = rc.get('suggestions', [])
                affected_tests = rc.get('affected_tests', [])

                # Category badge color
                category_colors = {
                    'test_data': 'var(--orange)',
                    'setup': 'var(--red)',
                    'network': 'var(--blue)',
                    'timeout': 'var(--yellow)',
                    'permissions': 'var(--red)',
                    'assertion': 'var(--text2)'
                }
                color = category_colors.get(category, 'var(--text2)')

                html_parts.append(f'''
<div class="card analysis-cluster">
  <div class="cluster-header">
    <div>
      <span class="cluster-badge" style="background:{color}">{category.upper()}</span>
      <span class="cluster-count">{cluster_size} test{'s' if cluster_size > 1 else ''} affected</span>
    </div>
  </div>
  <div class="cluster-cause">
    <strong>Probable Cause:</strong> {probable_cause}
  </div>''')

                # Show affected tests (first 5)
                if affected_tests:
                    tests_display = ', '.join(f'<code>{t}</code>' for t in affected_tests[:5])
                    if len(affected_tests) > 5:
                        tests_display += f' <span class="text-muted">+{len(affected_tests) - 5} more</span>'
                    html_parts.append(f'<div class="affected-tests"><strong>Affected:</strong> {tests_display}</div>')

                # Show suggestions
                if suggestions:
                    html_parts.append('<div class="suggestions"><strong>Suggestions:</strong><ul>')
                    for sug in suggestions[:4]:  # Show first 4 suggestions
                        html_parts.append(f'<li>{sug}</li>')
                    html_parts.append('</ul></div>')

                # GitHub search hint
                github_search = rc.get('github_search', {})
                if github_search and github_search.get('repo'):
                    repo = github_search['repo']
                    test_search = github_search.get('test_file_search', '')
                    error_search = github_search.get('error_search', '')

                    html_parts.append('<div class="github-hints">')
                    html_parts.append('<strong>🔎 Investigation:</strong><br>')
                    html_parts.append(f'<code class="search-hint">gh search code "{test_search.split(":")[1]}" --repo {repo}</code><br>')
                    html_parts.append(f'<code class="search-hint">gh pr list --search "{error_search}" --repo {repo}</code>')
                    html_parts.append('</div>')

                html_parts.append('</div>')

            if len(root_causes) > 10:
                html_parts.append(f'<p class="text-muted">... and {len(root_causes) - 10} more failure clusters</p>')

            html_parts.append('</div>')

        # Must-gather insights
        mg_insights = analyzed.get('must_gather_insights', [])
        if mg_insights:
            html_parts.append('<div class="section">')
            html_parts.append('<h2>🔬 Must-Gather Correlation</h2>')
            html_parts.append('<div class="card">')
            html_parts.append('<ul>')
            for insight in mg_insights:
                html_parts.append(f'<li>{insight}</li>')
            html_parts.append('</ul>')
            html_parts.append('</div>')
            html_parts.append('</div>')

        return '\n'.join(html_parts)

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
        af = analysis_results.get('analyzed_failures', {})
        if isinstance(af, dict):
            failures = af.get('failures', [])
        else:
            failures = af

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

    def _generate_must_gather_section(self, analysis_results: Dict[str, Any]) -> str:
        """Generate must-gather diagnostics section"""
        must_gather = analysis_results.get('must_gather')

        if not must_gather:
            return ''

        html_parts = []
        html_parts.append('<div class="section">')
        html_parts.append('<h2>🔬 Must-Gather Diagnostics</h2>')

        # Show warnings if any
        if must_gather.warnings:
            html_parts.append('<div class="evidence-banner warning">')
            html_parts.append('<div class="evidence-banner-content">')
            html_parts.append('<div class="evidence-banner-icon">⚠️</div>')
            html_parts.append('<div>')
            html_parts.append('<div class="evidence-banner-title">Parsing Warnings</div>')
            html_parts.append('<ul class="evidence-warnings">')
            for warning in must_gather.warnings[:5]:
                html_parts.append(f'<li>{warning}</li>')
            html_parts.append('</ul>')
            html_parts.append('</div></div></div>')

        # Summary card
        total_pod_logs = sum(len(pods) for pods in must_gather.pod_logs.values())
        total_events = sum(len(events) for events in must_gather.events.values())
        total_namespaces = len(must_gather.pod_logs.keys() | must_gather.events.keys())

        # Sanitize archive path - only show filename
        from pathlib import Path
        archive_filename = Path(must_gather.archive_path).name if must_gather.archive_path else 'unknown'

        html_parts.append(f'''<div class="card">
  <p><strong>📦 Archive:</strong> <code>{archive_filename}</code></p>
  <p><strong>🕐 Collection Time:</strong> {must_gather.collection_time or 'Unknown'}</p>
  <p><strong>🌐 Cluster Version:</strong> {must_gather.cluster_version or 'Unknown'}</p>
  <p><strong>📊 Data:</strong> {total_pod_logs} pod logs, {total_events} events across {total_namespaces} namespaces</p>
</div>''')

        # Pod failures
        from analyzer.must_gather_parser import MustGatherParser
        parser = MustGatherParser(must_gather.archive_path)
        pod_failures = parser.get_pod_failures(must_gather)
        failing_pods = parser.get_failing_pods(must_gather)

        if failing_pods:
            html_parts.append('<h3>❌ Failed Pods</h3>')
            for pod in failing_pods[:10]:
                html_parts.append(f'''<div class="card card-fail">
  <p><strong>Pod:</strong> <code>{pod.get('namespace')}/{pod.get('name')}</code></p>
  <p><strong>Phase:</strong> {pod.get('phase')}</p>
  <p><strong>Reason:</strong> {pod.get('reason', 'N/A')}</p>
  <div class="error-message">
    <strong>Message:</strong><br>
    <code>{pod.get('message', 'No message')[:500]}</code>
  </div>
</div>''')

            if len(failing_pods) > 10:
                html_parts.append(f'<p class="text-muted">... and {len(failing_pods) - 10} more failed pods</p>')

        # Log errors
        if pod_failures:
            html_parts.append('<h3>📋 Pod Log Errors</h3>')
            for failure in pod_failures[:15]:
                html_parts.append(f'''<div class="card card-fail">
  <p><strong>Pod:</strong> <code>{failure.get('namespace')}/{failure.get('pod_container')}</code></p>
  <p><strong>Type:</strong> {failure.get('type')}</p>
  <p><strong>Line:</strong> {failure.get('line_number')}</p>
  <div class="error-message">
    <strong>Error:</strong><br>
    <code>{failure.get('message', '')[:300]}</code>
  </div>
  <details>
    <summary>Show context</summary>
    <pre style="background:var(--bg2); padding:1rem; border-radius:6px; overflow-x:auto;">{failure.get('context', '')}</pre>
  </details>
</div>''')

            if len(pod_failures) > 15:
                html_parts.append(f'<p class="text-muted">... and {len(pod_failures) - 15} more log errors</p>')

        # Events
        if must_gather.events:
            html_parts.append('<h3>⚠️ Cluster Events (Warnings/Errors)</h3>')
            event_count = 0
            for namespace, events in must_gather.events.items():
                for event in events[:5]:
                    event_count += 1
                    if event_count > 20:
                        break

                    event_type = event.get('type', 'Unknown')
                    reason = event.get('reason', 'Unknown')
                    message = event.get('message', 'No message')
                    obj_ref = event.get('involvedObject', {})
                    obj_name = f"{obj_ref.get('kind', 'Object')}/{obj_ref.get('name', 'unknown')}"

                    html_parts.append(f'''<div class="card">
  <p><strong>Namespace:</strong> <code>{namespace}</code></p>
  <p><strong>Object:</strong> <code>{obj_name}</code></p>
  <p><strong>Type:</strong> <span class="badge" style="background:var(--{'red' if event_type == 'Error' else 'yellow'})">{event_type}</span> - {reason}</p>
  <p><strong>Message:</strong> {message[:500]}</p>
</div>''')

        html_parts.append('</div>')
        return '\n'.join(html_parts)

    def _generate_cluster_logs_section(self, analysis_results: Dict[str, Any]) -> str:
        """Generate cluster logs section with error extraction"""
        cluster_logs = analysis_results.get('cluster_logs', [])

        if not cluster_logs:
            return ''

        html_parts = []
        html_parts.append('<div class="section">')
        html_parts.append('<h2>📋 Cluster Logs</h2>')

        for log_file in cluster_logs:
            log_path = log_file.get('path', 'unknown')
            log_content = log_file.get('content', '')
            log_size = log_file.get('size', 0)

            # Sanitize path - only show filename, not full artifact path
            from pathlib import Path
            log_filename = Path(log_path).name

            html_parts.append(f'''<div class="card">
  <p><strong>📄 Log File:</strong> <code>{log_filename}</code></p>
  <p><strong>📊 Size:</strong> {log_size:,} bytes</p>
</div>''')

            # Parse JSON-formatted pytest logs
            import json
            import re

            errors = []
            warnings = []
            line_num = 0

            for line in log_content.split('\n'):
                line_num += 1
                if not line.strip():
                    continue

                try:
                    # Try to parse as JSON
                    log_entry = json.loads(line)
                    level = log_entry.get('level', '').upper()
                    event = log_entry.get('event', '')

                    if level in ['ERROR', 'CRITICAL']:
                        errors.append({
                            'line': line_num,
                            'level': level,
                            'event': event,
                            'logger': log_entry.get('logger', 'unknown')
                        })
                    elif level == 'WARNING':
                        warnings.append({
                            'line': line_num,
                            'event': event,
                            'logger': log_entry.get('logger', 'unknown')
                        })
                except json.JSONDecodeError:
                    # Not JSON, check for error patterns
                    if re.search(r'\b(ERROR|FATAL|CRITICAL|Exception|Traceback)\b', line, re.IGNORECASE):
                        errors.append({
                            'line': line_num,
                            'level': 'ERROR',
                            'event': line[:200],
                            'logger': 'raw'
                        })

            # Show errors
            if errors:
                html_parts.append(f'<h3>❌ Errors Found ({len(errors)})</h3>')
                for error in errors[:20]:
                    html_parts.append(f'''<div class="card card-fail">
  <p><strong>Line {error['line']}</strong> [{error['logger']}]</p>
  <div class="error-message">
    <code>{error['event'][:500]}</code>
  </div>
</div>''')

                if len(errors) > 20:
                    html_parts.append(f'<p class="text-muted">... and {len(errors) - 20} more errors</p>')

            # Show warnings summary
            if warnings:
                html_parts.append(f'<h3>⚠️ Warnings ({len(warnings)})</h3>')
                html_parts.append('<div class="card">')
                html_parts.append(f'<p>Found {len(warnings)} warning entries in logs</p>')
                html_parts.append('<details>')
                html_parts.append('<summary>Show first 10 warnings</summary>')
                html_parts.append('<ul>')
                for warning in warnings[:10]:
                    html_parts.append(f"<li>Line {warning['line']}: {warning['event'][:200]}</li>")
                html_parts.append('</ul>')
                html_parts.append('</details>')
                html_parts.append('</div>')

            if not errors and not warnings:
                html_parts.append('<div class="card">')
                html_parts.append('<p>✅ No errors or warnings found in cluster logs</p>')
                html_parts.append('</div>')

        html_parts.append('</div>')
        return '\n'.join(html_parts)

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
