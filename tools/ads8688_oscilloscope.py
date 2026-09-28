#!/usr/bin/env python3
"""
ADS8688 High-Speed Real-Time Digital Oscilloscope & Optical Flicker Compliance Analyzer
Designed for ESP32-S3 + ADS8688 (220 kSPS Sampling Rate).
Integrates:
- Tab 1: Real-time Oscilloscope & FFT Spectrum Analyzer (60 FPS, Edge Trigger)
- Tab 2: Optical Flicker Compliance Analyzer (SVM, Percent Flicker, FI, Mp, JA10, PstLM)
- Data Processing Engine: Rule-based extensible pipeline for pre-filtering and calibration.
Tech Stack: Python 3, PyQt6, PyQtGraph, NumPy, SciPy, PySerial.
"""

import sys
import time
import struct
import collections
from typing import Optional, Dict, Any
import numpy as np
import serial
import serial.tools.list_ports

from PyQt6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from algorithm import DataProcessingEngine, FlickerCore, FlickerResult

# Configure PyQtGraph performance options
pg.setConfigOptions(antialias=False, useOpenGL=True)

# Protocol constants
MAGIC_HEAD = 0x55AA
MAGIC_TAIL = 0xAA55
HEADER_FORMAT = "<HBBH"  # uint16 magic, uint8 type, uint8 seq, uint16 len
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
FOOTER_FORMAT = "<HH"    # uint16 checksum, uint16 magic_tail
FOOTER_SIZE = struct.calcsize(FOOTER_FORMAT)

SAMPLING_RATE_DEFAULT = 220000  # 220 kSPS
BUFFER_CAPACITY = 220000 * 5    # Store up to 5 seconds of full-speed history (1.1M samples)


class FastSerialWorker(QtCore.QThread):
    """
    Dedicated high-priority background worker for high-speed serial streaming.
    Decoupled from GUI rendering to prevent buffer overrun at 500 KB/s.
    """
    data_received = QtCore.pyqtSignal(np.ndarray)
    stats_updated = QtCore.pyqtSignal(float, float, int)  # speed_kb, fps, dropped_frames
    error_occurred = QtCore.pyqtSignal(str)

    def __init__(self, port_name, baudrate=115200):
        super().__init__()
        self.port_name = port_name
        self.baudrate = baudrate
        self.running = False
        self.ser = None

    def run(self):
        try:
            self.ser = serial.Serial()
            self.ser.port = self.port_name
            self.ser.baudrate = self.baudrate
            self.ser.timeout = 0.2

            # Windows usbser.sys error 31 workaround
            orig_reconf = self.ser._reconfigure_port
            def safe_reconfigure_port():
                try:
                    orig_reconf()
                except Exception:
                    pass
            self.ser._reconfigure_port = safe_reconfigure_port

            self.ser.open()
            try:
                self.ser.dtr = True
                self.ser.rts = True
            except Exception:
                pass

            self.ser.reset_input_buffer()
        except Exception as e:
            self.error_occurred.emit(f"Failed to open {self.port_name}: {e}")
            return

        self.running = True
        rx_buffer = bytearray()
        last_seq = None
        total_lost_frames = 0
        total_bytes = 0

        last_stat_time = time.time()
        bytes_since_last_stat = 0
        frames_since_last_stat = 0

        while self.running:
            try:
                in_waiting = self.ser.in_waiting
                chunk_size = max(in_waiting, 4096)
                chunk = self.ser.read(chunk_size)
                if not chunk:
                    continue

                rx_buffer.extend(chunk)
                chunk_len = len(chunk)
                total_bytes += chunk_len
                bytes_since_last_stat += chunk_len

                new_samples = []

                # Frame parsing
                while len(rx_buffer) >= (HEADER_SIZE + FOOTER_SIZE):
                    head_idx = rx_buffer.find(b'\xaa\x55')
                    if head_idx == -1:
                        rx_buffer = rx_buffer[-1:]
                        break

                    if head_idx > 0:
                        del rx_buffer[:head_idx]

                    if len(rx_buffer) < HEADER_SIZE:
                        break

                    magic, pkt_type, seq, payload_len = struct.unpack(HEADER_FORMAT, rx_buffer[:HEADER_SIZE])
                    full_len = HEADER_SIZE + payload_len + FOOTER_SIZE

                    if len(rx_buffer) < full_len:
                        break

                    frame = rx_buffer[:full_len]
                    checksum_rx, tail = struct.unpack_from(FOOTER_FORMAT, frame, HEADER_SIZE + payload_len)

                    if tail == MAGIC_TAIL:
                        # Sequence counter check
                        if last_seq is not None:
                            expected_seq = (last_seq + 1) & 0xFF
                            if seq != expected_seq:
                                lost = (seq - expected_seq) & 0xFF
                                total_lost_frames += lost
                        last_seq = seq
                        frames_since_last_stat += 1

                        payload = frame[HEADER_SIZE : HEADER_SIZE + payload_len]
                        samples = np.frombuffer(payload, dtype="<u2")
                        new_samples.append(samples)

                    del rx_buffer[:full_len]

                if new_samples:
                    merged = np.concatenate(new_samples)
                    self.data_received.emit(merged)

                # Periodic link statistics (2 Hz)
                now = time.time()
                elapsed = now - last_stat_time
                if elapsed >= 0.5:
                    speed_kb = (bytes_since_last_stat / 1024.0) / elapsed
                    fps = frames_since_last_stat / elapsed
                    self.stats_updated.emit(speed_kb, fps, total_lost_frames)
                    last_stat_time = now
                    bytes_since_last_stat = 0
                    frames_since_last_stat = 0

            except Exception as e:
                if self.running:
                    self.error_occurred.emit(f"Communication error: {e}")
                break

        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass

    def stop(self):
        self.running = False
        self.wait(1000)


class FlickerAnalysisWorker(QtCore.QThread):
    """Asynchronous background worker for intensive flicker computation."""
    finished = QtCore.pyqtSignal(FlickerResult, np.ndarray)
    error = QtCore.pyqtSignal(str)

    def __init__(self, data: np.ndarray, fs: float, duration: float):
        super().__init__()
        self.data = data
        self.fs = fs
        self.duration = duration

    def run(self):
        try:
            result = FlickerCore.analyze_all(self.data, self.fs, self.duration)
            self.finished.emit(result, self.data)
        except Exception as e:
            self.error.emit(str(e))


class OscilloscopeWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ADS8688 High-Speed Scope & Optical Flicker Analyzer (220 kSPS)")
        self.resize(1360, 850)

        # Core Engines
        self.data_engine = DataProcessingEngine()

        # Signal buffer (5 seconds ring capacity)
        self.sample_rate = SAMPLING_RATE_DEFAULT
        self.voltage_scale_mode = "±5.12V (Calibrated)"
        self.data_ring = np.zeros(BUFFER_CAPACITY, dtype=np.float32)
        self.ring_head = 0
        self.total_samples_collected = 0

        # Scope display parameters
        self.is_running = True
        self.trigger_mode = "Auto"      # Auto, Normal, Single
        self.trigger_edge = "Rising"    # Rising, Falling
        self.trigger_level = 1.65       # Volts
        self.timebase_ms = 10.0         # 10ms visible time window
        self.single_triggered = False

        self.worker = None
        self.flicker_worker = None
        self.last_flicker_result: Optional[FlickerResult] = None
        self.last_flicker_data: Optional[np.ndarray] = None

        # Dedicated live forward sampling for Tab 2 flicker testing
        self.is_collecting_flicker = False
        self.flicker_target_duration = 1.0
        self.flicker_target_samples = int(1.0 * SAMPLING_RATE_DEFAULT)
        self.flicker_collected_buffer = []
        self.flicker_collected_count = 0

        self._init_ui()
        self._init_timer()
        self.refresh_ports()

    def _init_ui(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #16181d; }
            QWidget { color: #e2e8f0; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; }
            QTabWidget::pane { border: 1px solid #2d3748; background-color: #1a202c; border-radius: 6px; }
            QTabBar::tab { background: #232a3b; color: #a0aec0; padding: 10px 24px; font-weight: bold; border-top-left-radius: 6px; border-top-right-radius: 6px; margin-right: 2px; }
            QTabBar::tab:selected { background: #1a202c; color: #63b3ed; border-bottom: 2px solid #63b3ed; }
            QGroupBox { border: 1px solid #2d3748; border-radius: 6px; margin-top: 10px; font-weight: bold; padding-top: 14px; background-color: #171923; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #63b3ed; }
            QPushButton { background-color: #2b354b; border: 1px solid #3c4866; border-radius: 5px; padding: 7px 14px; font-weight: bold; }
            QPushButton:hover { background-color: #3b4a68; }
            QPushButton:pressed { background-color: #1f2738; }
            QPushButton:checked { background-color: #3182ce; border-color: #63b3ed; }
            QComboBox, QSpinBox, QDoubleSpinBox { background-color: #1a202c; border: 1px solid #3c4866; border-radius: 4px; padding: 4px; }
            QLabel { font-size: 12px; }
        """)

        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QtWidgets.QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(12)

        # ---------------- Center Tabs ----------------
        self.tab_widget = QtWidgets.QTabWidget()
        
        # Tab 1: Oscilloscope & Spectrum
        tab_scope = QtWidgets.QWidget()
        tab_scope_layout = QtWidgets.QVBoxLayout(tab_scope)
        tab_scope_layout.setContentsMargins(6, 6, 6, 6)

        self.plot_widget = pg.PlotWidget(title="Channel 1 (AIN0) - Real-time Time Domain Waveform")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel('bottom', "Time", units='ms')
        self.plot_widget.setLabel('left', "Voltage", units='V')
        self.plot_widget.setYRange(0.0, 3.5, padding=0.1)
        self.curve = self.plot_widget.plot(pen=pg.mkPen(color='#00ffcc', width=1.5))

        self.trig_line = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen(color='#ffaa00', style=QtCore.Qt.PenStyle.DashLine, width=1.5))
        self.trig_line.setValue(self.trigger_level)
        self.trig_line.sigPositionChanged.connect(self._on_trig_line_dragged)
        self.plot_widget.addItem(self.trig_line)

        self.fft_widget = pg.PlotWidget(title="Frequency Domain Spectrum (FFT)")
        self.fft_widget.showGrid(x=True, y=True, alpha=0.3)
        self.fft_widget.setLabel('bottom', "Frequency", units='Hz')
        self.fft_widget.setLabel('left', "Amplitude", units='V')
        self.fft_curve = self.fft_widget.plot(pen=pg.mkPen(color='#ff66cc', width=1.5))
        self.fft_widget.setMaximumHeight(230)

        tab_scope_layout.addWidget(self.plot_widget, stretch=3)
        tab_scope_layout.addWidget(self.fft_widget, stretch=1)
        self.tab_widget.addTab(tab_scope, "📊 实时示波器 & 频谱 (Oscilloscope)")

        # Tab 2: Flicker Compliance Analyzer
        tab_flicker = self._build_flicker_tab()
        self.tab_widget.addTab(tab_flicker, "💡 频闪合规测试仪 (Flicker Analyzer)")

        main_layout.addWidget(self.tab_widget, stretch=4)

        # ---------------- Right Sidebar ----------------
        sidebar = QtWidgets.QVBoxLayout()
        sidebar.setSpacing(10)

        # 1. Connection Group
        conn_group = QtWidgets.QGroupBox("Serial Connection")
        conn_layout = QtWidgets.QGridLayout(conn_group)
        conn_layout.addWidget(QtWidgets.QLabel("Port:"), 0, 0)
        self.port_combo = QtWidgets.QComboBox()
        conn_layout.addWidget(self.port_combo, 0, 1)
        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        conn_layout.addWidget(self.btn_refresh, 0, 2)

        conn_layout.addWidget(QtWidgets.QLabel("Baud:"), 1, 0)
        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["115200", "921600", "2000000", "3000000"])
        self.baud_combo.setCurrentText("115200")
        conn_layout.addWidget(self.baud_combo, 1, 1, 1, 2)

        self.btn_connect = QtWidgets.QPushButton("Connect")
        self.btn_connect.setStyleSheet("background-color: #238636; color: white;")
        self.btn_connect.clicked.connect(self.toggle_connection)
        conn_layout.addWidget(self.btn_connect, 2, 0, 1, 3)
        sidebar.addWidget(conn_group)

        # 2. Scope Acquisition Controls
        ctrl_group = QtWidgets.QGroupBox("Acquisition & Scale")
        ctrl_layout = QtWidgets.QGridLayout(ctrl_group)
        self.btn_run_stop = QtWidgets.QPushButton("STOP")
        self.btn_run_stop.setCheckable(True)
        self.btn_run_stop.clicked.connect(self.toggle_run_stop)
        ctrl_layout.addWidget(self.btn_run_stop, 0, 0, 1, 2)

        ctrl_layout.addWidget(QtWidgets.QLabel("Time Window (ms):"), 1, 0)
        self.spin_timebase = QtWidgets.QDoubleSpinBox()
        self.spin_timebase.setRange(0.5, 2000.0)
        self.spin_timebase.setValue(10.0)
        self.spin_timebase.setSingleStep(5.0)
        self.spin_timebase.valueChanged.connect(self._on_timebase_changed)
        ctrl_layout.addWidget(self.spin_timebase, 1, 1)

        ctrl_layout.addWidget(QtWidgets.QLabel("Scale Calib:"), 2, 0)
        self.scale_combo = QtWidgets.QComboBox()
        self.scale_combo.addItems(["±5.12V (Calibrated)", "±5.12V (Raw Bipolar)", "Raw Code (0-65535)"])
        self.scale_combo.currentIndexChanged.connect(self._on_scale_changed)
        ctrl_layout.addWidget(self.scale_combo, 2, 1)
        sidebar.addWidget(ctrl_group)

        # 3. Data Processing Rules Control (Engine)
        engine_group = QtWidgets.QGroupBox("Data Processing Engine (规则引擎)")
        engine_layout = QtWidgets.QVBoxLayout(engine_group)
        self.chk_zeroclip = QtWidgets.QCheckBox("启用光信号去负截断 (ZeroClip)")
        self.chk_zeroclip.setChecked(True)
        self.chk_zeroclip.toggled.connect(lambda v: self.data_engine.set_rule_enabled("ZeroClip", v))
        engine_layout.addWidget(self.chk_zeroclip)

        self.chk_custom_rule = QtWidgets.QCheckBox("启用预留开发规则 (UserCustomSlot)")
        self.chk_custom_rule.setChecked(False)
        self.chk_custom_rule.toggled.connect(lambda v: self.data_engine.set_rule_enabled("UserRuleSlot", v))
        engine_layout.addWidget(self.chk_custom_rule)
        sidebar.addWidget(engine_group)

        # 4. Trigger Engine
        trig_group = QtWidgets.QGroupBox("Trigger Engine")
        trig_layout = QtWidgets.QGridLayout(trig_group)
        trig_layout.addWidget(QtWidgets.QLabel("Mode:"), 0, 0)
        self.trig_mode_combo = QtWidgets.QComboBox()
        self.trig_mode_combo.addItems(["Auto", "Normal", "Single"])
        self.trig_mode_combo.currentTextChanged.connect(self._on_trig_mode_changed)
        trig_layout.addWidget(self.trig_mode_combo, 0, 1)

        trig_layout.addWidget(QtWidgets.QLabel("Edge:"), 1, 0)
        self.trig_edge_combo = QtWidgets.QComboBox()
        self.trig_edge_combo.addItems(["Rising", "Falling"])
        self.trig_edge_combo.currentTextChanged.connect(self._on_trig_edge_changed)
        trig_layout.addWidget(self.trig_edge_combo, 1, 1)

        trig_layout.addWidget(QtWidgets.QLabel("Level (V):"), 2, 0)
        self.spin_trig_level = QtWidgets.QDoubleSpinBox()
        self.spin_trig_level.setRange(-10.0, 10.0)
        self.spin_trig_level.setValue(1.65)
        self.spin_trig_level.setSingleStep(0.05)
        self.spin_trig_level.valueChanged.connect(self._on_trig_spin_changed)
        trig_layout.addWidget(self.spin_trig_level, 2, 1)

        self.btn_force_trig = QtWidgets.QPushButton("Force Trigger")
        self.btn_force_trig.clicked.connect(self._force_trigger)
        trig_layout.addWidget(self.btn_force_trig, 3, 0, 1, 2)
        sidebar.addWidget(trig_group)

        # 5. Live Measurements
        meas_group = QtWidgets.QGroupBox("Live Scope Measurements")
        meas_layout = QtWidgets.QGridLayout(meas_group)
        self.lbl_vpp = QtWidgets.QLabel("Vpp:  --")
        self.lbl_vmax = QtWidgets.QLabel("Vmax: --")
        self.lbl_vmin = QtWidgets.QLabel("Vmin: --")
        self.lbl_vavg = QtWidgets.QLabel("Vavg: --")
        self.lbl_freq = QtWidgets.QLabel("Freq: --")
        font = QtGui.QFont("Courier New", 10, QtGui.QFont.Weight.Bold)
        for lbl in [self.lbl_vpp, self.lbl_vmax, self.lbl_vmin, self.lbl_vavg, self.lbl_freq]:
            lbl.setFont(font)
            lbl.setStyleSheet("color: #58a6ff;")
        meas_layout.addWidget(self.lbl_vpp, 0, 0)
        meas_layout.addWidget(self.lbl_vavg, 0, 1)
        meas_layout.addWidget(self.lbl_vmax, 1, 0)
        meas_layout.addWidget(self.lbl_vmin, 1, 1)
        meas_layout.addWidget(self.lbl_freq, 2, 0, 1, 2)
        sidebar.addWidget(meas_group)

        # 6. Throughput & Status
        stat_group = QtWidgets.QGroupBox("Link Status")
        stat_layout = QtWidgets.QVBoxLayout(stat_group)
        self.lbl_throughput = QtWidgets.QLabel("Speed: 0.0 KB/s")
        self.lbl_fps = QtWidgets.QLabel("Packet Rate: 0 FPS")
        self.lbl_lost = QtWidgets.QLabel("Lost Packets: 0")
        stat_layout.addWidget(self.lbl_throughput)
        stat_layout.addWidget(self.lbl_fps)
        stat_layout.addWidget(self.lbl_lost)
        sidebar.addWidget(stat_group)

        # Export & Save
        self.btn_export = QtWidgets.QPushButton("Export Captured Waveform (CSV)")
        self.btn_export.clicked.connect(self.export_csv)
        sidebar.addWidget(self.btn_export)

        sidebar.addStretch()
        main_layout.addLayout(sidebar, stretch=1)

    def _build_flicker_tab(self) -> QtWidgets.QWidget:
        """Construct the dedicated Tab 2 for Optical Flicker Compliance Analysis."""
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # 1. Top Control Bar
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.addWidget(QtWidgets.QLabel("<b>采集时长 (Capture Duration):</b>"))
        self.combo_flicker_dur = QtWidgets.QComboBox()
        self.combo_flicker_dur.addItems(["1.0 秒 (快速评估)", "2.0 秒 (推荐准度)", "5.0 秒 (深度测量)"])
        self.combo_flicker_dur.setCurrentIndex(0)
        top_bar.addWidget(self.combo_flicker_dur)

        self.btn_run_flicker = QtWidgets.QPushButton("⚡ 开始频闪捕获与分析 (Capture & Analyze)")
        self.btn_run_flicker.setStyleSheet("background-color: #2b6cb0; color: white; font-size: 13px; padding: 6px 18px;")
        self.btn_run_flicker.clicked.connect(self.start_flicker_analysis)
        top_bar.addWidget(self.btn_run_flicker)

        self.btn_export_flicker = QtWidgets.QPushButton("📄 导出频闪报告 (Export CSV)")
        self.btn_export_flicker.clicked.connect(self.export_flicker_report)
        top_bar.addWidget(self.btn_export_flicker)

        # Progress bar for live capture countdown
        self.progress_flicker = QtWidgets.QProgressBar()
        self.progress_flicker.setRange(0, 100)
        self.progress_flicker.setValue(0)
        self.progress_flicker.setTextVisible(True)
        self.progress_flicker.setFixedWidth(160)
        self.progress_flicker.setStyleSheet("""
            QProgressBar { border: 1px solid #4a5568; border-radius: 4px; text-align: center; background-color: #2d3748; color: white; }
            QProgressBar::chunk { background-color: #319795; }
        """)
        self.progress_flicker.setVisible(False)
        top_bar.addWidget(self.progress_flicker)

        self.lbl_flicker_status = QtWidgets.QLabel("状态: 就绪 (等待捕获)")
        self.lbl_flicker_status.setStyleSheet("color: #a0aec0; font-style: italic;")
        top_bar.addWidget(self.lbl_flicker_status)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # 2. Main Middle Display (Left: Plots, Right: Compliance Indicators)
        mid_splitter = QtWidgets.QHBoxLayout()

        # Visual Plots
        plot_box = QtWidgets.QVBoxLayout()
        self.flicker_plot_wave = pg.PlotWidget(title="光信号完整波形 (Captured Light Waveform)")
        self.flicker_plot_wave.showGrid(x=True, y=True, alpha=0.3)
        self.flicker_plot_wave.setLabel('bottom', "Time", units='s')
        self.flicker_plot_wave.setLabel('left', "Light Signal", units='V')
        self.flicker_curve_wave = self.flicker_plot_wave.plot(pen=pg.mkPen(color='#38b2ac', width=1.2))

        self.flicker_plot_spec = pg.PlotWidget(title="频闪调制频谱 (Modulation Spectrum up to 2000Hz)")
        self.flicker_plot_spec.showGrid(x=True, y=True, alpha=0.3)
        self.flicker_plot_spec.setLabel('bottom', "Frequency", units='Hz')
        self.flicker_plot_spec.setLabel('left', "Modulation / Sensitivity", units='')
        self.flicker_curve_spec = self.flicker_plot_spec.plot(pen=pg.mkPen(color='#f6ad55', width=1.5))
        self.flicker_curve_sens = self.flicker_plot_spec.plot(pen=pg.mkPen(color='#fc8181', width=1.2, style=QtCore.Qt.PenStyle.DashLine))

        plot_box.addWidget(self.flicker_plot_wave, stretch=3)
        plot_box.addWidget(self.flicker_plot_spec, stretch=2)
        mid_splitter.addLayout(plot_box, stretch=3)

        # Right Side: Multi-standard Compliance Cards
        cards_widget = QtWidgets.QWidget()
        cards_layout = QtWidgets.QVBoxLayout(cards_widget)
        cards_layout.setContentsMargins(4, 0, 4, 0)
        cards_layout.setSpacing(8)

        # Card 1: Basic Flicker Index
        grp_basic = QtWidgets.QGroupBox("1. 基础频闪度量 (Basic Metrics)")
        lay_basic = QtWidgets.QGridLayout(grp_basic)
        self.val_pfl = QtWidgets.QLabel("-- %")
        self.val_fi = QtWidgets.QLabel("--")
        self.val_dom_freq = QtWidgets.QLabel("-- Hz")
        for v in [self.val_pfl, self.val_fi, self.val_dom_freq]:
            v.setFont(QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.Bold))
            v.setStyleSheet("color: #63b3ed;")
        lay_basic.addWidget(QtWidgets.QLabel("波动深度 (Percent Flicker):"), 0, 0)
        lay_basic.addWidget(self.val_pfl, 0, 1)
        lay_basic.addWidget(QtWidgets.QLabel("闪烁指数 (Flicker Index):"), 1, 0)
        lay_basic.addWidget(self.val_fi, 1, 1)
        lay_basic.addWidget(QtWidgets.QLabel("主频闪频率 (Dominant Freq):"), 2, 0)
        lay_basic.addWidget(self.val_dom_freq, 2, 1)
        cards_layout.addWidget(grp_basic)

        # Card 2: CIE TN 006 / EU 2019/2020 SVM
        grp_svm = QtWidgets.QGroupBox("2. 频闪效应可视度 SVM (CIE TN 006)")
        lay_svm = QtWidgets.QVBoxLayout(grp_svm)
        self.val_svm = QtWidgets.QLabel("SVM: --")
        self.val_svm.setFont(QtGui.QFont("Segoe UI", 14, QtGui.QFont.Weight.Bold))
        self.val_svm.setStyleSheet("color: #68d391;")
        self.lbl_svm_status = QtWidgets.QLabel("欧盟生态设计 ErP 要求: SVM ≤ 0.4 (免除频闪效应)")
        self.lbl_svm_status.setStyleSheet("color: #a0aec0; font-size: 11px;")
        lay_svm.addWidget(self.val_svm)
        lay_svm.addWidget(self.lbl_svm_status)
        cards_layout.addWidget(grp_svm)

        # Card 3: LRC Mp & IEC PstLM
        grp_adv = QtWidgets.QGroupBox("3. 感知指标 (Mp & PstLM)")
        lay_adv = QtWidgets.QGridLayout(grp_adv)
        self.val_mp = QtWidgets.QLabel("--")
        self.val_pst = QtWidgets.QLabel("--")
        for v in [self.val_mp, self.val_pst]:
            v.setFont(QtGui.QFont("Segoe UI", 11, QtGui.QFont.Weight.Bold))
            v.setStyleSheet("color: #d6bcfa;")
        lay_adv.addWidget(QtWidgets.QLabel("LRC 频闪效应 Mp:"), 0, 0)
        lay_adv.addWidget(self.val_mp, 0, 1)
        lay_adv.addWidget(QtWidgets.QLabel("短时闪烁度 PstLM (IEC):"), 1, 0)
        lay_adv.addWidget(self.val_pst, 1, 1)
        cards_layout.addWidget(grp_adv)

        # Card 4: California Title 24 JA10
        grp_ja10 = QtWidgets.QGroupBox("4. 加州 Title 24 JA10 截止滤波调制深度")
        lay_ja10 = QtWidgets.QGridLayout(grp_ja10)
        self.ja10_labels: Dict[int, QtWidgets.QLabel] = {}
        thresholds = [40, 90, 200, 400, 1000]
        for idx, th in enumerate(thresholds):
            lbl = QtWidgets.QLabel(f"≤ {th} Hz:")
            val = QtWidgets.QLabel("-- %")
            val.setFont(QtGui.QFont("Courier New", 10, QtGui.QFont.Weight.Bold))
            val.setStyleSheet("color: #fbd38d;")
            lay_ja10.addWidget(lbl, idx, 0)
            lay_ja10.addWidget(val, idx, 1)
            self.ja10_labels[th] = val
        cards_layout.addWidget(grp_ja10)

        cards_layout.addStretch()
        mid_splitter.addWidget(cards_widget, stretch=2)
        layout.addLayout(mid_splitter)

        return tab

    def _init_timer(self):
        # 60 FPS UI rendering timer (~16ms)
        self.render_timer = QtCore.QTimer()
        self.render_timer.timeout.connect(self.update_scope_display)
        self.render_timer.start(16)

    def refresh_ports(self):
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.port_combo.addItem(f"{p.device} ({p.description})", p.device)

    def toggle_connection(self):
        if self.worker is None:
            port = self.port_combo.currentData()
            if not port:
                QtWidgets.QMessageBox.warning(self, "Warning", "Please select a serial port!")
                return
            baud = int(self.baud_combo.currentText())

            self.worker = FastSerialWorker(port, baud)
            self.worker.data_received.connect(self.on_raw_data_received)
            self.worker.stats_updated.connect(self.on_stats_updated)
            self.worker.error_occurred.connect(self.on_worker_error)
            self.worker.start()

            self.btn_connect.setText("Disconnect")
            self.btn_connect.setStyleSheet("background-color: #da3633; color: white;")
            self.port_combo.setEnabled(False)
            self.baud_combo.setEnabled(False)
        else:
            self.worker.stop()
            self.worker = None
            self.btn_connect.setText("Connect")
            self.btn_connect.setStyleSheet("background-color: #238636; color: white;")
            self.port_combo.setEnabled(True)
            self.baud_combo.setEnabled(True)

    def toggle_run_stop(self):
        self.is_running = not self.btn_run_stop.isChecked()
        if self.is_running:
            self.btn_run_stop.setText("STOP")
            self.btn_run_stop.setStyleSheet("")
            self.single_triggered = False
        else:
            self.btn_run_stop.setText("RUN")
            self.btn_run_stop.setStyleSheet("background-color: #d29922; color: black; font-weight: bold;")

    def _on_timebase_changed(self, val):
        self.timebase_ms = val

    def _on_scale_changed(self, idx):
        self.voltage_scale_mode = self.scale_combo.currentText()
        if "Raw Code" in self.voltage_scale_mode:
            self.plot_widget.setYRange(0, 65535, padding=0.1)
            self.plot_widget.setLabel('left', "Raw ADC Code", units='LSB')
        else:
            self.plot_widget.setYRange(-1.0, 4.0, padding=0.1)
            self.plot_widget.setLabel('left', "Voltage", units='V')

    def _on_trig_mode_changed(self, mode):
        self.trigger_mode = mode
        self.single_triggered = False

    def _on_trig_edge_changed(self, edge):
        self.trigger_edge = edge

    def _on_trig_spin_changed(self, val):
        self.trigger_level = val
        self.trig_line.blockSignals(True)
        self.trig_line.setValue(val)
        self.trig_line.blockSignals(False)

    def _on_trig_line_dragged(self):
        val = self.trig_line.value()
        self.trigger_level = val
        self.spin_trig_level.blockSignals(True)
        self.spin_trig_level.setValue(val)
        self.spin_trig_level.blockSignals(False)

    def _force_trigger(self):
        self.single_triggered = False
        if not self.is_running:
            self.toggle_run_stop()

    @QtCore.pyqtSlot(np.ndarray)
    def on_raw_data_received(self, raw_samples):
        # Convert uint16 to voltage based on ADS8688 transfer function
        if "Calibrated" in self.voltage_scale_mode:
            raw_bipolar = ((raw_samples.astype(np.float32) - 32768.0) / 32768.0) * 5.12
            volts = (raw_bipolar + 5.12) / 2.0
        elif "Raw Bipolar" in self.voltage_scale_mode:
            volts = ((raw_samples.astype(np.float32) - 32768.0) / 32768.0) * 5.12
        else:
            volts = raw_samples.astype(np.float32)

        # Apply extensible rule pipeline (DataProcessingEngine)
        processed_volts = self.data_engine.process(volts, self.sample_rate)

        n = len(processed_volts)
        idx = self.ring_head
        if idx + n <= BUFFER_CAPACITY:
            self.data_ring[idx : idx + n] = processed_volts
        else:
            first_part = BUFFER_CAPACITY - idx
            self.data_ring[idx:] = processed_volts[:first_part]
            self.data_ring[: n - first_part] = processed_volts[first_part:]

        self.ring_head = (self.ring_head + n) % BUFFER_CAPACITY
        self.total_samples_collected += n

        # Live forward sampling accumulator for Tab 2 flicker measurement
        if self.is_collecting_flicker:
            self.flicker_collected_buffer.append(processed_volts)
            self.flicker_collected_count += n
            pct = min(100, int((self.flicker_collected_count / self.flicker_target_samples) * 100))
            self.progress_flicker.setValue(pct)
            cur_sec = self.flicker_collected_count / self.sample_rate
            self.lbl_flicker_status.setText(
                f"状态: 📡 实时采样中 ({cur_sec:.1f}s / {self.flicker_target_duration:.1f}s, {pct}%)..."
            )

            if self.flicker_collected_count >= self.flicker_target_samples:
                # Target window collected! Stop accumulation and kick off background worker
                self.is_collecting_flicker = False
                self.progress_flicker.setValue(100)
                full_flicker_data = np.concatenate(self.flicker_collected_buffer)[:self.flicker_target_samples]
                self.flicker_collected_buffer = []

                self.lbl_flicker_status.setText("状态: ⏳ 正在进行多标准频闪运算 (SVM, JA10, Mp, PstLM)...")
                self.lbl_flicker_status.setStyleSheet("color: #ecc94b; font-weight: bold;")

                self.flicker_worker = FlickerAnalysisWorker(full_flicker_data, self.sample_rate, self.flicker_target_duration)
                self.flicker_worker.finished.connect(self._on_flicker_analysis_finished)
                self.flicker_worker.error.connect(self._on_flicker_analysis_error)
                self.flicker_worker.start()

    @QtCore.pyqtSlot(float, float, int)
    def on_stats_updated(self, speed_kb, fps, lost_pkts):
        self.lbl_throughput.setText(f"Speed: {speed_kb:.1f} KB/s")
        self.lbl_fps.setText(f"Frame Rate: {fps:.1f} FPS")
        self.lbl_lost.setText(f"Lost Packets: {lost_pkts}")

    @QtCore.pyqtSlot(str)
    def on_worker_error(self, err_msg):
        QtWidgets.QMessageBox.critical(self, "Hardware Error", err_msg)
        if self.worker:
            self.toggle_connection()

    def update_scope_display(self):
        """Called every ~16ms by Qt GUI timer."""
        # Only update Tab 1 if it is currently selected or active
        if self.tab_widget.currentIndex() != 0:
            return

        if not self.is_running or self.total_samples_collected < 1000:
            return

        pts_count = int((self.timebase_ms / 1000.0) * self.sample_rate)
        pts_count = max(100, min(pts_count, BUFFER_CAPACITY // 2))

        head = self.ring_head
        search_len = pts_count + 4000
        if head >= search_len:
            window = self.data_ring[head - search_len : head]
        else:
            window = np.concatenate((self.data_ring[head - search_len :], self.data_ring[:head]))

        # Trigger detection
        trig_idx = -1
        thresh = self.trigger_level
        if self.trigger_edge == "Rising":
            crossings = np.where((window[:-1] < thresh) & (window[1:] >= thresh))[0]
        else:
            crossings = np.where((window[:-1] > thresh) & (window[1:] <= thresh))[0]

        if len(crossings) > 0:
            valid_crossings = crossings[crossings <= (len(window) - pts_count)]
            if len(valid_crossings) > 0:
                trig_idx = valid_crossings[-1]

        if trig_idx != -1:
            disp_data = window[trig_idx : trig_idx + pts_count]
            if self.trigger_mode == "Single":
                self.btn_run_stop.setChecked(True)
                self.toggle_run_stop()
        else:
            if self.trigger_mode == "Normal":
                return
            disp_data = window[-pts_count:]

        # Intelligent decimation for high point counts
        num_pts = len(disp_data)
        if num_pts > 10000:
            step = int(np.ceil(num_pts / 10000))
            render_y = disp_data[::step]
            time_axis = np.linspace(0, self.timebase_ms, len(render_y), endpoint=False)
            self.curve.setData(time_axis, render_y)
        else:
            time_axis = np.linspace(0, self.timebase_ms, num_pts, endpoint=False)
            self.curve.setData(time_axis, disp_data)

        # Real-time Measurements
        vmax = float(np.max(disp_data))
        vmin = float(np.min(disp_data))
        vpp = vmax - vmin
        vavg = float(np.mean(disp_data))

        self.lbl_vpp.setText(f"Vpp:  {vpp:6.3f} V")
        self.lbl_vmax.setText(f"Vmax: {vmax:6.3f} V")
        self.lbl_vmin.setText(f"Vmin: {vmin:6.3f} V")
        self.lbl_vavg.setText(f"Vavg: {vavg:6.3f} V")

        # Frequency measurement
        freq = None
        if vpp > 0.03:
            high_th = vavg + 0.15 * vpp
            low_th = vavg - 0.15 * vpp
            states = np.zeros(len(disp_data), dtype=np.int8)
            current_state = 1 if disp_data[0] >= vavg else 0
            for i in range(len(disp_data)):
                val = disp_data[i]
                if val >= high_th:
                    current_state = 1
                elif val <= low_th:
                    current_state = 0
                states[i] = current_state
            edges = np.where((states[:-1] == 0) & (states[1:] == 1))[0]
            if len(edges) >= 2:
                diffs = np.diff(edges)
                if len(diffs) > 0:
                    med_period = float(np.median(diffs))
                    if med_period > 0:
                        freq = self.sample_rate / med_period

        # FFT Spectrum
        fft_len = min(8192, len(disp_data))
        if fft_len >= 512:
            fft_chunk = (disp_data[:fft_len] - vavg) * np.hanning(fft_len)
            fft_vals = np.abs(np.fft.rfft(fft_chunk)) / (fft_len / 2)
            fft_freqs = np.fft.rfftfreq(fft_len, d=1.0 / self.sample_rate)
            self.fft_curve.setData(fft_freqs, fft_vals)

            valid_bins = np.where(fft_freqs >= 5.0)[0]
            if len(valid_bins) > 0:
                peak_idx = valid_bins[np.argmax(fft_vals[valid_bins])]
                peak_freq = fft_freqs[peak_idx]
                if freq is None or abs(freq - peak_freq) / (peak_freq + 1e-5) > 0.35:
                    freq = peak_freq

        if freq is not None and freq > 0:
            if freq < 1000:
                self.lbl_freq.setText(f"Freq: {freq:6.1f} Hz")
            else:
                self.lbl_freq.setText(f"Freq: {freq/1000.0:6.2f} kHz")
        else:
            self.lbl_freq.setText("Freq: < No Edge >")

    # ---------------- Tab 2: Optical Flicker Handler ----------------
    def start_flicker_analysis(self):
        """Initiate fresh real-time forward sampling for the selected duration."""
        if not self.worker or not self.worker.running:
            QtWidgets.QMessageBox.warning(
                self, "设备未连接", "请先连接串口并确保正在接收采样数据！"
            )
            return

        dur_str = self.combo_flicker_dur.currentText()
        if "1.0" in dur_str:
            duration = 1.0
        elif "2.0" in dur_str:
            duration = 2.0
        else:
            duration = 5.0

        self.flicker_target_duration = duration
        self.flicker_target_samples = int(duration * self.sample_rate)
        self.flicker_collected_buffer = []
        self.flicker_collected_count = 0

        self.btn_run_flicker.setEnabled(False)
        self.progress_flicker.setValue(0)
        self.progress_flicker.setVisible(True)
        self.lbl_flicker_status.setText(f"状态: 📡 启动新鲜采样 (目标: {duration}s, {self.flicker_target_samples}点)...")
        self.lbl_flicker_status.setStyleSheet("color: #63b3ed; font-weight: bold;")

        # Enable live collection flag
        self.is_collecting_flicker = True

    @QtCore.pyqtSlot(FlickerResult, np.ndarray)
    def _on_flicker_analysis_finished(self, res: FlickerResult, raw_data: np.ndarray):
        self.btn_run_flicker.setEnabled(True)
        self.progress_flicker.setVisible(False)
        self.last_flicker_result = res
        self.last_flicker_data = raw_data
        self.lbl_flicker_status.setText(f"状态: ✅ 分析完成 (耗时样本: {res.sample_count} 点)")
        self.lbl_flicker_status.setStyleSheet("color: #48bb78; font-weight: bold;")

        # Update Basic Cards
        self.val_pfl.setText(f"{res.flicker_percent:.2f} %")
        self.val_fi.setText(f"{res.flicker_index:.4f}")
        self.val_dom_freq.setText(f"{res.dominant_frequency:.1f} Hz")

        # Update SVM Card
        self.val_svm.setText(f"SVM: {res.svm:.3f}")
        if res.svm <= 0.4:
            self.val_svm.setStyleSheet("color: #48bb78; font-size: 16px; font-weight: bold;")
            self.lbl_svm_status.setText("标准判定: 极佳 (SVM ≤ 0.4 符合 EU 2019/2020 最高标准)")
            self.lbl_svm_status.setStyleSheet("color: #48bb78;")
        elif res.svm <= 0.9:
            self.val_svm.setStyleSheet("color: #ecc94b; font-size: 16px; font-weight: bold;")
            self.lbl_svm_status.setText("标准判定: 合格 (SVM ≤ 0.9 在可接受低频闪区间)")
            self.lbl_svm_status.setStyleSheet("color: #ecc94b;")
        else:
            self.val_svm.setStyleSheet("color: #f56565; font-size: 16px; font-weight: bold;")
            self.lbl_svm_status.setText("标准判定: 超标 (SVM > 0.9 存在可见频闪效应风险)")
            self.lbl_svm_status.setStyleSheet("color: #f56565;")

        # Update Mp & PstLM
        self.val_mp.setText(f"{res.mp:.3f}")
        if res.pst_lm is not None:
            self.val_pst.setText(f"{res.pst_lm:.3f}")
        else:
            self.val_pst.setText("数据时长不足 (需>2s)")

        # Update JA10 Labels
        for th, val in res.ja10.items():
            if th in self.ja10_labels:
                self.ja10_labels[th].setText(f"{val:.2f} %")

        # Update Plots
        # 1. Light Waveform (Downsample if needed for smooth plotting)
        dec = max(1, len(raw_data) // 5000)
        plot_y = raw_data[::dec]
        plot_x = np.linspace(0, res.duration_sec, len(plot_y), endpoint=False)
        self.flicker_curve_wave.setData(plot_x, plot_y)

        # 2. Modulation Spectrum & Sensitivity Curve
        if res.fft_freqs is not None and res.fft_mags is not None:
            self.flicker_curve_spec.setData(res.fft_freqs, res.fft_mags)
            sens = FlickerCore._get_sensitivity_strobo(res.fft_freqs)
            self.flicker_curve_sens.setData(res.fft_freqs, sens)

    @QtCore.pyqtSlot(str)
    def _on_flicker_analysis_error(self, err_msg: str):
        self.btn_run_flicker.setEnabled(True)
        self.progress_flicker.setVisible(False)
        self.lbl_flicker_status.setText(f"状态: ❌ 计算错误: {err_msg}")
        self.lbl_flicker_status.setStyleSheet("color: #f56565; font-weight: bold;")
        QtWidgets.QMessageBox.critical(self, "频闪计算异常", f"分析失败: {err_msg}")

    def export_flicker_report(self):
        """Export comprehensive flicker compliance results to CSV report."""
        if self.last_flicker_result is None or self.last_flicker_data is None:
            QtWidgets.QMessageBox.information(self, "提示", "请先执行一次频闪分析再导出报告！")
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出频闪合规报告", "flicker_report.csv", "CSV Files (*.csv)"
        )
        if not path:
            return

        res = self.last_flicker_result
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("# ADS8688 光学频闪测试与认证评估报告\n")
                f.write(f"# 测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 采样率: {self.sample_rate} Hz, 时长: {res.duration_sec} s, 点数: {res.sample_count}\n\n")
                f.write("指标类别,项目名称,测量结果,合格建议阈值\n")
                f.write(f"波动度,Percent Flicker (%),{res.flicker_percent:.2f},IEEE 1789 推荐低风险\n")
                f.write(f"波动度,Flicker Index,{res.flicker_index:.4f},无\n")
                f.write(f"频闪特性,Dominant Frequency (Hz),{res.dominant_frequency:.1f},无\n")
                f.write(f"欧盟标准,CIE SVM (频闪可视度),{res.svm:.3f},<= 0.4 (EU 2019/2020)\n")
                f.write(f"感知模型,LRC Mp,{res.mp:.3f},无\n")
                f.write(f"IEC标准,PstLM,{res.pst_lm if res.pst_lm is not None else 'N/A'},<= 1.0 (IEC 61547)\n")
                for th, mod in res.ja10.items():
                    f.write(f"加州JA10,Percent Mod (<= {th}Hz),{mod:.2f}%,California Title 24\n")

            QtWidgets.QMessageBox.information(self, "导出成功", f"频闪合规测试报告已成功保存至:\n{path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "导出错误", f"保存报告失败: {e}")

    def export_csv(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export Waveform Data", "waveform.csv", "CSV Files (*.csv)")
        if not path:
            return

        pts_count = int((self.timebase_ms / 1000.0) * self.sample_rate)
        head = self.ring_head
        if head >= pts_count:
            data = self.data_ring[head - pts_count : head]
        else:
            data = np.concatenate((self.data_ring[head - pts_count :], self.data_ring[:head]))

        time_axis = np.linspace(0, self.timebase_ms / 1000.0, len(data), endpoint=False)
        table = np.column_stack((time_axis, data))
        np.savetxt(path, table, delimiter=",", header="Time_s,Voltage_V", comments="")
        QtWidgets.QMessageBox.information(self, "Exported", f"Successfully saved {len(data)} points to CSV!")

    def closeEvent(self, event):
        if self.worker:
            self.worker.stop()
        if self.flicker_worker and self.flicker_worker.isRunning():
            self.flicker_worker.terminate()
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    window = OscilloscopeWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
