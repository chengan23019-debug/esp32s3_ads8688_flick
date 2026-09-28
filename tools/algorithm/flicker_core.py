"""
Unified Flicker Measurement Core Engine.
Integrates algorithms:
- Percent Flicker & Flicker Index (IES RP-16-10 / IEEE 1789)
- SVM: Stroboscopic Visibility Measure (CIE TN 006:2016, EU 2019/2020)
- Mp: LRC Flicker Metric (Lighting Research Center)
- JA10: California CEC Title 24 JA10 (Multi-cutoff filtered modulation)
- PstLM: Short-term Flicker Indicator (IEC TR 61547-1)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.signal import find_peaks, butter, bilinear as scipy_bilinear, lfilter
from numpy.polynomial import Polynomial


@dataclass
class FlickerResult:
    flicker_percent: float = 0.0          # 闪烁百分比 / 波动深度 (%)
    flicker_index: float = 0.0            # 闪烁指数 FI
    dominant_frequency: float = 0.0       # 主频闪频率 (Hz)
    svm: float = 0.0                      # CIE SVM
    mp: float = 0.0                       # LRC Mp
    ja10: Dict[int, float] = field(default_factory=dict) # {40: %, 90: %, 200: %, 400: %, 1000: %}
    pst_lm: Optional[float] = None        # IEC PstLM
    p_inst_max: Optional[float] = None
    duration_sec: float = 0.0
    sample_count: int = 0
    fft_freqs: Optional[np.ndarray] = None
    fft_mags: Optional[np.ndarray] = None


class FlickerCore:
    """Core mathematical algorithms for optical flicker evaluation."""

    @staticmethod
    def calc_flicker_percent_and_index(waveform: np.ndarray) -> Tuple[float, float]:
        """
        Calculate Flicker Index and Percent Flicker (% Modulation).
        """
        if len(waveform) == 0:
            return 0.0, 0.0
        
        if len(waveform) > 200:
            # 使用 99.8% 与 0.2% 分位数排除残留孤立毛刺脉冲引起的极值剧烈震荡
            v_max = float(np.percentile(waveform, 99.8))
            v_min = float(np.percentile(waveform, 0.2))
        else:
            v_max = float(np.max(waveform))
            v_min = float(np.min(waveform))

        v_sum = float(v_max + v_min)
        percent = (100.0 * (v_max - v_min) / v_sum) if v_sum > 1e-9 else 0.0
        
        v_mean = float(np.mean(waveform))
        w = waveform - v_mean
        w_pos = np.where(w > 0, w, 0.0)
        total_sum = float(np.sum(waveform))
        
        fi = float(np.sum(w_pos) / total_sum) if total_sum > 1e-9 else 0.0
        return round(percent, 2), round(fi, 4)

    @staticmethod
    def _get_sensitivity_strobo(frequency: np.ndarray) -> np.ndarray:
        """CIE TN 006:2016 Human sensitivity function for SVM."""
        freq = np.asarray(frequency, dtype=float)
        a = 0.00518
        b = 306.6
        # threshold = 1 / (1 + exp(-a*(f - b))) + exp(-f/10)*20
        # Prevent runtime overflow in exp
        with np.errstate(over='ignore', divide='ignore'):
            term1 = 1.0 / (1.0 + np.exp(-a * (freq - b)))
            term2 = np.exp(-freq / 10.0) * 20.0
        return term1 + term2

    @classmethod
    def calc_svm(cls, waveform: np.ndarray, duration: float) -> Tuple[float, Optional[np.ndarray], Optional[np.ndarray], float]:
        """
        Calculate Stroboscopic Visibility Measure (SVM).
        Returns: (SVM_value, effective_freqs, effective_magnitudes, dominant_freq)
        """
        Ls = len(waveform)
        if Ls < 64 or duration <= 0:
            return 0.0, None, None, 0.0

        Fs = Ls / duration
        FmaxSVM = 2000.0

        if Fs < 100:
            return 0.0, None, None, 0.0

        # Downsample or handle if sampling rate is too high (> 20kSPS) to accelerate FFT without losing 2000Hz resolution
        # 220 kSPS has huge points (e.g. 220k points for 1s). Fast decimation for SVM:
        decimate_factor = 1
        if Fs > 40000:
            decimate_factor = int(Fs // 20000)
            if decimate_factor > 1:
                waveform = waveform[::decimate_factor]
                Ls = len(waveform)
                Fs = Fs / decimate_factor

        NFFT = int(2 ** int(np.ceil(np.log2(Ls)) + 1))
        h_win = np.hanning(Ls)
        attHann = float(np.sum(h_win)) / Ls
        
        # FFT
        Y = (1.0 / attHann) * np.fft.fft(h_win * waveform, NFFT) / Ls
        mag0 = np.abs(Y[0])
        if mag0 < 1e-9:
            return 0.0, None, None, 0.0

        half_n = NFFT // 2
        mag = np.abs(Y[0:half_n]) / mag0
        f = (Fs / 2.0) * np.linspace(0, 1, half_n)

        # Frequencies up to 2000 Hz
        max_idx = int(np.searchsorted(f, min(FmaxSVM, Fs / 2.0)))
        if max_idx <= 2:
            return 0.0, None, None, 0.0

        ff = f[1:max_idx]
        b = 2.0 * mag[1:max_idx]

        # Ignore sub-3Hz DC baseline wander and thermal drift when searching for dominant optical flicker frequency
        valid_freq_mask = (ff >= 3.0)
        if np.any(valid_freq_mask):
            valid_b = b[valid_freq_mask]
            valid_ff = ff[valid_freq_mask]
            dom_idx = np.argmax(valid_b)
            dom_freq = float(valid_ff[dom_idx])
        else:
            dom_idx = np.argmax(b) if len(b) > 0 else 0
            dom_freq = float(ff[dom_idx]) if len(ff) > 0 else 0.0

        # Peak detection
        b_pks, _ = find_peaks(b)
        if len(b_pks) == 0:
            return 0.0, ff, b, round(dom_freq, 1)

        i_pks = b[b_pks]
        sens = cls._get_sensitivity_strobo(ff[b_pks])
        c_pks = i_pks / sens
        svm_val = float(np.sum(c_pks ** 3.7) ** (1.0 / 3.7))
        return round(svm_val, 3), ff, b, round(dom_freq, 1)

    @staticmethod
    def calc_mp(waveform: np.ndarray, fs: float) -> float:
        """Calculate LRC Flicker Metric Mp (Lighting Research Center)."""
        if len(waveform) < 128:
            return 0.0

        # Decimate if fs is very high to speed up polynomial matching (5~65Hz)
        if fs > 20000:
            step = int(fs // 10000)
            waveform = waveform[::step]
            fs = fs / step

        if len(waveform) % 2 != 0:
            waveform = waveform[:-1]

        # Fit modulation detection threshold
        SF = np.array([5, 10, 15, 20, 25, 40, 60], dtype=float)
        ST = np.array([0.846, 0.568, 0.542, 0.759, 1.198, 5.677, 56.157], dtype=float) / 100.0
        poly = Polynomial.fit(SF, ST, deg=5)

        X = np.abs(np.fft.fft(waveform))
        if X[0] < 1e-9:
            return 0.0

        M = 2.0 * X[:len(X) // 2 + 1] / X[0]
        f = (fs / 2.0) * np.linspace(0, 1, len(M))

        mdth = np.full_like(f, np.nan)
        mask = (f >= 5.0) & (f <= 65.0)
        mdth[mask] = poly(f[mask])

        with np.errstate(invalid='ignore', divide='ignore'):
            mpk = M / mdth
        mpk[np.isnan(mdth)] = 0.0
        mpk[np.isinf(mpk)] = 0.0

        mp = float(np.sqrt(np.sum(mpk ** 2)))
        return round(mp, 3)

    @staticmethod
    def calc_ja10(waveform: np.ndarray, fs: float) -> Dict[int, float]:
        """
        Calculate California Title 24 JA10 percent amplitude modulation at
        cut-off frequencies [40, 90, 200, 400, 1000] Hz.
        """
        results: Dict[int, float] = {}
        if len(waveform) < 64:
            return results

        # Decimate if fs > 40k to accelerate IFFT
        if fs > 40000:
            step = int(fs // 20000)
            waveform = waveform[::step]
            fs = fs / step

        n = len(waveform)
        freq_data = np.fft.fft(waveform)
        freqs = np.fft.fftfreq(n, 1.0 / fs)

        thresholds = [40, 90, 200, 400, 1000]
        for th in thresholds:
            f_copy = freq_data.copy()
            f_copy[np.abs(freqs) > th] = 0.0
            filtered = np.real(np.fft.ifft(f_copy))
            f_max = float(np.max(filtered))
            f_min = float(np.min(filtered))
            denom = f_max + f_min
            mod = (100.0 * (f_max - f_min) / denom) if denom > 1e-9 else 0.0
            results[th] = round(float(mod), 2)

        return results

    @staticmethod
    def calc_pst_lm(waveform: np.ndarray, fs: float) -> Tuple[Optional[float], Optional[float]]:
        """
        Calculate IEC TR 61547-1 PstLM (Short-term light flickermeter metric).
        Optimized with scipy.signal.lfilter for rapid execution.
        """
        if len(waveform) < int(fs * 2.0):  # At least 2 seconds needed for any basic estimate
            return None, None

        # Decimate to ~4000 Hz for standard flickermeter processing speed
        if fs > 8000:
            step = int(fs // 4000)
            waveform = waveform[::step]
            fs = fs / step

        u_0 = waveform / np.mean(waveform)

        # High-pass filter 0.05 Hz
        b_hp, a_hp = butter(1, 0.05 / (fs / 2.0), 'high')
        u_hp = lfilter(b_hp, a_hp, u_0 - np.mean(u_0))
        smooth_limit = min(int(np.round(fs / 10.0)), len(u_hp))
        if smooth_limit > 0:
            u_hp[:smooth_limit] *= np.linspace(0, 1, smooth_limit)

        # Low-pass filter 35 Hz
        b_bw, a_bw = butter(6, 35.0 / (fs / 2.0), 'low')
        u_bw = lfilter(b_bw, a_bw, u_hp)

        # Human visual weighting filter (bilinear)
        num1 = [0.041661, 44.758, 2715.6, 29839, 0]
        den1 = [1, 196.32, 11781, 534820, 3505380]
        b_w, a_w = scipy_bilinear(num1, den1, fs)
        u_w = lfilter(b_w, a_w, u_bw)

        # Square and smooth
        SCALING_FACTOR = 1.101603155420234e+06
        u_q = u_w ** 2
        b_lp, a_lp = butter(1, (1.0 / (2.0 * np.pi * 300e-3)) / (fs / 2.0), 'low')
        P_inst = SCALING_FACTOR * lfilter(b_lp, a_lp, u_q)

        # Transient exclusion
        tau_transient = 2.0  # shortened for desktop quick tests if record is short
        n_transient = int(tau_transient * fs)
        if n_transient >= len(P_inst):
            n_transient = int(len(P_inst) * 0.1)

        s = P_inst[n_transient:]
        if len(s) == 0:
            return None, None

        p_inst_max = float(np.max(s))

        NUMOF_CLASSES = 5000
        bin_cnt, edges = np.histogram(s, NUMOF_CLASSES)
        denom_sum = np.sum(bin_cnt)
        if denom_sum == 0:
            return None, None
        cpf_cum_probability = 100.0 * (1.0 - np.cumsum(bin_cnt) / denom_sum)

        def get_percentile(target_prob):
            idx = int(np.argmin(np.abs(cpf_cum_probability - target_prob)))
            return edges[idx]

        p_50s = np.mean([get_percentile(p) for p in [30, 50, 80]])
        p_10s = np.mean([get_percentile(p) for p in [6, 8, 10, 13, 17]])
        p_3s = np.mean([get_percentile(p) for p in [2.2, 3, 4]])
        p_1s = np.mean([get_percentile(p) for p in [0.7, 1, 1.5]])
        p_0_1 = get_percentile(0.1)

        val = 0.0314 * p_0_1 + 0.0525 * p_1s + 0.0657 * p_3s + 0.28 * p_10s + 0.08 * p_50s
        pst = float(np.sqrt(max(0.0, val)))
        return round(pst, 3), round(p_inst_max, 3)

    @classmethod
    def analyze_all(cls, waveform: np.ndarray, fs: float, duration: float) -> FlickerResult:
        """Run complete flicker analysis on a given waveform dataset."""
        res = FlickerResult()
        res.sample_count = len(waveform)
        res.duration_sec = duration

        if len(waveform) < 16:
            return res

        # 1. Percent Flicker & FI
        res.flicker_percent, res.flicker_index = cls.calc_flicker_percent_and_index(waveform)

        # 2. SVM & Dominant Freq & Spectrum
        res.svm, res.fft_freqs, res.fft_mags, res.dominant_frequency = cls.calc_svm(waveform, duration)

        # 3. LRC Mp
        res.mp = cls.calc_mp(waveform, fs)

        # 4. JA10
        res.ja10 = cls.calc_ja10(waveform, fs)

        # 5. PstLM
        res.pst_lm, res.p_inst_max = cls.calc_pst_lm(waveform, fs)

        return res
