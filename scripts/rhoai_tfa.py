#!/usr/bin/env python3
"""
RHOAI Test Failure Analyzer - Unified CLI

Consolidated command-line interface for all TFA operations:
- Component management (list, show, validate, onboard)
- Test analysis (analyze builds, generate reports)

Usage:
    rhoai-tfa component list
    rhoai-tfa component show <name> [--framework=<pytest|cypress>]
    rhoai-tfa component validate <name> [--framework=<pytest|cypress>]
    rhoai-tfa component onboard
    rhoai-tfa analyze <component> --framework=<pytest|cypress> --build=<N>

Examples:
    # List all registered components
    rhoai-tfa component list

    # Show model-registry pytest configuration
    rhoai-tfa component show model-registry --framework=pytest

    # Onboard a new component
    rhoai-tfa component onboard

    # Analyze model-registry build #28
    rhoai-tfa analyze model-registry --framework=pytest --build=28

    # Analyze dashboard build with custom job
    rhoai-tfa analyze dashboard --framework=cypress --build=3695 --job="cypress/dashboard-tests"
"""
import sys
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def create_parser() -> argparse.ArgumentParser:
    """Create the main argument parser with subcommands"""
    parser = argparse.ArgumentParser(
        prog='rhoai-tfa',
        description='RHOAI Test Failure Analyzer - Multi-component test analysis platform',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  rhoai-tfa component list
  rhoai-tfa component show model-registry --framework=pytest
  rhoai-tfa component onboard
  rhoai-tfa analyze model-registry --framework=pytest --build=28

For more information, see TEAM_ONBOARDING.md
"""
    )

    # Top-level subcommands
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # ========== Component Management Commands ==========
    component_parser = subparsers.add_parser(
        'component',
        help='Manage components (list, show, validate, onboard)'
    )
    component_subparsers = component_parser.add_subparsers(dest='component_command')

    # component list
    component_subparsers.add_parser(
        'list',
        help='List all registered components'
    )

    # component show
    show_parser = component_subparsers.add_parser(
        'show',
        help='Show component configuration'
    )
    show_parser.add_argument('name', help='Component name')
    show_parser.add_argument(
        '--framework',
        help='Test framework (pytest, cypress, etc.)',
        default=None
    )

    # component validate
    validate_parser = component_subparsers.add_parser(
        'validate',
        help='Validate component configuration'
    )
    validate_parser.add_argument('name', help='Component name')
    validate_parser.add_argument(
        '--framework',
        help='Test framework (pytest, cypress, etc.)',
        default=None
    )

    # component onboard
    component_subparsers.add_parser(
        'onboard',
        help='Onboard a new component (interactive wizard)'
    )

    # ========== Analysis Commands ==========
    analyze_parser = subparsers.add_parser(
        'analyze',
        help='Analyze test results from a Jenkins build'
    )
    analyze_parser.add_argument('component', help='Component name (e.g., model-registry)')
    analyze_parser.add_argument(
        '--framework',
        required=True,
        help='Test framework (pytest, cypress, etc.)'
    )
    analyze_parser.add_argument(
        '--build',
        type=int,
        required=True,
        help='Jenkins build number'
    )
    analyze_parser.add_argument(
        '--job',
        help='Jenkins job path (uses component.json if not provided)'
    )
    analyze_parser.add_argument(
        '--variant',
        choices=['rhoai', 'odh'],
        default='rhoai',
        help='Platform variant (default: rhoai)'
    )

    return parser


def main():
    """Main entry point for unified CLI"""
    parser = create_parser()
    args = parser.parse_args()

    # Handle no command
    if not args.command:
        parser.print_help()
        return 0

    # Route to appropriate command handler
    if args.command == 'component':
        return handle_component_command(args)
    elif args.command == 'analyze':
        return handle_analyze_command(args)
    else:
        print(f"Unknown command: {args.command}")
        parser.print_help()
        return 1


def handle_component_command(args):
    """Handle component management commands"""
    if not args.component_command:
        print("Error: Component subcommand required")
        print("Use: rhoai-tfa component {list|show|validate|onboard}")
        return 1

    if args.component_command == 'list':
        # Import here to avoid loading dependencies until needed
        from scripts.component_cli import list_components
        return list_components()

    elif args.component_command == 'show':
        from scripts.component_cli import show_component
        return show_component(args.name, args.framework)

    elif args.component_command == 'validate':
        from scripts.component_cli import validate_component
        return validate_component(args.name, args.framework)

    elif args.component_command == 'onboard':
        from scripts.onboard_component import run_onboarding_wizard
        return run_onboarding_wizard()

    else:
        print(f"Unknown component command: {args.component_command}")
        return 1


def handle_analyze_command(args):
    """Handle test analysis command"""
    import asyncio
    from scripts.analyze_component import analyze_component_build

    # Run async analysis
    result = asyncio.run(analyze_component_build(
        component_name=args.component,
        framework=args.framework,
        build_number=args.build,
        jenkins_job=args.job,
        variant=args.variant
    ))

    return 0 if result else 1


if __name__ == '__main__':
    sys.exit(main())
