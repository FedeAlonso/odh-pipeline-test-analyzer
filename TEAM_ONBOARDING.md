# Component Team Onboarding Guide

**Welcome to the Test Failure Analysis Platform!**

This platform enables RHOAI/ODH component teams to self-onboard and get automated test failure analysis with professional HTML reports.

---

## 🎯 Quick Start

### 1. Onboard Your Component

Run the interactive wizard:

```bash
python3 scripts/onboard_component.py
```

Provide:
- Component name (e.g., `model-registry`, `workbenches`, `kserve`)
- Test framework (`pytest`, `cypress`, `robot`, etc.)
- Jenkins job paths (e.g., `rhoai/3.4/selfmanaged/cli/aws/rhoai-sanity`)
- Artifact patterns to filter your tests (e.g., `**/model-registry*.xml`)
- Repository URL
- Maintainers

**Created files:**
```
.claude/components/{your-component}/{framework}/
├── component.json           # Configuration
├── reporter.py             # HTML reporter (you own this!)
├── skills/
│   └── analyze-failures.md # Custom skill
└── README.md
```

### 2. Run Analysis

```bash
python3 scripts/analyze_component.py {component} {framework} --build {number}
```

**Example:**
```bash
python3 scripts/analyze_component.py model-registry pytest --build 28
```

**Generates:** `reports/by-component/{component}/build-{num}-rhoai.html`

### 3. View Report

Open the HTML report in your browser - features:
- ✅ Dark-themed professional interface
- ✅ Summary cards (total, passed, failed, skipped)
- ✅ Test suite breakdown
- ✅ Detailed failure analysis
- ✅ Cluster health information

---

## 📋 What You Get

### component.json
Your component's configuration:
- **Jenkins jobs**: Which pipelines to monitor
- **Artifact patterns**: Filters to get ONLY your test results from shared jobs
- **Test framework**: pytest, cypress, robot, etc.
- **Namespaces**: Your Kubernetes namespaces
- **Failure categories**: Types of failures you care about

### reporter.py
**YOU OWN THIS FILE!**

Generated at onboarding with current dashboard HTML styling. Fully customizable:

```python
from analyzer.interfaces.base_html_reporter import BaseHTMLReporter

class YourComponentHTMLReporter(BaseHTMLReporter):
    """
    Inherits professional dashboard styling
    Customize as needed for your team
    """
    
    # Override methods to customize:
    
    def _generate_css(self) -> str:
        """Change colors, fonts, styling"""
        css = super()._generate_css()
        css += """
        :root {
          --green: #YOUR_COLOR;  /* Team branding */
          --red: #YOUR_COLOR;
        }
        """
        return css
    
    def generate_html_report(self, results, output_path=None):
        """Add team-specific sections"""
        html = super().generate_html_report(results, output_path)
        
        # Add custom sections (Grafana links, Jira, Slack)
        custom = '''
        <div class="section">
          <h2>📊 Team Links</h2>
          <div class="card">
            <p><a href="https://your-grafana">Metrics</a></p>
            <p><a href="https://jira">JIRA Project</a></p>
            <p>Slack: #your-channel</p>
          </div>
        </div>
        '''
        
        html = html.replace('</div></body>', custom + '</div></body>')
        
        if output_path:
            output_path.write_text(html)
        
        return html
```

**Why you own it:**
- ✅ Dashboard team can evolve their HTML without affecting you
- ✅ You get a snapshot of current professional styling
- ✅ Customize colors, sections, branding as needed
- ✅ Versioned in git with your component

### skills/analyze-failures.md
Custom skill for analyzing your component's failures. Created with context from:
- Your component.json configuration
- Test framework patterns
- Your Jenkins jobs
- Your failure categories

---

## 🔧 Advanced: Custom Pipeline Checks

### Why Create Custom Analyzer?

**Each component has different pipeline infrastructure:**

- **Dashboard**: npm install → cypress binary → operator deployment → tests
- **Model Registry**: Python venv → DB migration → operator → tests  
- **Workbenches**: JupyterHub install → PVC provisioning → notebook spawning → tests
- **KServe**: Knative setup → Istio mesh → InferenceService → tests

**Your pipeline checks are COMPLETELY INDEPENDENT from other components!**

### Create analyzer.py (Optional)

```python
# .claude/components/{your-component}/{framework}/analyzer.py

from analyzer.interfaces.base_analyzer import BaseFailureAnalyzer

class YourComponentAnalyzer(BaseFailureAnalyzer):
    """
    YOUR pipeline-specific failure detection
    
    Completely independent - doesn't affect other components
    """
    
    def categorize_failure(self, failure):
        """Detect YOUR pipeline failures"""
        error = failure['error_message']
        jenkins_job = failure.get('jenkins_job', '')
        
        # YOUR pipeline-specific checks
        if 'your operator failed' in error.lower():
            return 'operator_failure'
        
        if 'your database migration' in error.lower():
            return 'migration_failure'
        
        # Check different pipelines differently
        if 'nightly' in jenkins_job:
            if 'performance regression' in error:
                return 'performance_issue'
        
        return 'test_failure'
    
    def determine_root_cause(self, failure):
        """YOUR root cause analysis logic"""
        category = failure.get('category')
        
        if category == 'operator_failure':
            return "Your operator installation failed. Check operator logs."
        
        if category == 'migration_failure':
            return "Database migration failed. Check schema compatibility."
        
        return "Unknown cause - review error details"
    
    def get_recommended_actions(self, failure):
        """YOUR team's debug commands"""
        category = failure.get('category')
        
        if category == 'operator_failure':
            return [
                "Check operator: oc get pods -n your-namespace",
                "View logs: oc logs -l app=your-operator",
                "Check CRD: oc get your-crd"
            ]
        
        return ["Review error and check logs"]
```

**Reference in component.json:**

```json
{
  "analysis": {
    "analyzer_module": "analyzer.py",
    "failure_categories": [
      "operator_failure",
      "migration_failure",
      "performance_issue",
      "setup_failure",
      "test_failure"
    ]
  }
}
```

**Platform will use YOUR analyzer for YOUR component only!**

---

## 🎨 Multiple Jenkins Pipelines

**You can monitor ANY number of Jenkins jobs:**

```json
{
  "jenkins": {
    "job_paths": [
      "rhoai/your-component/sanity",      // Quick smoke tests
      "rhoai/your-component/nightly",     // Full suite
      "rhoai/your-component/release"      // Release blockers
    ]
  }
}
```

**Access in analyzer to check which pipeline failed:**

```python
def categorize_failure(self, failure):
    jenkins_job = failure.get('jenkins_job', '')
    
    if 'nightly' in jenkins_job:
        # Nightly-specific checks
        pass
    
    if 'release' in jenkins_job:
        # Stricter checks for release pipeline
        pass
```

---

## 📦 Artifact Filtering

**Critical for shared Jenkins jobs!**

If your tests run in a shared Jenkins job (like `rhoai-sanity`), use artifact patterns to get ONLY your tests:

```json
{
  "jenkins": {
    "job_paths": ["rhoai/3.4/selfmanaged/cli/aws/rhoai-sanity"],
    "artifact_patterns": [
      "**/model-registry*.xml",  // Only model-registry results
      "**/ai-hub*.xml"           // And ai-hub results
    ]
  }
}
```

**Platform will:**
1. Fetch ALL artifacts from Jenkins (8+ files)
2. Filter to ONLY files matching your patterns (1-2 files)
3. Parse ONLY your test results
4. Generate report with ONLY your data

**Result:** Shared job, isolated analysis!

---

## 🏗️ Platform Architecture

### What Platform Provides

**Infrastructure you don't need to build:**

- ✅ **Jenkins Integration**: Async HTTP client, authentication, build/artifact fetching
- ✅ **Default Parsers**: pytest (JUnit XML), cypress (JSON), Robot Framework (planned)
- ✅ **Plugin System**: Interfaces for custom parsers, analyzers, reporters
- ✅ **Component Registry**: Auto-discovers components from `.claude/components/`
- ✅ **HTML Reporting**: Professional dashboard-style reports (BaseHTMLReporter)
- ✅ **CLI Tools**: Management commands (list, show, validate, analyze)

### What Teams Provide

**Domain knowledge platform can't have:**

- ✅ **Jenkins job paths**: Which pipelines to monitor
- ✅ **Artifact patterns**: How to filter your tests from shared jobs
- ✅ **Custom analyzer** (optional): YOUR pipeline-specific failure detection
- ✅ **Failure categories**: What failure types matter to YOUR team
- ✅ **Custom reporter** (optional): Team branding, custom sections, links
- ✅ **Skills**: Component-specific debugging knowledge

### Complete Isolation

**Each component is independent:**

```
Dashboard Component:
  ├── Uses: .claude/components/dashboard/cypress/analyzer.py
  ├── Checks: npm failures, cypress binary, dashboard operator
  └── Affects: ONLY dashboard reports

Model Registry Component:
  ├── Uses: .claude/components/model-registry/pytest/analyzer.py
  ├── Checks: DB migrations, PostgreSQL, model-registry operator
  └── Affects: ONLY model-registry reports

Workbenches Component:
  ├── Uses: .claude/components/workbenches/pytest/analyzer.py
  ├── Checks: JupyterHub install, PVC provisioning, notebook spawning
  └── Affects: ONLY workbenches reports
```

**No shared state. No cross-contamination.**

---

## 🔍 Common Questions

### Q: Does my component use dashboard's pipeline checks?

**A: NO!** Each component is completely independent.

Dashboard's analyzer.py only affects dashboard reports. You create your OWN analyzer.py with YOUR pipeline-specific logic.

### Q: Can I point to different Jenkins pipelines?

**A: YES!** Configure ANY Jenkins job paths in component.json:

```json
{
  "jenkins": {
    "job_paths": [
      "any/jenkins/job/path",
      "completely/different/pipeline"
    ]
  }
}
```

### Q: Can I customize the HTML report?

**A: YES!** You OWN reporter.py. Customize:
- Colors and fonts
- Custom sections (team dashboards, Jira links)
- Failure display format
- Anything you want

### Q: What if dashboard changes their HTML?

**A: Doesn't affect you!** You got a snapshot at onboarding time.

If you want new features:
- Regenerate reporter.py (lose customizations)
- Manually copy features you want
- Or keep your version

### Q: What if I have custom test framework?

**A: Create custom parser!**

```python
# .claude/components/{component}/{framework}/parser.py

from analyzer.interfaces.base_parser import BaseTestParser

class YourFrameworkParser(BaseTestParser):
    def parse_artifact(self, artifact_content, artifact_type):
        # YOUR parsing logic
        pass
    
    def extract_failures(self, parsed_data):
        # YOUR failure extraction
        pass
```

Reference in component.json:
```json
{
  "analysis": {
    "parser_module": "parser.py"
  }
}
```

### Q: How do I share this with my team?

**A: Point them to your component directory!**

```
.claude/components/{your-component}/{framework}/
├── README.md              # Team-specific guide
├── component.json         # Configuration
├── reporter.py           # Customize as team
├── analyzer.py           # Pipeline checks
└── skills/
    └── analyze-failures.md
```

Everything is in git. Team members can:
- Customize reporter.py (team branding)
- Add pipeline checks (analyzer.py)
- Update failure categories
- Add custom sections

---

## 📊 Example: Model Registry Onboarding

### 1. Ran Onboarding Wizard

```bash
python3 scripts/onboard_component.py

Component name: model-registry
Framework: pytest
Jenkins job: rhoai/3.4/selfmanaged/cli/aws/rhoai-sanity
Artifact patterns: **/ai-hub*.xml, **/model-registry*.xml
Repository: https://github.com/opendatahub-io/model-registry
```

### 2. Generated Files

**component.json:**
```json
{
  "name": "model-registry",
  "framework": "pytest",
  "jenkins": {
    "job_paths": ["rhoai/3.4/selfmanaged/cli/aws/rhoai-sanity"],
    "artifact_patterns": ["**/ai-hub*.xml", "**/model-registry*.xml"]
  },
  "reporting": {
    "reporter_module": "reporter.py"
  }
}
```

**reporter.py:**
```python
class ModelRegistryHTMLReporter(BaseHTMLReporter):
    """Inherits dashboard styling, ready to customize"""
    pass
```

### 3. Ran Analysis

```bash
python3 scripts/analyze_component.py model-registry pytest --build 28
```

### 4. Results

- ✅ Fetched build from Jenkins (1.4MB console output)
- ✅ Found 8 artifacts, filtered to 1 (ai-hub-xunit_report.xml)
- ✅ Parsed 32 tests (32 passed, 0 failed)
- ✅ Generated HTML report: `reports/by-component/model-registry/build-28-rhoai.html`

**Report shows:**
- Professional dark-themed interface
- Summary: 32 total, 32 passed, 0 failed
- Test suite breakdown table
- "✅ No Failures Detected" section

---

## 🚀 Next Steps

1. **Onboard your component** using the wizard
2. **Run first analysis** on recent Jenkins build
3. **Review HTML report** - share with team
4. **Customize reporter.py** (optional) - add team branding/links
5. **Create analyzer.py** (optional) - add pipeline-specific checks
6. **Commit to git** - your component config is versioned

---

## 🛠️ Management Commands

### List All Components

```bash
python3 scripts/component_cli.py list
```

### Show Component Details

```bash
python3 scripts/component_cli.py show {component} {framework}
```

### Validate Configuration

```bash
python3 scripts/component_cli.py validate {component} {framework}
```

### Discover Components

```bash
python3 scripts/component_cli.py discover
```

### Component Statistics

```bash
python3 scripts/component_cli.py stats
```

---

## 📚 File Structure

```
dashboard-build-analyzer/
├── .claude/
│   └── components/
│       ├── dashboard/cypress/          # Dashboard team
│       │   ├── component.json
│       │   ├── reporter.py
│       │   └── analyzer.py
│       ├── model-registry/pytest/      # Model Registry team
│       │   ├── component.json
│       │   ├── reporter.py
│       │   └── skills/
│       └── {your-component}/{framework}/
│           ├── component.json          # YOUR config
│           ├── reporter.py            # YOUR HTML reporter
│           ├── analyzer.py            # YOUR pipeline checks (optional)
│           ├── parser.py              # YOUR custom parser (optional)
│           └── skills/
│               └── analyze-failures.md
├── analyzer/
│   ├── interfaces/
│   │   ├── base_parser.py            # Parser interface
│   │   ├── base_analyzer.py          # Analyzer interface
│   │   ├── base_reporter.py          # Reporter interface
│   │   └── base_html_reporter.py     # HTML base (you inherit)
│   ├── parsers/
│   │   └── pytest_parser.py          # Default pytest parser
│   └── registry/
│       ├── component_registry.py     # Auto-discovers components
│       └── plugin_loader.py          # Loads your plugins
├── scripts/
│   ├── analyze_component.py          # Run analysis
│   ├── onboard_component.py          # Onboarding wizard
│   └── component_cli.py              # Management CLI
└── reports/
    └── by-component/
        └── {your-component}/
            └── build-{num}-rhoai.html  # Your reports
```

---

## 🎯 Key Principles

1. **Self-Service**: Teams onboard when they want, no platform team bottleneck
2. **Independence**: Each component completely isolated, no shared state
3. **Ownership**: Teams own their reporter, analyzer, parser code
4. **Flexibility**: Point to any Jenkins jobs, customize everything
5. **Platform Agnostic**: Works with any test framework, any pipeline
6. **Professional Output**: Dashboard-quality HTML reports out of the box

---

## 💡 Tips

- **Start simple**: Use default parser, no custom analyzer, default reporter
- **Iterate**: Add custom analyzer when you identify pipeline patterns
- **Customize reporter**: Add team branding, dashboard links after first analysis
- **Use artifact patterns**: Essential for shared Jenkins jobs
- **Version everything**: All config in git, team can collaborate
- **Share reports**: HTML files are standalone, share via email/Slack/Confluence

---

## 🆘 Support

- **Platform Issues**: Create issue in dashboard-build-analyzer repo
- **Questions**: Reach out to dashboard team
- **Component-Specific**: You own your analyzer/reporter, customize as needed

---

**Welcome aboard! 🚀**
