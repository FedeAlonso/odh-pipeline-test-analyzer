"""
Component registry for discovering and loading test analysis components
"""
from .component_registry import ComponentRegistry
from .plugin_loader import PluginLoader
from .validator import ComponentValidator

__all__ = ['ComponentRegistry', 'PluginLoader', 'ComponentValidator']
