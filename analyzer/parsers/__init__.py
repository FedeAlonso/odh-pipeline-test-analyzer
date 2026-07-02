"""
Default parsers for common test frameworks

These parsers work for most projects using standard frameworks.
Components can override by providing their own parser.py
"""
from .pytest_parser import PytestDefaultParser

# Import other parsers if they exist
try:
    from .cypress_parser import CypressDefaultParser
except ImportError:
    CypressDefaultParser = None

try:
    from .golang_parser import GolangDefaultParser
except ImportError:
    GolangDefaultParser = None

__all__ = ['PytestDefaultParser', 'get_default_parser']


def get_default_parser(framework: str):
    """
    Get default parser for a framework

    Args:
        framework: Framework name (pytest, cypress, golang, etc.)

    Returns:
        Parser class or None if no default available
    """
    parsers = {
        'pytest': PytestDefaultParser,
        'jest': PytestDefaultParser,  # Jest output is similar enough
        'junit': PytestDefaultParser,  # JUnit XML is similar
    }

    # Add optional parsers if available
    if CypressDefaultParser:
        parsers['cypress'] = CypressDefaultParser
    if GolangDefaultParser:
        parsers['golang'] = GolangDefaultParser

    return parsers.get(framework)
