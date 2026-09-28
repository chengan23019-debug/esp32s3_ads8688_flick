data = [
    (0.1, 0.1),
    (0.2, 0.2),
    (0.3, 0.21),
    (0.32, 0.32),
    (0.4, 0.4),
    (0.5, 0.39),
    (0.53, 0.42),
    (0.6, 0.41),
    (0.63, 0.42),
    (0.7, 0.69),
    (0.8, 0.8),
    (0.9, 0.85),
    (1.0, 0.79),
    (1.1, 0.85),
    (1.2, 0.83),
    (1.3, 1.3),
    (1.4, 1.37),
    (1.5, 1.49)
]

print(f"{'Vin':>5} | {'Vmeas':>5} | {'MeasHex':>7} | {'MeasBin':>19} | {'ExpHex':>7} | {'ExpBin':>19} | {'Diff':>6}")
print("-" * 75)
for vin, vmeas in data:
    exp_code = int(round(32768 + vin * 9600))
    meas_code = int(round(32768 + vmeas * 9600))
    diff = meas_code - exp_code
    b_meas = ' '.join([format(meas_code, '016b')[i:i+4] for i in range(0, 16, 4)])
    b_exp = ' '.join([format(exp_code, '016b')[i:i+4] for i in range(0, 16, 4)])
    print(f"{vin:5.2f} | {vmeas:5.2f} | 0x{meas_code:04X}  | {b_meas} | 0x{exp_code:04X} | {b_exp} | {diff:+6d}")
