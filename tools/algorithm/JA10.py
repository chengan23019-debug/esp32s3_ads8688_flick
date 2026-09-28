import numpy as np
import matplotlib.pyplot as plt
# 生成示例数据
duration = 10  # 测试持续时间（秒）
sampling_rate = 10000  # 采样率（每秒采样点数）
time = np.linspace(0, duration, duration * sampling_rate)
dimming_levels = {
    '100%': np.sin(2 * np.pi * 60 * time) + 10,  # 100%亮度下的数据
    '20%': np.sin(2 * np.pi * 60 * time) * 0.2 +10,  # 20%亮度下的数据
    'Min': np.sin(2 * np.pi * 60 * time) * 0.1 + 10  # 最小亮度下的数据
}


# 设置频率阈值并过滤数据
def apply_frequency_filter(frequency_data, frequencies,threshold):
    # 将70Hz以上的频率成分置零
    filtered_frequency_data = frequency_data.copy()
    filtered_frequency_data[np.abs(frequencies) > threshold] = 0

    # 进行逆傅里叶变换，得到滤波后的信号
    filtered_signal = np.fft.ifft(filtered_frequency_data)

    return filtered_signal

# 计算经过滤波后数据的百分比调制深度
def calculate_filtered_amplitude_modulation(filtered_data):
    maximum = np.max(filtered_data)
    minimum = np.min(filtered_data)
    return ((maximum - minimum) / (maximum + minimum)) * 100



def getJA10(signal):
    # 进行傅里叶变换
    frequency_data = np.fft.fft(signal)
    frequencies = np.fft.fftfreq(len(signal), 1 / sampling_rate)

    plt.figure(figsize=(12, 8))
    frequency_thresholds = [40, 90, 200, 400, 1000]
    result = []
    for threshold in frequency_thresholds:
        filtered_frequency_data = frequency_data.copy()
        filtered_data = apply_frequency_filter(filtered_frequency_data, frequencies, threshold)
        filtered_amplitude_modulation = calculate_filtered_amplitude_modulation(filtered_data)
        filtered_amplitude_modulation = np.real(filtered_amplitude_modulation)
        # print(f"Percent Amplitude Modulation (filtered {threshold}Hz) for : {filtered_amplitude_modulation:.2f}")
        result.append(round(filtered_amplitude_modulation,2))

    return result




if __name__ == '__main__':
    # signal = np.sin(2 * np.pi * 60 * time) + np.cos(2 * np.pi * 120 * time)+ np.sin(2 * np.pi * 1200 * time) + 3 # 示例信号（包含60Hz和120Hz成分）
    # print(max(signal))
    # print(min(signal))
    # getJA10(signal)
# # 对每个亮度等级进行处理
# for dimming_level, data in dimming_levels.items():
#     # 计算未经滤波的数据的百分比调制深度
#     unfiltered_amplitude_modulation = calculate_filtered_amplitude_modulation(data)
#     print(f"Percent Amplitude Modulation (unfiltered) for {dimming_level}: {unfiltered_amplitude_modulation:.2f}")
#     # 定义频率阈值并过滤数据
#     frequency_thresholds = [40, 90, 200, 400, 1000]
#     for threshold in frequency_thresholds:
#         filtered_data = apply_frequency_filter(data, threshold)
#
#         # 计算经过滤波后数据的百分比调制深度
#         filtered_amplitude_modulation = calculate_filtered_amplitude_modulation(filtered_data)
#         print(f"Percent Amplitude Modulation (filtered {threshold}Hz) for {dimming_level}: {filtered_amplitude_modulation:.2f}")

    data = np.loadtxt('./220/2/CH0.txt')

    # 删除负值
    # data[data < 0] = 0
    data = data[data > 0]
    data = data[0:1000000]
    print(data.shape)
    result = getJA10(data)
    print(result)