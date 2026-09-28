import pyvisa
import time

rm = pyvisa.ResourceManager()
resource_name = "USB0::0x2A8D::0x3601::MY57200190::INSTR"

try:
    source = rm.open_resource(resource_name)
    source.timeout = 10000
    idn = source.query("*IDN?").strip()
    print(f"已连接仪器: {idn}")
    
    # 1. 复位与状态清除
    source.write("*RST")
    source.write("*CLS")
    time.sleep(1)
    
    # 2. 安全保护与额定参数
    source.write("CURR:LIM 2.0")
    source.write("FREQ 50.0")
    source.write("VOLT 230.0")
    
    # ==================== 8.8 Hz 目标 PstLM = 1.0 参数 ====================
    # IEC TR 61547-1 / IEC 61000-4-15 规范中 230V @ 8.8Hz 矩形波的标准调制量为 0.250%
    # 上次由于放大了 2.2727 倍（ΔV=1.306V），导致测量值刚好为 PstLM = 2.276
    # PstLM 与调制深度呈高度线性，因此直接根据当前实测 2.276 校正回 1.0
    target_pst = 1.0
    last_measured_pst = 2.276  # 上次实测值
    last_delta_v = 1.3068      # 上次实际设置的 ΔV

    v_nominal = 230.0
    # 线性校正得到新的 ΔV：
    delta_v = last_delta_v * (target_pst / last_measured_pst)  # 约 0.574 V (约 0.2496%)
    
    v_high = round(v_nominal + delta_v / 2.0, 4)  # 约 230.287 V
    v_low = round(v_nominal - delta_v / 2.0, 4)   # 约 229.713 V
    dwell_time = round(1.0 / (8.8 * 2), 5)        # 0.05682 s (8.8 Hz 半周期，高低电平各 56.82 ms)
    
    print(f"\n【标准 8.8 Hz 测试配置 - 目标 PstLM = 1.0】")
    print(f"基于实测值 {last_measured_pst} 校准后参数:")
    print(f"ΔV 波动幅度: {delta_v:.4f} V (波动比例: {delta_v / v_nominal * 100:.3f}%)")
    print(f"电压设定: V_high = {v_high} V, V_low = {v_low} V")
    print(f"驻留时间: {dwell_time * 1000:.2f} ms (调制频率 8.8 Hz)")
    print("--------------------------------------------------")
    # ====================================================================

    # 3. 设置触发源（修正后的兼容语法，消除 -113 错误）
    source.write("TRIG:SOUR IMM")

    # 4. 写入 8.8 Hz 硬件 LIST
    source.write("VOLT:MODE LIST")
    source.write(f"LIST:VOLT {v_high}, {v_low}")
    source.write(f"LIST:DWEL {dwell_time}, {dwell_time}")
    source.write("LIST:COUN INF")
    source.write("LIST:STEP AUTO")
    
    # 5. 点亮并预热
    print("点亮白炽灯，请等待预热达到热平衡...")
    source.write("OUTP ON")
    time.sleep(3)  # 如果刚开灯，建议在这里多等 1~2 分钟
    
    # 6. 启动调制
    print("启动 8.8 Hz 瞬态调制...")
    source.write("INIT:NAME TRAN")
    time.sleep(0.5)

    # 7. 检查错误状态
    err = source.query("SYST:ERR?").strip()
    print(f"6811C 状态检查: {err}")

    print("\n>>> 6811C 正在稳定输出目标 PstLM = 1.0 光调制波形 <<<")
    print("采集卡可以开始采样并进行 PstLM 计算。")
    print("按 Ctrl+C 退出并安全关闭输出。\n")

    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\n用户中断，正在关断输出...")
    if 'source' in locals():
        source.write("ABOR:NAME TRAN")
        source.write("OUTP OFF")
        source.close()
    print("仪器已安全关断。")
    
except Exception as e:
    print(f"执行出错: {e}")
    if 'source' in locals():
        source.write("OUTP OFF")
        source.close()