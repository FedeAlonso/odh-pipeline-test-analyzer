"""
Plugin Loader - Dynamically loads component parser and analyzer modules
"""
import importlib.util
import sys
from pathlib import Path
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class PluginLoader:
    """Loads component plugin modules dynamically"""

    def __init__(self):
        """Initialize plugin loader"""
        self.loaded_modules = {}

    def load_parser(self, config: Dict[str, Any]):
        """
        Load parser module for a component

        Tries in this order:
        1. Component-specific parser (parser.py in component directory)
        2. Default framework parser (analyzer/parsers/{framework}_parser.py)

        Args:
            config: Component configuration

        Returns:
            Parser instance or None if failed
        """
        parser_module_name = config['analysis'].get('parser_module')
        component_dir = config.get('_component_dir')
        framework = config.get('framework', 'unknown')

        # Try 1: Component-specific parser
        if parser_module_name and component_dir:
            parser_path = Path(component_dir) / parser_module_name

            if parser_path.exists():
                logger.info(f"Using component-specific parser: {parser_path}")
                return self._load_parser_from_path(parser_path, config)
            else:
                logger.warning(f"Component parser not found: {parser_path}")

        # Try 2: Default framework parser
        logger.info(f"Attempting to use default parser for framework: {framework}")
        default_parser = self._load_default_parser(framework, config)

        if default_parser:
            logger.info(f"Using default {framework} parser")
            return default_parser

        logger.error(f"No parser available for {config.get('name')}/{framework}")
        return None

    def _load_parser_from_path(self, parser_path: Path, config: Dict[str, Any]):
        """Load parser from a specific file path"""

        try:
            # Load module dynamically
            module_name = f"{config['name']}_{config['framework']}_parser"
            spec = importlib.util.spec_from_file_location(module_name, parser_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Cache module
            self.loaded_modules[f"{config['name']}/{config['framework']}/parser"] = module

            # Find parser class
            parser_class = self._find_parser_class(module)
            if not parser_class:
                logger.error(f"No parser class found in {parser_path}")
                return None

            # Instantiate parser
            return parser_class(config)

        except Exception as e:
            logger.error(f"Error loading parser from {parser_path}: {e}")
            return None

    def load_analyzer(self, config: Dict[str, Any]):
        """
        Load analyzer module for a component

        Args:
            config: Component configuration

        Returns:
            Analyzer instance or None if failed
        """
        analyzer_module_name = config['analysis'].get('analyzer_module')
        if not analyzer_module_name:
            logger.warning("No analyzer_module specified in config")
            return None

        component_dir = config.get('_component_dir')
        if not component_dir:
            logger.error("Component directory not set in config")
            return None

        analyzer_path = Path(component_dir) / analyzer_module_name

        if not analyzer_path.exists():
            logger.error(f"Analyzer module not found: {analyzer_path}")
            return None

        try:
            # Load module dynamically
            module_name = f"{config['name']}_{config['framework']}_analyzer"
            spec = importlib.util.spec_from_file_location(module_name, analyzer_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Cache module
            self.loaded_modules[f"{config['name']}/{config['framework']}/analyzer"] = module

            # Find analyzer class
            analyzer_class = self._find_analyzer_class(module)
            if not analyzer_class:
                logger.error(f"No analyzer class found in {analyzer_path}")
                return None

            # Instantiate analyzer
            return analyzer_class(config)

        except Exception as e:
            logger.error(f"Error loading analyzer from {analyzer_path}: {e}")
            return None

    def load_reporter(self, config: Dict[str, Any]):
        """
        Load reporter module for a component

        Args:
            config: Component configuration

        Returns:
            Reporter instance or None if using default
        """
        # Check if custom reporter is specified
        reporting_config = config.get('reporting', {})
        reporter_module = reporting_config.get('reporter_module')

        if not reporter_module:
            # Use default reporter (markdown - simple and portable)
            from analyzer.interfaces.base_reporter import BaseReporter

            class DefaultReporter(BaseReporter):
                """Default reporter implementation with detailed breakdown"""

                def generate_report(self, analysis_results: Dict[str, Any],
                                   output_path: Optional[Path] = None) -> str:
                    """Generate comprehensive markdown report"""
                    sections = []

                    # Header
                    metadata = analysis_results.get('metadata', {})
                    sections.append(f"# {self.component_name.title()} Test Analysis Report")
                    sections.append(f"\n**Build**: #{metadata.get('build_number', 'N/A')}")
                    sections.append(f"**Job**: `{metadata.get('build_url', 'N/A')}`")
                    sections.append(f"**Status**: {metadata.get('status', 'N/A')}")
                    sections.append(f"**Date**: {metadata.get('analysis_date', 'N/A')}\n")

                    # Summary
                    test_results = analysis_results.get('test_results', {})
                    sections.append(self.format_summary_section(test_results))

                    # Breakdown by suite
                    artifact_results = test_results.get('artifact_results', {})
                    if artifact_results:
                        sections.append("\n## Test Suite Breakdown\n")
                        for suite_name, suite_data in sorted(artifact_results.items()):
                            total = suite_data.get('total', 0)
                            passed = suite_data.get('passed', 0)
                            failed = suite_data.get('failed', 0)
                            skipped = suite_data.get('skipped', 0)

                            sections.append(f"### {suite_name}")
                            sections.append(f"- **Total**: {total}")
                            sections.append(f"- **Passed**: {passed}")
                            sections.append(f"- **Failed**: {failed}")
                            sections.append(f"- **Skipped**: {skipped}")

                            if failed > 0:
                                failures = suite_data.get('failures', [])
                                if failures:
                                    sections.append(f"\n**Failures:**")
                                    for failure in failures[:5]:  # Show first 5
                                        test_name = failure.get('test_name', 'Unknown')
                                        error_msg = failure.get('error_message', 'No message')[:100]
                                        sections.append(f"- `{test_name}`: {error_msg}")
                                    if len(failures) > 5:
                                        sections.append(f"  _(and {len(failures) - 5} more)_")
                            sections.append("")

                    # Overall failures
                    analyzed_failures = analysis_results.get('analyzed_failures', [])
                    if analyzed_failures:
                        sections.append(self.format_failure_section(analyzed_failures))

                        # Category breakdown
                        by_category = analysis_results.get('by_category', {})
                        if by_category:
                            sections.append(self.format_category_breakdown(by_category))

                    # Cluster health
                    cluster_state = analysis_results.get('cluster_state')
                    if cluster_state:
                        sections.append(self.format_cluster_health_section(cluster_state))

                    report = '\n'.join(sections)

                    # Save if output_path provided
                    if output_path:
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text(report)
                        logger.info(f"Report saved to: {output_path}")

                    return report

            return DefaultReporter(config)

        # Load custom reporter
        component_dir = config.get('_component_dir')
        reporter_path = Path(component_dir) / reporter_module

        if not reporter_path.exists():
            logger.error(f"Reporter module not found: {reporter_path}")
            return None

        try:
            module_name = f"{config['name']}_{config['framework']}_reporter"
            spec = importlib.util.spec_from_file_location(module_name, reporter_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Find reporter class
            reporter_class = self._find_reporter_class(module)
            if not reporter_class:
                logger.error(f"No reporter class found in {reporter_path}")
                return None

            return reporter_class(config)

        except Exception as e:
            logger.error(f"Error loading reporter from {reporter_path}: {e}")
            return None

    def _load_default_parser(self, framework: str, config: Dict[str, Any]):
        """Load default parser for a framework"""
        try:
            from analyzer.parsers import get_default_parser

            parser_class = get_default_parser(framework)
            if parser_class:
                return parser_class(config)
            return None
        except Exception as e:
            logger.error(f"Error loading default parser for {framework}: {e}")
            return None

    def _find_parser_class(self, module):
        """Find parser class in module (subclass of BaseTestParser)"""
        from analyzer.interfaces.base_parser import BaseTestParser

        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseTestParser) and obj != BaseTestParser:
                return obj
        return None

    def _find_analyzer_class(self, module):
        """Find analyzer class in module (subclass of BaseFailureAnalyzer)"""
        from analyzer.interfaces.base_analyzer import BaseFailureAnalyzer

        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseFailureAnalyzer) and obj != BaseFailureAnalyzer:
                return obj
        return None

    def _find_reporter_class(self, module):
        """Find reporter class in module (subclass of BaseReporter or BaseHTMLReporter)"""
        from analyzer.interfaces.base_reporter import BaseReporter
        from analyzer.interfaces.base_html_reporter import BaseHTMLReporter

        for name in dir(module):
            obj = getattr(module, name)
            # Accept both BaseReporter and BaseHTMLReporter subclasses
            if isinstance(obj, type) and (
                (issubclass(obj, BaseReporter) and obj != BaseReporter) or
                (issubclass(obj, BaseHTMLReporter) and obj != BaseHTMLReporter)
            ):
                return obj
        return None
