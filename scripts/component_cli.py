#!/usr/bin/env python3
"""
Component Registry CLI

Command-line interface for managing and querying test analysis components
"""
import sys
import argparse
import json
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzer.registry import ComponentRegistry


def list_components(registry: ComponentRegistry, output_format: str = 'table'):
    """List all discovered components"""
    components = registry.list_components()

    if not components:
        print("No components found. Have you created any component.json files in .claude/components/?")
        return

    if output_format == 'json':
        print(json.dumps(components, indent=2))
        return

    # Table format
    print(f"\n{'=' * 100}")
    print(f"{'Component':<30} {'Framework':<15} {'Priority':<12} {'Jobs':<25}")
    print(f"{'=' * 100}")

    for comp in components:
        jobs = ', '.join(comp['jenkins_jobs'][:2])  # Show first 2 jobs
        if len(comp['jenkins_jobs']) > 2:
            jobs += f" (+{len(comp['jenkins_jobs']) - 2} more)"

        print(f"{comp['name']:<30} {comp['framework']:<15} {comp['priority']:<12} {jobs:<25}")

    print(f"{'=' * 100}")
    print(f"\nTotal: {len(components)} components\n")


def show_component(registry: ComponentRegistry, component_name: str, framework: str):
    """Show detailed information about a component"""
    config = registry.get_component(component_name, framework)

    if not config:
        print(f"Component not found: {component_name}/{framework}")
        return

    print(f"\n{'=' * 80}")
    print(f"Component: {config['name']}/{config['framework']}")
    print(f"{'=' * 80}\n")

    print(f"Version: {config['version']}")
    print(f"Description: {config.get('description', 'N/A')}")
    print(f"Maintainers: {', '.join(config.get('maintainers', []))}")

    print(f"\nJenkins Jobs:")
    for job in config['jenkins']['job_paths']:
        print(f"  - {job}")

    print(f"\nTest Framework:")
    tf = config['test_framework']
    print(f"  Type: {tf['type']}")
    print(f"  Directory: {tf['test_directory']}")
    print(f"  Pattern: {tf.get('test_file_pattern', 'N/A')}")

    print(f"\nCluster:")
    cluster = config.get('cluster', {})
    print(f"  Namespaces: {', '.join(cluster.get('namespaces', []))}")

    print(f"\nAnalysis:")
    analysis = config['analysis']
    print(f"  Parser: {analysis.get('parser_module', 'N/A')}")
    print(f"  Analyzer: {analysis.get('analyzer_module', 'N/A')}")
    print(f"  Categories: {', '.join(analysis.get('failure_categories', []))}")

    print(f"\n{'=' * 80}\n")


def validate_component(registry: ComponentRegistry, component_name: str, framework: str):
    """Validate a component configuration"""
    print(f"\nValidating {component_name}/{framework}...")

    is_valid, errors = registry.validate_component(component_name, framework)

    if is_valid:
        print("✅ Component configuration is valid")

        # Check if modules exist
        config = registry.get_component(component_name, framework)
        component_dir = config.get('_component_dir')

        parser_module = config['analysis'].get('parser_module')
        if parser_module:
            parser_path = Path(component_dir) / parser_module
            if parser_path.exists():
                print(f"✅ Parser module exists: {parser_module}")
            else:
                print(f"⚠️  Parser module not found: {parser_module}")

        analyzer_module = config['analysis'].get('analyzer_module')
        if analyzer_module:
            analyzer_path = Path(component_dir) / analyzer_module
            if analyzer_path.exists():
                print(f"✅ Analyzer module exists: {analyzer_module}")
            else:
                print(f"⚠️  Analyzer module not found: {analyzer_module}")

    else:
        print("❌ Component configuration is invalid:")
        for error in errors:
            print(f"   - {error}")

    print()


def find_components_for_job(registry: ComponentRegistry, job_path: str):
    """Find components that analyze a specific Jenkins job"""
    components = registry.get_components_for_job(job_path)

    if not components:
        print(f"No components found for job: {job_path}")
        return

    print(f"\nComponents analyzing '{job_path}':")
    for comp_key in components:
        print(f"  - {comp_key}")
    print()


def show_stats(registry: ComponentRegistry):
    """Show registry statistics"""
    stats = registry.get_stats()

    print(f"\n{'=' * 60}")
    print("Component Registry Statistics")
    print(f"{'=' * 60}\n")

    print(f"Total Components: {stats['total_components']}")
    print(f"Frameworks: {', '.join(stats['frameworks'])}")
    print(f"Jenkins Jobs Tracked: {stats['jenkins_jobs_tracked']}")

    print(f"\nBy Priority:")
    for priority, count in stats['by_priority'].items():
        print(f"  {priority.title()}: {count}")

    print(f"\n{'=' * 60}\n")


def discover_and_refresh(registry: ComponentRegistry):
    """Rediscover all components"""
    print("\nDiscovering components...")
    discovered = registry.discover_components()

    print(f"✅ Discovered {len(discovered)} components:")
    for comp_key in discovered:
        print(f"   - {comp_key}")
    print()


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Manage and query test analysis components',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all components
  python scripts/component_cli.py list

  # Show component details
  python scripts/component_cli.py show dashboard cypress

  # Validate component configuration
  python scripts/component_cli.py validate dashboard cypress

  # Find components for a Jenkins job
  python scripts/component_cli.py find-job cypress/dashboard-tests

  # Show registry statistics
  python scripts/component_cli.py stats

  # Rediscover components
  python scripts/component_cli.py discover
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # List command
    list_parser = subparsers.add_parser('list', help='List all components')
    list_parser.add_argument('--format', choices=['table', 'json'], default='table',
                            help='Output format')

    # Show command
    show_parser = subparsers.add_parser('show', help='Show component details')
    show_parser.add_argument('component', help='Component name')
    show_parser.add_argument('framework', help='Test framework')

    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validate component configuration')
    validate_parser.add_argument('component', help='Component name')
    validate_parser.add_argument('framework', help='Test framework')

    # Find-job command
    find_job_parser = subparsers.add_parser('find-job', help='Find components for a Jenkins job')
    find_job_parser.add_argument('job_path', help='Jenkins job path')

    # Stats command
    subparsers.add_parser('stats', help='Show registry statistics')

    # Discover command
    subparsers.add_parser('discover', help='Rediscover all components')

    args = parser.parse_args()

    # Initialize registry
    registry = ComponentRegistry()

    # Execute command
    if args.command == 'list':
        list_components(registry, args.format)
    elif args.command == 'show':
        show_component(registry, args.component, args.framework)
    elif args.command == 'validate':
        validate_component(registry, args.component, args.framework)
    elif args.command == 'find-job':
        find_components_for_job(registry, args.job_path)
    elif args.command == 'stats':
        show_stats(registry)
    elif args.command == 'discover':
        discover_and_refresh(registry)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
