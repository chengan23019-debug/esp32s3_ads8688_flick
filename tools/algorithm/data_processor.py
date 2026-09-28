"""
Data Processing Engine for ADS8688 Signal Pipeline.
Provides a flexible, extensible rule-based pipeline between data acquisition
and downstream analytics (oscilloscope view, flicker analysis, etc.).
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import numpy as np


class BaseProcessingRule(ABC):
    """
    Abstract base class for all data processing rules.
    Subclass this to implement custom filtering, calibration, denoising, etc.
    """
    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled

    @abstractmethod
    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        """
        Process the input 1D numpy array of samples.
        :param data: 1D float/int numpy array of sampled voltages or raw codes.
        :param fs: Sampling frequency in Hz (e.g. 220000.0).
        :param context: Optional extra parameters (e.g. channel info, calibration constants).
        :return: Processed 1D numpy array.
        """
        pass


class PassThroughRule(BaseProcessingRule):
    """Default pass-through rule: does nothing."""
    def __init__(self, name: str = "PassThrough", enabled: bool = True):
        super().__init__(name, enabled)

    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        return data


class ZeroClipRule(BaseProcessingRule):
    """
    Zero-clipping rule: optical signals (photodiode/illuminance) cannot be physically negative.
    Removes small negative offsets/noise caused by op-amp offset voltage.
    """
    def __init__(self, name: str = "ZeroClip", enabled: bool = True, min_value: float = 0.0):
        super().__init__(name, enabled)
        self.min_value = min_value

    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        if not self.enabled:
            return data
        return np.clip(data, self.min_value, None)


class CustomUserRulePlaceholder(BaseProcessingRule):
    """
    ================================================================================
    【预留开发位置】用户自定义数据处理规则 (User Custom Rule Slot)
    在此处添加您的具体处理规则，如：
      - 光谱响应修正 / 照度换算系数 (Lux = Voltage * K + B)
      - 基线漂移消除 / 移动平均滤波 (Baseline wander removal / Moving average)
      - 50Hz/60Hz 工频陷波或带通滤波 (Notch / Bandpass filter)
      - 异常脉冲剔除 (Glitch / Outlier rejection)
      - 多项式非线性校准 (Polynomial calibration)
    ================================================================================
    """
    def __init__(self, name: str = "CustomRuleSlot", enabled: bool = False, **kwargs):
        super().__init__(name, enabled)
        self.params = kwargs

    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        if not self.enabled:
            return data

        processed_data = data.copy()

        # >>>>>> [在此编写您的自定义算法规则逻辑] <<<<<<
        # 示例：
        # gain = self.params.get('gain', 1.0)
        # offset = self.params.get('offset', 0.0)
        # processed_data = processed_data * gain + offset

        return processed_data


class DataProcessingEngine:
    """
    The orchestrator pipeline that runs raw acquisition data through registered rules
    before feeding it into flicker or spectrum algorithms.
    """
    def __init__(self):
        self.rules: List[BaseProcessingRule] = []
        self._init_default_rules()

    def _init_default_rules(self):
        # 1. 默认直通规则
        self.rules.append(PassThroughRule(name="PassThrough", enabled=True))
        # 2. 负值截断规则（光学频闪要求非负）
        self.rules.append(ZeroClipRule(name="ZeroClip", enabled=True, min_value=0.0))
        # 3. 预留用户规则槽位（默认不启用，待用户填入逻辑后随时开启）
        self.rules.append(CustomUserRulePlaceholder(name="UserRuleSlot", enabled=False))

    def add_rule(self, rule: BaseProcessingRule, index: Optional[int] = None):
        """Append or insert a new processing rule."""
        if index is None:
            self.rules.append(rule)
        else:
            self.rules.insert(index, rule)

    def remove_rule_by_name(self, name: str):
        self.rules = [r for r in self.rules if r.name != name]

    def get_rule(self, name: str) -> Optional[BaseProcessingRule]:
        for r in self.rules:
            if r.name == name:
                return r
        return None

    def set_rule_enabled(self, name: str, enabled: bool):
        rule = self.get_rule(name)
        if rule:
            rule.enabled = enabled

    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        """
        Execute all active rules sequentially.
        """
        if data is None or len(data) == 0:
            return data

        out = data
        for rule in self.rules:
            if rule.enabled:
                out = rule.process(out, fs, context=context)
        return out
