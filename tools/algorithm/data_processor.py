"""
Data Processing Engine for ADS8688 Signal Pipeline.
Provides a flexible, extensible rule-based pipeline between data acquisition
and downstream analytics (oscilloscope view, flicker analysis, etc.).
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import numpy as np
from scipy import signal


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
        :param data: 1D float numpy array of sampled voltages or raw codes.
        :param fs: Sampling frequency in Hz (e.g. 220000.0).
        :param context: Optional extra parameters.
        :return: Processed 1D numpy array.
        """
        pass

    def reset(self):
        """Reset internal filter states when stream breaks or resets."""
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


class SpikeGlitchRemovalRule(BaseProcessingRule):
    """
    针对硬件毛刺/异常窄脉冲尖峰的剔除滤波规则 (Anti-Glitch / Spike Filter)。
    利用局部滑动中值差分检测瞬间跳变的窄脉冲毛刺并用平滑值替换，
    特别有效滤除 USB 传输包边界干扰、电源地弹跳或 ADC 突发瞬态尖峰，
    同时完好保留正弦波/三角波主频信号的原始波峰波谷形态。
    """
    def __init__(
        self,
        name: str = "SpikeGlitchFilter",
        enabled: bool = True,
        threshold_volts: float = 0.15,
        window_size: int = 5
    ):
        super().__init__(name, enabled)
        self.threshold = threshold_volts
        self.window_size = window_size
        self._history = np.array([], dtype=np.float32)

    def reset(self):
        self._history = np.array([], dtype=np.float32)

    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        if not self.enabled or len(data) < 7:
            return data

        out = data.copy()
        n = len(out)
        w = max(5, self.window_size | 1)  # 保持奇数窗口
        half_w = w // 2

        # 拼接历史尾部数据保证分块边界平滑
        if len(self._history) >= half_w:
            concat_data = np.concatenate((self._history[-half_w:], out))
            start_offset = half_w
        else:
            concat_data = out
            start_offset = 0

        # 保留最后几个点供下一批次拼接使用
        self._history = out[-half_w:].copy()

        d = concat_data
        total_len = len(d)

        # 针对毛刺的局部统计滤波：
        # 1. 检测快速单点/双点下冲突变（下冲尖刺是图示的主要问题）
        for i in range(max(1, start_offset), total_len - 1):
            prev_val = d[i - 1]
            cur_val = d[i]
            next_val = d[i + 1]

            local_baseline = 0.5 * (prev_val + next_val)
            delta = cur_val - local_baseline

            # 强下冲毛刺检测 (cur_val 明显低于局部均值)
            if delta < -self.threshold:
                # 左右两点相对连续，属于孤立下冲尖刺
                if abs(next_val - prev_val) < abs(delta) * 0.75:
                    d[i] = local_baseline

            # 强上冲毛刺检测 (cur_val 明显高于局部均值)
            elif delta > self.threshold:
                if abs(next_val - prev_val) < abs(delta) * 0.75:
                    d[i] = local_baseline

        return concat_data[start_offset : start_offset + n]


class IIRLowPassFilterRule(BaseProcessingRule):
    """
    高频噪声平滑低通滤波规则 (Butterworth Low-pass Filter)。
    保持连续流式 IIR 状态（lfilter_zi），在分包连续流处理中无接缝突变。
    截止频率默认 20 kHz，用于滤除 ADS8688 高采样率 (220 kSPS) 下引入的超宽带高频底噪。
    """
    def __init__(
        self,
        name: str = "LowPassFilter",
        enabled: bool = False,
        cutoff_hz: float = 20000.0,
        order: int = 2
    ):
        super().__init__(name, enabled)
        self.cutoff_hz = cutoff_hz
        self.order = order
        self._b = None
        self._a = None
        self._zi = None
        self._last_fs = None

    def reset(self):
        self._zi = None

    def _update_coefficients(self, fs: float):
        nyq = 0.5 * fs
        normal_cutoff = min(0.95, max(0.001, self.cutoff_hz / nyq))
        self._b, self._a = signal.butter(self.order, normal_cutoff, btype='low', analog=False)
        self._zi = signal.lfilter_zi(self._b, self._a)
        self._last_fs = fs

    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        if not self.enabled or len(data) == 0:
            return data

        if self._b is None or self._last_fs != fs:
            self._update_coefficients(fs)

        if self._zi is None:
            self._zi = signal.lfilter_zi(self._b, self._a) * data[0]

        filtered, self._zi = signal.lfilter(self._b, self._a, data, zi=self._zi)
        return filtered.astype(np.float32)


class MovingAverageFilterRule(BaseProcessingRule):
    """
    快速滑动均值滤波规则 (Moving Average Filter)。
    简单稳健，用于去除细小高频毛刺，使示波器波形更平滑纯净。
    """
    def __init__(self, name: str = "MovingAverage", enabled: bool = False, window_size: int = 5):
        super().__init__(name, enabled)
        self.window_size = window_size
        self._buf = np.array([], dtype=np.float32)

    def reset(self):
        self._buf = np.array([], dtype=np.float32)

    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        if not self.enabled or len(data) < self.window_size:
            return data

        w = max(3, int(self.window_size))
        # 拼接前序缓冲以消除分块滤波边界效应
        if len(self._buf) > 0:
            full = np.concatenate((self._buf, data))
        else:
            full = data

        self._buf = full[-w:].copy()

        kernel = np.ones(w, dtype=np.float32) / w
        filtered = np.convolve(full, kernel, mode='same')
        return filtered[-len(data):]


class CustomUserRulePlaceholder(BaseProcessingRule):
    """
    【预留开发位置】用户自定义数据处理规则 (User Custom Rule Slot)
    在此处可随时添加您的个性化算法逻辑，例如增益放大、偏置校准等。
    """
    def __init__(self, name: str = "UserRuleSlot", enabled: bool = False, **kwargs):
        super().__init__(name, enabled)
        self.params = kwargs

    def process(self, data: np.ndarray, fs: float, context: Optional[Dict[str, Any]] = None) -> np.ndarray:
        if not self.enabled:
            return data

        processed_data = data.copy()
        # 自定义处理逻辑：
        # gain = self.params.get('gain', 1.0)
        # offset = self.params.get('offset', 0.0)
        # processed_data = processed_data * gain + offset
        return processed_data


class DataProcessingEngine:
    """
    数据处理引擎中心调度管线：管理并按序执行所有已注册的数据处理与滤波规则。
    """
    def __init__(self):
        self.rules: List[BaseProcessingRule] = []
        self._init_default_rules()

    def _init_default_rules(self):
        # 1. 默认直通规则
        self.rules.append(PassThroughRule(name="PassThrough", enabled=True))
        # 2. 负值截断规则（光学频闪要求非负）
        self.rules.append(ZeroClipRule(name="ZeroClip", enabled=True, min_value=0.0))
        # 3. 硬件毛刺/尖峰脉冲剔除规则（专门滤除图片中底部的瞬态下冲毛刺）
        self.rules.append(SpikeGlitchRemovalRule(name="SpikeGlitchFilter", enabled=True, threshold_volts=0.10, window_size=5))
        # 4. 20kHz 连续流式低通滤波规则（滤除高频底噪）
        self.rules.append(IIRLowPassFilterRule(name="LowPassFilter", enabled=False, cutoff_hz=20000.0, order=2))
        # 5. 滑动均值平滑滤波规则
        self.rules.append(MovingAverageFilterRule(name="MovingAverage", enabled=False, window_size=5))
        # 6. 预留用户规则槽位
        self.rules.append(CustomUserRulePlaceholder(name="UserRuleSlot", enabled=False))

    def reset_all_filters(self):
        """Reset state across all streaming filter rules."""
        for r in self.rules:
            r.reset()

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
