"""
Component Configuration Validator

Validates component.json files against the schema
"""
import json
from pathlib import Path
from typing import Dict, List, Any, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class ComponentValidator:
    """Validates component configurations"""

    def __init__(self, schema_path: Optional[Path] = None):
        """
        Initialize validator

        Args:
            schema_path: Path to component.schema.json (default: .claude/schemas/component.schema.json)
        """
        self.schema_path = schema_path or Path('.claude/schemas/component.schema.json')
        self.schema = None

        if self.schema_path.exists():
            with open(self.schema_path) as f:
                self.schema = json.load(f)

    def validate(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate component configuration

        Args:
            config: Component configuration dictionary

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors = []

        # Check required fields
        required_fields = ['name', 'framework', 'version', 'jenkins', 'test_framework', 'analysis']
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")

        if errors:
            return False, errors

        # Validate name format
        name = config.get('name', '')
        if not self._validate_name(name):
            errors.append(f"Invalid name format: {name} (must be lowercase, hyphenated)")

        # Validate version format
        version = config.get('version', '')
        if not self._validate_version(version):
            errors.append(f"Invalid version format: {version} (must be semver: X.Y.Z)")

        # Validate framework
        framework = config.get('framework', '')
        valid_frameworks = ['cypress', 'pytest', 'golang', 'jest', 'junit', 'selenium']
        if framework not in valid_frameworks:
            errors.append(f"Invalid framework: {framework} (must be one of {valid_frameworks})")

        # Validate Jenkins configuration
        jenkins_errors = self._validate_jenkins_config(config.get('jenkins', {}))
        errors.extend(jenkins_errors)

        # Validate test framework configuration
        test_framework_errors = self._validate_test_framework(config.get('test_framework', {}))
        errors.extend(test_framework_errors)

        # Validate analysis configuration
        analysis_errors = self._validate_analysis_config(config.get('analysis', {}))
        errors.extend(analysis_errors)

        # Validate maintainers format
        maintainers = config.get('maintainers', [])
        for maintainer in maintainers:
            if not maintainer.startswith('@'):
                errors.append(f"Invalid maintainer format: {maintainer} (must start with @)")

        return len(errors) == 0, errors

    def _validate_name(self, name: str) -> bool:
        """Validate component name format"""
        import re
        pattern = r'^[a-z][a-z0-9-]*$'
        return bool(re.match(pattern, name))

    def _validate_version(self, version: str) -> bool:
        """Validate version format (semver)"""
        import re
        pattern = r'^\d+\.\d+\.\d+$'
        return bool(re.match(pattern, version))

    def _validate_jenkins_config(self, jenkins_config: Dict[str, Any]) -> List[str]:
        """Validate Jenkins configuration"""
        errors = []

        if 'job_paths' not in jenkins_config:
            errors.append("Jenkins config missing required field: job_paths")
        elif not isinstance(jenkins_config['job_paths'], list):
            errors.append("Jenkins job_paths must be a list")
        elif len(jenkins_config['job_paths']) == 0:
            errors.append("Jenkins job_paths cannot be empty")

        return errors

    def _validate_test_framework(self, test_framework: Dict[str, Any]) -> List[str]:
        """Validate test framework configuration"""
        errors = []

        required_fields = ['type', 'test_directory']
        for field in required_fields:
            if field not in test_framework:
                errors.append(f"Test framework missing required field: {field}")

        # Validate framework type
        framework_type = test_framework.get('type', '')
        valid_types = ['cypress', 'pytest', 'golang', 'jest', 'junit', 'selenium']
        if framework_type and framework_type not in valid_types:
            errors.append(f"Invalid test framework type: {framework_type}")

        return errors

    def _validate_analysis_config(self, analysis_config: Dict[str, Any]) -> List[str]:
        """Validate analysis configuration"""
        errors = []

        if 'parser_module' not in analysis_config:
            errors.append("Analysis config missing required field: parser_module")

        return errors

    def validate_file(self, config_file: Path) -> Tuple[bool, List[str]]:
        """
        Validate a component.json file

        Args:
            config_file: Path to component.json

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        if not config_file.exists():
            return False, [f"File not found: {config_file}"]

        try:
            with open(config_file) as f:
                config = json.load(f)
            return self.validate(config)
        except json.JSONDecodeError as e:
            return False, [f"Invalid JSON: {e}"]
        except Exception as e:
            return False, [f"Error reading file: {e}"]

    def validate_with_schema(self, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """
        Validate configuration against JSON schema (if available)

        Args:
            config: Component configuration

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        if not self.schema:
            logger.warning("JSON schema not loaded, falling back to basic validation")
            return self.validate(config)

        try:
            import jsonschema
            jsonschema.validate(config, self.schema)
            return True, []
        except jsonschema.ValidationError as e:
            return False, [str(e)]
        except ImportError:
            logger.warning("jsonschema package not installed, falling back to basic validation")
            return self.validate(config)
