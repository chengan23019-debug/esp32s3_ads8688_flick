"""
Algorithm package initialization.
Exposes DataProcessingEngine and FlickerCore.
"""

from .data_processor import (
    DataProcessingEngine,
    BaseProcessingRule,
    PassThroughRule,
    ZeroClipRule,
    SpikeGlitchRemovalRule,
    IIRLowPassFilterRule,
    MovingAverageFilterRule,
    CustomUserRulePlaceholder,
)
from .flicker_core import FlickerCore, FlickerResult
from .pdf_reporter import FlickerPdfReportGenerator

__all__ = [
    "DataProcessingEngine",
    "BaseProcessingRule",
    "PassThroughRule",
    "ZeroClipRule",
    "SpikeGlitchRemovalRule",
    "IIRLowPassFilterRule",
    "MovingAverageFilterRule",
    "CustomUserRulePlaceholder",
    "FlickerCore",
    "FlickerResult",
    "FlickerPdfReportGenerator",
]
