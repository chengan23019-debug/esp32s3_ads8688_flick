"""
Algorithm package initialization.
Exposes DataProcessingEngine and FlickerCore.
"""

from .data_processor import DataProcessingEngine, BaseProcessingRule, PassThroughRule, ZeroClipRule, CustomUserRulePlaceholder
from .flicker_core import FlickerCore, FlickerResult

__all__ = [
    "DataProcessingEngine",
    "BaseProcessingRule",
    "PassThroughRule",
    "ZeroClipRule",
    "CustomUserRulePlaceholder",
    "FlickerCore",
    "FlickerResult"
]
