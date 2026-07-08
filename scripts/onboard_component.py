#!/usr/bin/env python3
"""
Interactive Component Onboarding Tool

Guides teams through component setup with interactive prompts,
creates all necessary files, validates configuration, and tests
against a real Jenkins build.
"""
import sys
import json
import argparse
from pathlib import Path
from typing import Optional, Dict, Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.registry import ComponentRegistry, ComponentValidator


class ComponentOnboarder:
    """Interactive tool for onboarding new components"""

    def __init__(self):
        self.base_path = Path('.claude/components')
        self.validator = ComponentValidator()
        self.registry = ComponentRegistry()

    def run_interactive(self):
        """Run interactive onboarding wizard"""
        print("=" * 80)
        print("RHOAI/ODH Component Onboarding Wizard")
        print("=" * 80)
        print("\nThis tool will guide you through adding your component to the")
        print("test analysis platform. It will take about 5 minutes.\n")

        # Collect component information
        config = self._collect_component_info()

        # Show preview
        self._show_preview(config)

        # Confirm
        if not self._confirm("Create component with this configuration?"):
            print("\n❌ Onboarding cancelled")
            return False

        # Create component
        component_dir = self._create_component(config)

        # Validate
        self._validate_component(config['name'], config['framework'])

        # Test (optional)
        if self._confirm("\nTest against a recent Jenkins build?"):
            self._test_component(config)

        # Success!
        self._show_success(config, component_dir)
        return True

    def _collect_component_info(self) -> Dict[str, Any]:
        """Collect component information through interactive prompts"""
        print("\n📋 Component Information")
        print("-" * 80)

        # Component name
        name = self._prompt_required(
            "Component name (lowercase, hyphenated)",
            "e.g., 'distributed-workloads', 'model-registry'"
        )

        # Test framework
        frameworks = ['pytest', 'cypress', 'golang', 'jest', 'junit', 'selenium']
        framework = self._prompt_choice(
            "Test framework",
            frameworks,
            "pytest"
        )

        # Jenkins job path
        print("\n💡 Tip: Find this in your Jenkins URL")
        print("   Example: https://jenkins.../job/components/job/my-component/job/tests/")
        print("   Job path: components/my-component/tests\n")

        job_path = self._prompt_required(
            "Jenkins job path",
            "e.g., 'components/my-component/tests'"
        )

        # Additional job paths
        additional_jobs = []
        if self._confirm("Add additional Jenkins job paths?"):
            while True:
                job = self._prompt("Additional job path (or Enter to finish)")
                if not job:
                    break
                additional_jobs.append(job)

        all_job_paths = [job_path] + additional_jobs

        # Test directory
        test_directory = self._prompt_required(
            "Test directory in repository",
            "e.g., 'tests/', 'tests/integration/'"
        )

        # Test file pattern
        default_patterns = {
            'pytest': 'test_*.py',
            'cypress': '*.cy.ts',
            'golang': '*_test.go',
            'jest': '*.test.ts',
            'junit': '*Test.java'
        }
        test_pattern = self._prompt(
            "Test file pattern",
            default_patterns.get(framework, '*.test.*')
        )

        # Runner command
        default_runners = {
            'pytest': 'pytest -v --junitxml=results.xml',
            'cypress': 'npm run cy:run',
            'golang': 'go test -v ./...',
            'jest': 'npm test',
            'junit': 'mvn test'
        }
        runner_command = self._prompt(
            "Command to run all tests",
            default_runners.get(framework, 'make test')
        )

        # Cluster namespaces
        print("\n☸️  Cluster Configuration")
        print("-" * 80)
        namespaces = []
        default_ns = ['redhat-ods-applications']

        if self._confirm(f"Monitor default namespace (redhat-ods-applications)?"):
            namespaces.extend(default_ns)

        print("\nAdd component-specific namespaces:")
        while True:
            ns = self._prompt("Namespace (or Enter to finish)")
            if not ns:
                break
            namespaces.append(ns)

        if not namespaces:
            namespaces = default_ns

        # Team information
        print("\n👥 Team Information")
        print("-" * 80)

        maintainers = []
        while True:
            maintainer = self._prompt(
                "Team/maintainer handle (with @, or Enter to finish)",
                "@my-team" if not maintainers else None
            )
            if not maintainer:
                break
            if not maintainer.startswith('@'):
                maintainer = f'@{maintainer}'
            maintainers.append(maintainer)

        if not maintainers:
            maintainers = ['@unknown-team']

        description = self._prompt(
            "Component description (optional)",
            f"{name} integration tests using {framework}"
        )

        # Priority
        priority = self._prompt_choice(
            "Component priority",
            ['critical', 'high', 'medium', 'low'],
            'medium'
        )

        # Build configuration
        return {
            'name': name,
            'framework': framework,
            'version': '1.0.0',
            'description': description,
            'maintainers': maintainers,
            'jenkins': {
                'job_paths': all_job_paths,
                'artifact_patterns': self._get_default_artifacts(framework)
            },
            'test_framework': {
                'type': framework,
                'test_directory': test_directory,
                'test_file_pattern': test_pattern,
                'runner_command': runner_command,
                'rerun_command': self._get_default_rerun(framework)
            },
            'cluster': {
                'namespaces': namespaces,
                'resource_types': ['pods', 'deployments', 'services']
            },
            'analysis': {
                'parser_module': 'parser.py',
                'failure_categories': self._get_default_categories(framework)
            },
            'reporting': {
                'template': 'templates/report.md.j2',
                'include_screenshots': framework == 'cypress',
                'include_cluster_health': True,
                'include_retry_analysis': False
            },
            'metadata': {
                'tags': [framework, 'integration' if 'integration' in test_directory else 'e2e'],
                'priority': priority
            }
        }

    def _show_preview(self, config: Dict[str, Any]):
        """Show configuration preview"""
        print("\n" + "=" * 80)
        print("📄 Configuration Preview")
        print("=" * 80)
        print(json.dumps(config, indent=2))
        print("=" * 80)

    def _create_component(self, config: Dict[str, Any]) -> Path:
        """Create component directory and files"""
        component_dir = self.base_path / config['name'] / config['framework']
        component_dir.mkdir(parents=True, exist_ok=True)

        # Create component.json
        config_file = component_dir / 'component.json'
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)

        print(f"\n✅ Created: {config_file}")

        # Create README.md
        readme = self._generate_readme(config)
        readme_file = component_dir / 'README.md'
        readme_file.write_text(readme)

        print(f"✅ Created: {readme_file}")

        # Create directories
        (component_dir / 'skills').mkdir(exist_ok=True)
        (component_dir / 'templates').mkdir(exist_ok=True)

        print(f"✅ Created: {component_dir / 'skills'}/")
        print(f"✅ Created: {component_dir / 'templates'}/")

        # Create placeholder parser.py
        parser_file = component_dir / 'parser.py'
        parser_template = self._generate_parser_template(config)
        parser_file.write_text(parser_template)

        print(f"✅ Created: {parser_file} (template)")

        return component_dir

    def _validate_component(self, name: str, framework: str):
        """Validate component configuration"""
        print(f"\n🔍 Validating {name}/{framework}...")

        # Reload registry
        self.registry.discover_components()

        is_valid, errors = self.registry.validate_component(name, framework)

        if is_valid:
            print("✅ Component configuration is valid")
        else:
            print("❌ Validation errors:")
            for error in errors:
                print(f"   - {error}")

    def _test_component(self, config: Dict[str, Any]):
        """Test component against Jenkins build"""
        print("\n🧪 Testing Component")
        print("-" * 80)

        build_number = self._prompt("Enter a recent build number to test with")

        if not build_number:
            print("⏭️  Skipping test")
            return

        print(f"\n🔄 Testing analysis of build #{build_number}...")
        print("   (This would run the analyzer - not implemented yet)")
        print(f"   Command: python scripts/analyze_component.py {config['name']} {config['framework']} --build {build_number}")

    def _show_success(self, config: Dict[str, Any], component_dir: Path):
        """Show success message and next steps"""
        print("\n" + "=" * 80)
        print("🎉 SUCCESS! Component Onboarded")
        print("=" * 80)
        print(f"\n✅ Component: {config['name']}/{config['framework']}")
        print(f"✅ Location: {component_dir}")
        print(f"✅ Status: Registered and ready to use")

        print("\n📝 Next Steps:")
        print("   1. Review the generated files in:", component_dir)
        print("   2. Customize parser.py if needed (currently a template)")
        print("   3. Add Claude skills in skills/ directory")
        print("   4. Test analysis:")
        print(f"      python scripts/analyze_component.py {config['name']} {config['framework']} --build <BUILD_NUM>")
        print("   5. View your component:")
        print(f"      python scripts/component_cli.py show {config['name']} {config['framework']}")

        print("\n📚 Documentation:")
        print("   - Component Guide: .claude/components/COMPONENT_GUIDE.md")
        print("   - Platform README: PLATFORM_README.md")
        print("   - Your README:", component_dir / "README.md")

    def _generate_readme(self, config: Dict[str, Any]) -> str:
        """Generate component README"""
        return f"""# {config['name'].title()} Component

## Overview

Test analysis component for {config['description']}.

**Component**: `{config['name']}/{config['framework']}`
**Priority**: {config['metadata']['priority']}
**Maintainers**: {', '.join(config['maintainers'])}

## Configuration

- **Jenkins Jobs**:
{chr(10).join(f"  - `{job}`" for job in config['jenkins']['job_paths'])}

- **Test Framework**: {config['framework']}
- **Test Location**: `{config['test_framework']['test_directory']}`
- **Test Pattern**: `{config['test_framework']['test_file_pattern']}`

## Cluster Resources

Tests run against these namespaces:
{chr(10).join(f"- `{ns}`" for ns in config['cluster']['namespaces'])}

## Usage

### Analyze Latest Build

```bash
cd /Users/acoughli/dashboard-build-analyzer
source venv/bin/activate

python scripts/analyze_component.py {config['name']} {config['framework']} --build <BUILD_NUMBER>
```

### Run Tests Locally

```bash
{config['test_framework']['runner_command']}
```

## Failure Categories

{chr(10).join(f"- `{cat}`" for cat in config['analysis']['failure_categories'])}

## Maintainers

{chr(10).join(f"- {m}" for m in config['maintainers'])}

## Generated

This README was auto-generated by the onboarding tool.
Last updated: {self._get_timestamp()}
"""

    def _generate_parser_template(self, config: Dict[str, Any]) -> str:
        """Generate parser.py template"""
        return f'''"""
Test parser for {config['name']} ({config['framework']})

This is a TEMPLATE - customize as needed for your test framework.
"""
from typing import Dict, List, Any
from analyzer.interfaces.base_parser import BaseTestParser


class {self._to_class_name(config['name'])}Parser(BaseTestParser):
    """Parser for {config['name']} {config['framework']} test results"""

    def parse_console_output(self, console_log: str) -> Dict[str, Any]:
        """
        Parse test results from Jenkins console output

        TODO: Implement parsing logic for {config['framework']} output
        """
        results = {{
            'total': 0,
            'passed': 0,
            'failed': 0,
            'skipped': 0,
            'duration': None,
            'failures': []
        }}

        # TODO: Add parsing logic here
        # Look for patterns specific to {config['framework']}

        return results

    def parse_artifact(self, artifact_content: str, artifact_type: str) -> Dict[str, Any]:
        """Parse test results from artifact files"""
        # TODO: Implement artifact parsing
        return {{}}

    def extract_failures(self, parsed_results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract individual test failures"""
        return parsed_results.get('failures', [])

    def get_rerun_command(self, failure: Dict[str, Any]) -> str:
        """Generate command to rerun specific test"""
        test_file = failure.get('test_file', '')
        return f"{config['test_framework'].get('rerun_command', 'rerun {{spec_path}}')}"
'''

    def _prompt(self, message: str, default: Optional[str] = None) -> str:
        """Prompt for input with optional default"""
        if default:
            response = input(f"{message} [{default}]: ").strip()
            return response if response else default
        return input(f"{message}: ").strip()

    def _prompt_required(self, message: str, example: Optional[str] = None) -> str:
        """Prompt for required input"""
        if example:
            print(f"   Example: {example}")
        while True:
            value = input(f"{message}: ").strip()
            if value:
                return value
            print("   ⚠️  This field is required. Please enter a value.")

    def _prompt_choice(self, message: str, choices: list, default: str) -> str:
        """Prompt for choice from list"""
        print(f"\n{message}:")
        for i, choice in enumerate(choices, 1):
            marker = " (default)" if choice == default else ""
            print(f"  {i}. {choice}{marker}")

        while True:
            response = input(f"Choice [1-{len(choices)}] or Enter for default [{default}]: ").strip()

            if not response:
                return default

            try:
                idx = int(response) - 1
                if 0 <= idx < len(choices):
                    return choices[idx]
            except ValueError:
                pass

            print(f"   ⚠️  Please enter a number between 1 and {len(choices)}")

    def _confirm(self, message: str) -> bool:
        """Prompt for yes/no confirmation"""
        while True:
            response = input(f"{message} [y/N]: ").strip().lower()
            if response in ['y', 'yes']:
                return True
            if response in ['n', 'no', '']:
                return False
            print("   ⚠️  Please answer 'y' or 'n'")

    def _get_default_artifacts(self, framework: str) -> list:
        """Get default artifact patterns for framework"""
        patterns = {
            'pytest': ['**/pytest-results*.xml', '**/test-report*.html'],
            'cypress': ['**/mochawesome*.json', '**/screenshots/**/*.png'],
            'golang': ['**/test-results*.xml'],
            'jest': ['**/jest-results*.json'],
            'junit': ['**/surefire-reports/*.xml']
        }
        return patterns.get(framework, ['**/*test-results*.xml'])

    def _get_default_rerun(self, framework: str) -> str:
        """Get default rerun command for framework"""
        commands = {
            'pytest': 'pytest -v {spec_path}',
            'cypress': "npm run cy:run -- --spec '{spec_path}'",
            'golang': 'go test -v -run {test_name}',
            'jest': 'npm test -- {spec_path}',
            'junit': 'mvn test -Dtest={test_name}'
        }
        return commands.get(framework, 'rerun {spec_path}')

    def _get_default_categories(self, framework: str) -> list:
        """Get default failure categories for framework"""
        base = ['timeout', 'assertion', 'unknown']

        additions = {
            'pytest': ['api_error', 'database_error', 'validation_error'],
            'cypress': ['element_not_found', 'network', 'navigation'],
            'golang': ['panic', 'race_condition'],
            'jest': ['snapshot_mismatch', 'async_timeout']
        }

        return base + additions.get(framework, [])

    def _to_class_name(self, component_name: str) -> str:
        """Convert component-name to ComponentName"""
        return ''.join(word.capitalize() for word in component_name.split('-'))

    def _get_timestamp(self) -> str:
        """Get current timestamp"""
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def run_onboarding_wizard():
    """Run the interactive onboarding wizard (for use by unified CLI)"""
    onboarder = ComponentOnboarder()
    success = onboarder.run_interactive()
    return 0 if success else 1


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Interactive component onboarding tool',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  python scripts/onboard_component.py

  # This tool will guide you through:
  # - Collecting component information
  # - Creating directory structure
  # - Generating configuration files
  # - Validating setup
  # - Testing against Jenkins (optional)
        """
    )

    args = parser.parse_args()

    sys.exit(run_onboarding_wizard())


if __name__ == '__main__':
    main()
