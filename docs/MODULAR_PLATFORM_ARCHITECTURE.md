# Modular Test Analysis Platform Architecture

## Overview

This document describes the architecture for transforming the Dashboard Build Analyzer into a modular, multi-component test analysis platform that can support multiple test frameworks (Cypress, pytest, etc.) across different RHOAI/ODH components.

## Motivation

**Current State:**
- Analyzer is tightly coupled to dashboard Cypress E2E tests
- Hardcoded for a single Jenkins pipeline
- Other teams cannot easily add analysis for their components

**Target State:**
- Multi-component platform supporting dashboard, model-registry, distributed-workloads, etc.
- Framework-agnostic core with pluggable analyzers
- Teams can contribute analysis modules without modifying core code
- Unified reporting across all components

## Reference Architecture

This design draws inspiration from two proven patterns:

### 1. odh-test-context Pattern
- **Component discovery**: Automated scanning of repositories
- **Concurrent analysis**: Process multiple components in parallel
- **Dual output format**: JSON (machine) + Markdown (human)
- **Validation-first**: Test commands are validated before documenting

### 2. architecture-context Pattern
- **Version-aware organization**: Structure by product version
- **Rich context injection**: Feed component metadata to analyzers
- **Aggregation layer**: Individual reports → platform-level summary
- **Visualization generation**: Auto-generate diagrams from data

## Platform Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Orchestration Layer                       │
│  - Multi-pipeline discovery                                  │
│  - Component registry                                        │
│  - Concurrent analysis scheduler                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Framework Core                           │
│  - Jenkins client (pipeline-agnostic)                        │
│  - Cluster inspector (namespace-aware)                       │
│  - Report generator (template-based)                         │
│  - Jira integration (pattern-based search)                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Component Plugins                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  dashboard   │  │ model-reg    │  │ distributed  │      │
│  │  /cypress    │  │  /pytest     │  │ -workloads   │      │
│  │              │  │              │  │  /pytest     │      │
│  │ - component  │  │ - component  │  │ - component  │      │
│  │   .json      │  │   .json      │  │   .json      │      │
│  │ - parser.py  │  │ - parser.py  │  │ - parser.py  │      │
│  │ - skills/    │  │ - skills/    │  │ - skills/    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
dashboard-build-analyzer/
├── .claude/
│   ├── components/                    # Component plugin directory
│   │   ├── dashboard/
│   │   │   ├── cypress/               # Test framework subdirectory
│   │   │   │   ├── component.json     # Component metadata
│   │   │   │   ├── parser.py          # Cypress-specific parser
│   │   │   │   ├── analyzer.py        # Cypress-specific analyzer
│   │   │   │   ├── skills/            # Claude skills for this component
│   │   │   │   │   ├── analyze-cypress-failures.md
│   │   │   │   │   └── rerun-flaky-tests.md
│   │   │   │   └── templates/         # Report templates
│   │   │   │       └── report.md.j2
│   │   │   └── README.md              # Component documentation
│   │   ├── model-registry/
│   │   │   ├── pytest/
│   │   │   │   ├── component.json
│   │   │   │   ├── parser.py
│   │   │   │   ├── analyzer.py
│   │   │   │   └── skills/
│   │   │   └── README.md
│   │   ├── distributed-workloads/
│   │   │   ├── pytest/
│   │   │   │   ├── component.json
│   │   │   │   ├── parser.py
│   │   │   │   └── skills/
│   │   │   └── README.md
│   │   └── COMPONENT_GUIDE.md         # How to add components
│   ├── schemas/
│   │   └── component.schema.json      # JSON schema for component.json
│   └── workflows/
│       └── analyze-all-components.js  # Multi-component workflow
├── analyzer/
│   ├── core/                          # Framework-agnostic core
│   │   ├── jenkins_client.py
│   │   ├── cluster_inspector.py
│   │   ├── jira_client.py
│   │   └── report_generator.py
│   ├── registry/                      # Component discovery
│   │   ├── component_registry.py      # Discovers and loads components
│   │   ├── plugin_loader.py           # Loads component modules
│   │   └── validator.py               # Validates component configs
│   └── interfaces/                    # Plugin interfaces
│       ├── base_parser.py             # ABC for test parsers
│       ├── base_analyzer.py           # ABC for failure analyzers
│       └── base_reporter.py           # ABC for report generators
├── scripts/
│   ├── analyze_component.py           # Single component analysis
│   ├── analyze_pipeline.py            # Multi-component pipeline
│   └── discover_components.py         # Component discovery tool
└── reports/
    ├── by-component/                  # Per-component reports
    │   ├── dashboard/
    │   ├── model-registry/
    │   └── distributed-workloads/
    └── platform/                      # Aggregated reports
        └── latest-platform-health.md
```

## Component Configuration Schema

Each component defines its metadata in `component.json`:

```json
{
  "name": "dashboard",
  "framework": "cypress",
  "version": "1.0.0",
  "description": "ODH Dashboard E2E tests using Cypress",
  "maintainers": ["@dashboard-team"],
  
  "jenkins": {
    "job_paths": [
      "cypress/dashboard-tests",
      "devops/rhoai-test-flow"
    ],
    "job_descriptions": [
      "dash-e2e-rhoai",
      "dash-e2e-odh"
    ],
    "artifact_patterns": [
      "**/mochawesome*.json",
      "**/screenshots/**/*.png"
    ]
  },
  
  "test_framework": {
    "type": "cypress",
    "version": "^13.0.0",
    "test_directory": "frontend/src/__tests__/cypress/cypress/tests/e2e/",
    "test_file_pattern": "*.cy.ts",
    "runner_command": "npm run cy:run:safe",
    "rerun_command": "npm run cy:run:safe -- --spec '{spec_path}'"
  },
  
  "cluster": {
    "namespaces": [
      "redhat-ods-applications",
      "redhat-ods-monitoring",
      "rhods-notebooks"
    ],
    "resource_types": [
      "pods",
      "deployments",
      "services"
    ]
  },
  
  "analysis": {
    "parser_module": "parser.py",
    "analyzer_module": "analyzer.py",
    "failure_categories": [
      "timeout",
      "assertion",
      "element_not_found",
      "network",
      "auth"
    ]
  },
  
  "jira": {
    "projects": ["RHOAIENG", "RHODS"],
    "components": ["Dashboard"],
    "search_patterns": "jira_patterns.py"
  },
  
  "reporting": {
    "template": "templates/report.md.j2",
    "include_screenshots": true,
    "include_cluster_health": true
  }
}
```

## Plugin Interface

### Base Parser Interface

```python
from abc import ABC, abstractmethod
from typing import Dict, List, Any

class BaseTestParser(ABC):
    """Abstract base class for test framework parsers"""
    
    @abstractmethod
    def parse_console_output(self, console_log: str) -> Dict[str, Any]:
        """Parse test results from console output"""
        pass
    
    @abstractmethod
    def parse_artifact(self, artifact_content: str, artifact_type: str) -> Dict[str, Any]:
        """Parse test results from artifact files"""
        pass
    
    @abstractmethod
    def extract_failures(self, parsed_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract individual test failures"""
        pass
    
    @abstractmethod
    def get_rerun_command(self, failure: Dict[str, Any]) -> str:
        """Generate command to rerun a specific test"""
        pass
```

### Base Analyzer Interface

```python
from abc import ABC, abstractmethod
from typing import Dict, List, Any

class BaseFailureAnalyzer(ABC):
    """Abstract base class for test failure analyzers"""
    
    @abstractmethod
    def categorize_failure(self, failure: Dict[str, Any]) -> str:
        """Categorize a test failure"""
        pass
    
    @abstractmethod
    def determine_root_cause(self, failure: Dict[str, Any], 
                            cluster_state: Dict[str, Any]) -> str:
        """Determine likely root cause of failure"""
        pass
    
    @abstractmethod
    def get_recommended_actions(self, failure: Dict[str, Any]) -> List[str]:
        """Get recommended debugging actions"""
        pass
```

## Component Registry System

The registry discovers and loads components dynamically:

```python
class ComponentRegistry:
    """Manages component discovery and loading"""
    
    def discover_components(self, base_path: Path = Path('.claude/components')):
        """Scan directory for component plugins"""
        
    def load_component(self, component_name: str, framework: str):
        """Load a specific component's modules"""
        
    def validate_component(self, component_config: Dict[str, Any]):
        """Validate component configuration against schema"""
        
    def list_components(self) -> List[Dict[str, Any]]:
        """List all discovered components"""
        
    def get_components_for_pipeline(self, jenkins_job_path: str) -> List[str]:
        """Find components matching a Jenkins job"""
```

## Multi-Pipeline Analysis Flow

```
1. Pipeline Discovery
   ├─ Scan Jenkins for recent builds
   ├─ Identify job paths (from image config)
   └─ Match to component registry

2. Parallel Component Analysis
   ├─ Load component config
   ├─ Instantiate framework-specific parser
   ├─ Parse test results
   ├─ Analyze failures
   ├─ Inspect cluster state
   └─ Generate component report

3. Aggregation
   ├─ Collect all component reports
   ├─ Cross-component correlation
   ├─ Platform-level health summary
   └─ Generate unified report
```

## Team Contribution Model

### Adding a New Component

1. **Create component directory structure**
   ```bash
   mkdir -p .claude/components/my-component/pytest
   ```

2. **Define component.json**
   - Specify Jenkins job paths
   - Define test framework details
   - Configure cluster resources to inspect

3. **Implement parser.py**
   - Extend `BaseTestParser`
   - Implement framework-specific parsing logic

4. **Implement analyzer.py**
   - Extend `BaseFailureAnalyzer`
   - Define failure categorization rules

5. **Add Claude skills** (optional)
   - Create skills for common analysis tasks
   - Add team-specific debugging workflows

6. **Document**
   - Add README.md with component details
   - Include examples and usage

### Validation

```bash
# Validate component configuration
python scripts/validate_component.py my-component/pytest

# Test component parser
python scripts/test_parser.py my-component/pytest --build 1234

# Dry run analysis
python scripts/analyze_component.py my-component/pytest --build 1234 --dry-run
```

## Pipeline Intelligence

The platform automatically detects which components are tested in a pipeline:

```python
# From nightly_autotrigger_smoke.groovy or COMPONENTS_TESTS_CONFIG
components = [
    'ai-pipelines',
    'codeflare-sdk',
    'customer-workflows',
    'distributed-workloads',
    'kueray',
    'llama_stack',
    # ... etc
]

# Platform maps these to .claude/components/ plugins
for component in components:
    if registry.has_component(component):
        analyzer = registry.load_component(component)
        results = analyzer.analyze(jenkins_build)
```

## Version Management

Track analysis across product versions:

```
reports/
├── by-version/
│   ├── rhoai-2.17/
│   │   ├── dashboard/
│   │   ├── model-registry/
│   │   └── platform-summary.md
│   └── rhoai-2.18/
│       ├── dashboard/
│       └── platform-summary.md
└── latest/  # Symlinks to most recent
```

## Integration with Existing Systems

### Jenkins
- Reuse existing `jenkins_client.py`
- Extend to support multi-job queries
- Add job-to-component mapping

### Jira
- Component-specific search patterns
- Cross-component issue correlation
- Automated issue creation per component

### Cluster Inspector
- Namespace-aware inspection
- Component-specific resource queries
- Multi-cluster support

## Migration Strategy

### Phase 1: Foundation (Current Sprint)
- ✅ Create directory structure
- ✅ Define component schema
- ✅ Implement registry system
- ✅ Create base interfaces
- ✅ Document contribution guide

### Phase 2: Dashboard Migration
- Refactor dashboard analyzer into plugin
- Create dashboard/cypress component
- Validate backward compatibility
- Migrate existing skills

### Phase 3: Platform Expansion
- Add model-registry/pytest component
- Add distributed-workloads/pytest component
- Implement aggregation layer
- Cross-component correlation

### Phase 4: Advanced Features
- Multi-version tracking
- Trend analysis across components
- Automated skill generation
- Platform health dashboard

## Benefits

### For Platform Team
- Unified analysis across all components
- Reduced maintenance burden
- Scalable architecture
- Clear separation of concerns

### For Component Teams
- Self-service test analysis
- Team-specific customization
- Preserved domain knowledge in skills
- Automated failure triage

### For QE/DevOps
- Single source of truth for test health
- Cross-component insights
- Automated reporting
- Actionable recommendations

## Next Steps

1. **Review this architecture** with stakeholders
2. **Create component schema** and validation
3. **Implement registry system**
4. **Refactor dashboard analyzer** as first plugin
5. **Document contribution process**
6. **Onboard first external team** (model-registry?)

## References

- [odh-test-context](https://github.com/jctanner/odh-test-context) - Multi-repo test discovery
- [architecture-context](https://github.com/opendatahub-io/architecture-context) - Component documentation automation
- [RHOAIENG-66134](https://redhat.atlassian.net/browse/RHOAIENG-66134) - Original requirement
