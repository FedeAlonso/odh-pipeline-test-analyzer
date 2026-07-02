"""
Additional methods to add to PluginLoader class

Add these to the PluginLoader class in plugin_loader.py
"""

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


def _load_default_parser(self, framework: str, config: Dict[str, Any]):
    """
    Load default parser for a framework

    Args:
        framework: Framework name (pytest, cypress, etc.)
        config: Component configuration

    Returns:
        Parser instance or None
    """
    try:
        from analyzer.parsers import get_default_parser

        parser_class = get_default_parser(framework)
        if parser_class:
            return parser_class(config)

        logger.warning(f"No default parser available for framework: {framework}")
        return None

    except ImportError as e:
        logger.error(f"Error importing default parsers: {e}")
        return None
