"""
Component Registry - Discovers and manages component plugins

Scans .claude/components/ directory to find available test analysis components
and provides API for loading and querying them.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

from .validator import ComponentValidator
from .plugin_loader import PluginLoader


logger = logging.getLogger(__name__)


class ComponentRegistry:
    """
    Registry for discovering and managing test analysis components

    Each component is a plugin that provides test parsing and analysis
    for a specific component/framework combination.
    """

    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialize component registry

        Args:
            base_path: Base path for components (default: .claude/components)
        """
        self.base_path = base_path or Path('.claude/components')
        self.components: Dict[str, Dict[str, Any]] = {}
        self.validator = ComponentValidator()
        self.plugin_loader = PluginLoader()

        # Discover components on initialization
        if self.base_path.exists():
            self.discover_components()

    def discover_components(self) -> List[str]:
        """
        Scan directory for component plugins

        Returns:
            List of discovered component keys (format: "component-name/framework")
        """
        discovered = []

        if not self.base_path.exists():
            logger.warning(f"Component base path does not exist: {self.base_path}")
            return discovered

        # Scan for component.json files
        for component_json in self.base_path.rglob('component.json'):
            try:
                # Load component configuration
                with open(component_json) as f:
                    config = json.load(f)

                # Validate configuration
                is_valid, errors = self.validator.validate(config)
                if not is_valid:
                    logger.error(f"Invalid component config at {component_json}: {errors}")
                    continue

                # Register component
                component_name = config['name']
                framework = config['framework']
                component_key = f"{component_name}/{framework}"

                # Add paths to config
                config['_component_dir'] = component_json.parent
                config['_config_file'] = component_json

                self.components[component_key] = config
                discovered.append(component_key)

                logger.info(f"Discovered component: {component_key}")

            except Exception as e:
                logger.error(f"Error loading component from {component_json}: {e}")
                continue

        logger.info(f"Discovered {len(discovered)} components")
        return discovered

    def get_component(self, component_name: str, framework: str) -> Optional[Dict[str, Any]]:
        """
        Get component configuration

        Args:
            component_name: Name of component (e.g., 'dashboard')
            framework: Test framework (e.g., 'cypress')

        Returns:
            Component configuration dictionary or None if not found
        """
        component_key = f"{component_name}/{framework}"
        return self.components.get(component_key)

    def load_parser(self, component_name: str, framework: str):
        """
        Load parser module for a component

        Args:
            component_name: Name of component
            framework: Test framework

        Returns:
            Parser instance or None if not found/failed
        """
        config = self.get_component(component_name, framework)
        if not config:
            logger.error(f"Component not found: {component_name}/{framework}")
            return None

        parser_module = config['analysis'].get('parser_module')
        if not parser_module:
            logger.error(f"No parser_module specified for {component_name}/{framework}")
            return None

        return self.plugin_loader.load_parser(config)

    def load_analyzer(self, component_name: str, framework: str):
        """
        Load analyzer module for a component

        Args:
            component_name: Name of component
            framework: Test framework

        Returns:
            Analyzer instance or None if not found/failed
        """
        config = self.get_component(component_name, framework)
        if not config:
            logger.error(f"Component not found: {component_name}/{framework}")
            return None

        analyzer_module = config['analysis'].get('analyzer_module')
        if not analyzer_module:
            logger.warning(f"No analyzer_module specified for {component_name}/{framework}, using default")
            return None

        return self.plugin_loader.load_analyzer(config)

    def load_reporter(self, component_name: str, framework: str):
        """
        Load reporter module for a component

        Args:
            component_name: Name of component
            framework: Test framework

        Returns:
            Reporter instance or None if not found/failed
        """
        config = self.get_component(component_name, framework)
        if not config:
            logger.error(f"Component not found: {component_name}/{framework}")
            return None

        return self.plugin_loader.load_reporter(config)

    def list_components(self) -> List[Dict[str, Any]]:
        """
        List all discovered components

        Returns:
            List of component configurations
        """
        return [
            {
                'key': key,
                'name': config['name'],
                'framework': config['framework'],
                'description': config.get('description', 'No description'),
                'maintainers': config.get('maintainers', []),
                'jenkins_jobs': config.get('jenkins', {}).get('job_paths', []),
                'priority': config.get('metadata', {}).get('priority', 'medium')
            }
            for key, config in self.components.items()
        ]

    def get_components_for_job(self, job_path: str) -> List[str]:
        """
        Find components that analyze a specific Jenkins job

        Args:
            job_path: Jenkins job path (e.g., 'cypress/dashboard-tests')

        Returns:
            List of component keys that analyze this job
        """
        matching = []

        for key, config in self.components.items():
            jenkins_config = config.get('jenkins', {})
            job_paths = jenkins_config.get('job_paths', [])

            if job_path in job_paths:
                matching.append(key)

        return matching

    def get_components_by_description(self, description_pattern: str) -> List[str]:
        """
        Find components that analyze jobs with matching descriptions

        Args:
            description_pattern: Job description pattern (e.g., 'dash-e2e-rhoai')

        Returns:
            List of component keys that match this description pattern
        """
        matching = []

        for key, config in self.components.items():
            jenkins_config = config.get('jenkins', {})
            job_descriptions = jenkins_config.get('job_descriptions', [])

            if description_pattern in job_descriptions:
                matching.append(key)

        return matching

    def get_all_jenkins_jobs(self) -> Dict[str, List[str]]:
        """
        Get mapping of all Jenkins jobs to components

        Returns:
            Dictionary mapping job paths to lists of component keys
        """
        job_map = {}

        for key, config in self.components.items():
            jenkins_config = config.get('jenkins', {})
            job_paths = jenkins_config.get('job_paths', [])

            for job_path in job_paths:
                if job_path not in job_map:
                    job_map[job_path] = []
                job_map[job_path].append(key)

        return job_map

    def validate_component(self, component_name: str, framework: str) -> tuple[bool, List[str]]:
        """
        Validate a component configuration

        Args:
            component_name: Name of component
            framework: Test framework

        Returns:
            Tuple of (is_valid, list of errors)
        """
        config = self.get_component(component_name, framework)
        if not config:
            return False, [f"Component not found: {component_name}/{framework}"]

        return self.validator.validate(config)

    def reload_component(self, component_name: str, framework: str) -> bool:
        """
        Reload a component configuration from disk

        Args:
            component_name: Name of component
            framework: Test framework

        Returns:
            True if reloaded successfully, False otherwise
        """
        component_key = f"{component_name}/{framework}"

        # Find the component.json file
        config_file = None
        for comp_json in self.base_path.rglob('component.json'):
            with open(comp_json) as f:
                config = json.load(f)
                if config.get('name') == component_name and config.get('framework') == framework:
                    config_file = comp_json
                    break

        if not config_file:
            logger.error(f"Component config file not found: {component_key}")
            return False

        try:
            with open(config_file) as f:
                config = json.load(f)

            # Validate
            is_valid, errors = self.validator.validate(config)
            if not is_valid:
                logger.error(f"Invalid component config: {errors}")
                return False

            # Update paths
            config['_component_dir'] = config_file.parent
            config['_config_file'] = config_file

            # Reload
            self.components[component_key] = config
            logger.info(f"Reloaded component: {component_key}")
            return True

        except Exception as e:
            logger.error(f"Error reloading component: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """
        Get registry statistics

        Returns:
            Dictionary with component statistics
        """
        frameworks = set(config['framework'] for config in self.components.values())
        priorities = {}

        for config in self.components.values():
            priority = config.get('metadata', {}).get('priority', 'medium')
            priorities[priority] = priorities.get(priority, 0) + 1

        return {
            'total_components': len(self.components),
            'frameworks': list(frameworks),
            'framework_count': len(frameworks),
            'by_priority': priorities,
            'jenkins_jobs_tracked': len(self.get_all_jenkins_jobs())
        }
