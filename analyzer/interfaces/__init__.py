"""
Plugin interfaces for the modular test analysis platform
"""
from .base_parser import BaseTestParser
from .base_analyzer import BaseFailureAnalyzer
from .base_reporter import BaseReporter

__all__ = ['BaseTestParser', 'BaseFailureAnalyzer', 'BaseReporter']
