"""
PDF Report Generator for Optical Flicker Compliance Analysis.
Generates styled PDF reports using PyQt6's native QTextDocument and QPdfWriter.
Does not require external heavy dependencies like reportlab or wkhtmltopdf.
"""

import time
from typing import Optional, Dict, Any
import numpy as np

from PyQt6 import QtCore, QtGui
from PyQt6.QtGui import QTextDocument, QPdfWriter, QPageSize, QPageLayout

from .flicker_core import FlickerResult


class FlickerPdfReportGenerator:
    """
    Renders optical flicker test results into a PDF report.
    Uses HTML5/CSS formatting rendered through PyQt6 QPdfWriter.
    """

    @classmethod
    def generate_pdf(
        cls,
        filepath: str,
        result: FlickerResult,
        sample_rate: float,
        extra_meta: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Generate and save PDF report to the specified file path.
        
        :param filepath: Output PDF file path (e.g. 'report.pdf')
        :param result: FlickerResult instance computed by FlickerCore
        :param sample_rate: Acquisition sampling rate in Hz
        :param extra_meta: Optional dict with test equipment/operator info
        :return: True if generation succeeded, False otherwise
        """
        try:
            printer = QPdfWriter(filepath)
            printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            printer.setPageOrientation(QPageLayout.Orientation.Portrait)
            # 12mm page margins
            printer.setPageMargins(QtCore.QMarginsF(12, 12, 12, 12), QPageLayout.Unit.Millimeter)
            printer.setResolution(300)

            html_content = cls._build_html(result, sample_rate, extra_meta)

            doc = QTextDocument()
            doc.setDefaultStyleSheet(cls._get_stylesheet())
            doc.setHtml(html_content)
            doc.print(printer)
            return True
        except Exception as e:
            print(f"[FlickerPdfReportGenerator] PDF generation failed: {e}")
            raise e

    @classmethod
    def _get_stylesheet(cls) -> str:
        return """
            body {
                font-family: 'Microsoft YaHei', 'Segoe UI', Arial, sans-serif;
                color: #2d3748;
                font-size: 11pt;
                line-height: 1.4;
            }
            h1 {
                color: #1a365d;
                font-size: 19pt;
                font-weight: bold;
                text-align: center;
                margin-bottom: 4px;
            }
            .subtitle {
                text-align: center;
                color: #718096;
                font-size: 9.5pt;
                margin-bottom: 16px;
            }
            .meta-table {
                width: 100%;
                margin-bottom: 14px;
                border-collapse: collapse;
                background-color: #f7fafc;
                border: 1px solid #e2e8f0;
            }
            .meta-table td {
                padding: 6px 10px;
                font-size: 9.5pt;
                border: 1px solid #e2e8f0;
            }
            .meta-label {
                font-weight: bold;
                color: #4a5568;
                width: 18%;
                background-color: #edf2f7;
            }
            .meta-val {
                width: 32%;
                color: #2b6cb0;
                font-family: 'Consolas', monospace;
            }
            .section-title {
                color: #2b6cb0;
                font-size: 12pt;
                font-weight: bold;
                border-bottom: 2px solid #3182ce;
                padding-bottom: 3px;
                margin-top: 14px;
                margin-bottom: 8px;
            }
            table.data-table {
                width: 100%;
                border-collapse: collapse;
                margin-bottom: 12px;
            }
            table.data-table th {
                background-color: #2b6cb0;
                color: #ffffff;
                font-weight: bold;
                font-size: 9.5pt;
                text-align: left;
                padding: 6px 8px;
                border: 1px solid #2b6cb0;
            }
            table.data-table td {
                padding: 5px 8px;
                border: 1px solid #cbd5e0;
                font-size: 9pt;
            }
            tr:nth-child(even) {
                background-color: #f8fafc;
            }
            .pass-tag {
                color: #2f855a;
                font-weight: bold;
            }
            .warn-tag {
                color: #c53030;
                font-weight: bold;
            }
            .footer-note {
                font-size: 8pt;
                color: #a0aec0;
                text-align: center;
                margin-top: 18px;
                border-top: 1px dashed #cbd5e0;
                padding-top: 6px;
            }
        """

    @classmethod
    def _build_html(
        cls,
        res: FlickerResult,
        sample_rate: float,
        extra_meta: Optional[Dict[str, Any]]
    ) -> str:
        curr_time = time.strftime("%Y-%m-%d %H:%M:%S")

        # Judge standard limits
        svm_pass = (res.svm <= 0.4)
        svm_status = f"<span class='pass-tag'>PASS (符合 &le;0.4)</span>" if svm_pass else f"<span class='warn-tag'>EXCEED (超标 &gt;0.4)</span>"

        pst_status = "N/A"
        if res.pst_lm is not None:
            pst_pass = (res.pst_lm <= 1.0)
            pst_status = f"<span class='pass-tag'>PASS (符合 &le;1.0)</span>" if pst_pass else f"<span class='warn-tag'>EXCEED (超标 &gt;1.0)</span>"

        # IEEE 1789 low risk guideline
        flicker_pct = res.flicker_percent
        dom_freq = res.dominant_frequency
        ieee_limit = 0.08 * dom_freq if dom_freq <= 1250 else 100.0
        ieee_status = f"<span class='pass-tag'>推荐低风险 (限值 ~{ieee_limit:.1f}%)</span>" if flicker_pct <= ieee_limit else f"<span class='warn-tag'>需关注风险 (限值 ~{ieee_limit:.1f}%)</span>"

        html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body>
            <h1>光学频闪综合测试与合规性评估报告</h1>
            <div class="subtitle">Optical Flicker Compliance & Quality Analysis Report (IEC 61547 / CIE / IEEE 1789)</div>

            <div class="section-title">一、测试采集环境与硬件配置</div>
            <table class="meta-table">
                <tr>
                    <td class="meta-label">测试硬件</td>
                    <td class="meta-val">ESP32-S3 + ADS8688 高精度采集模组</td>
                    <td class="meta-label">报告生成时间</td>
                    <td class="meta-val">{curr_time}</td>
                </tr>
                <tr>
                    <td class="meta-label">采样频率 (Fs)</td>
                    <td class="meta-val">{sample_rate:,.0f} Hz (SPS)</td>
                    <td class="meta-label">有效采样时长</td>
                    <td class="meta-val">{res.duration_sec:.2f} 秒</td>
                </tr>
                <tr>
                    <td class="meta-label">分析总样本点</td>
                    <td class="meta-val">{res.sample_count:,} 点</td>
                    <td class="meta-label">主调制频闪频率</td>
                    <td class="meta-val"><b>{res.dominant_frequency:.1f} Hz</b></td>
                </tr>
            </table>

            <div class="section-title">二、国际与行业核心频闪认证指标</div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th width="20%">测试项目 / 参照规范</th>
                        <th width="22%">参数定义 (Indicator)</th>
                        <th width="18%">实测数值</th>
                        <th width="22%">标准门限要求</th>
                        <th width="18%">评估判定</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><b>CIE TN 006 / EU 2019/2020</b></td>
                        <td>SVM (频闪可视度效应)</td>
                        <td><b>{res.svm:.3f}</b></td>
                        <td>&le; 0.4 (EU Ecodesign)</td>
                        <td>{svm_status}</td>
                    </tr>
                    <tr>
                        <td><b>IEC TR 61547-1 / 63158</b></td>
                        <td>PstLM (短期频闪指数)</td>
                        <td><b>{f"{res.pst_lm:.3f}" if res.pst_lm is not None else "N/A"}</b></td>
                        <td>&le; 1.0 (IEC 规范)</td>
                        <td>{pst_status}</td>
                    </tr>
                    <tr>
                        <td><b>IEEE 1789-2015</b></td>
                        <td>Percent Flicker (波动深度 %)</td>
                        <td><b>{res.flicker_percent:.2f} %</b></td>
                        <td>&le; 0.08 &times; Freq</td>
                        <td>{ieee_status}</td>
                    </tr>
                    <tr>
                        <td><b>IESNA 基础光学定义</b></td>
                        <td>Flicker Index (频闪指数 FI)</td>
                        <td><b>{res.flicker_index:.4f}</b></td>
                        <td>0.0 ~ 1.0 (越低越平稳)</td>
                        <td><span class='pass-tag'>指标记录</span></td>
                    </tr>
                    <tr>
                        <td><b>LRC ASSIST 感知模型</b></td>
                        <td>Mp (频闪感知效应)</td>
                        <td><b>{res.mp:.3f}</b></td>
                        <td>无强制 (参考人类敏感模型)</td>
                        <td><span class='pass-tag'>指标记录</span></td>
                    </tr>
                </tbody>
            </table>

            <div class="section-title">三、加州能源 Title 24 (JA10) 频闪调制深度评估</div>
            <table class="data-table">
                <thead>
                    <tr>
                        <th width="35%">低通截止频率档位 (Cut-off Freq)</th>
                        <th width="30%">截止范围内调制深度 (Percent Mod)</th>
                        <th width="35%">参考法规与建议</th>
                    </tr>
                </thead>
                <tbody>
        """

        for cutoff_freq, mod_pct in res.ja10.items():
            ja10_rec = "California Title 24 JA10 (&le; 30% 建议)" if cutoff_freq <= 200 else "高频波段调制"
            html += f"""
                    <tr>
                        <td>截止频率 &le; {cutoff_freq} Hz</td>
                        <td><b>{mod_pct:.2f} %</b></td>
                        <td>{ja10_rec}</td>
                    </tr>
            """

        html += f"""
                </tbody>
            </table>

            <div class="footer-note">
                报告由 ADS8688 高精度数字化光学频闪仪自动生成 | 核心算法兼容 IEEE 1789, CIE SVM, IEC PstLM, Title 24 JA10
            </div>
        </body>
        </html>
        """
        return html
