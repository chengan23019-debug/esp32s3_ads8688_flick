import numpy as np
from scipy.signal import butter, filtfilt

def light_flickermeter_metric_PstLM(I, FS):
    if len(I.shape) != 1:
        raise ValueError('输入必须是一维向量')
    if FS < 2000:
        print('采样频率应该 >= 2000 Hz')
    if np.any(I < 0):
        print('输入光波形包含负值！值必须 >= 0！')

    # Block a: 输入光照适配器
    u_0 = I / np.mean(I)

    # Block b: 滤波器
    b_hp, a_hp = butter(1, 0.05 / (FS / 2), 'high')
    u_hp = filter_matlab(b_hp, a_hp, u_0 - np.mean(u_0))
    smooth_limit = min(int(np.round(FS / 10)), len(u_hp))
    u_hp[:smooth_limit] *= np.linspace(0, 1, smooth_limit)
    b_bw, a_bw = butter(6, 35 / (FS / 2), 'low')
    u_bw = filter_matlab(b_bw, a_bw, u_hp)

    # Block 3b: 加权滤波器
    # 使用bilinear转换创建数字滤波器
    num1 = [0.041661, 44.758, 2715.6, 29839, 0]
    den1 = [1, 196.32, 11781, 534820, 3505380]
    b_w, a_w = bilinear(num1, den1, FS)
    u_w = filter_matlab(b_w, a_w, u_bw)

    # Block c: 平方和平滑处理
    SCALING_FACTOR = 1.101603155420234e+06
    u_q = [i * i for i in u_w]  # 将列表中的每个元素与数字相乘
    # u_q = np.array(u_w) ** 2
    b_lp, a_lp = butter(1, 1 / (2 * np.pi * 300e-3) / (FS / 2), 'low')
    s = [i * SCALING_FACTOR for i in filter_matlab(b_lp, a_lp, u_q)]
    # s = SCALING_FACTOR * filter_matlab(b_lp, a_lp, u_q)
    P_inst = s

    # Block d: 统计评估
    tau_transient = 20
    n_transient = int(tau_transient * FS)
    if n_transient >= len(P_inst):
        print(f'警告：信号长度（{len(P_inst)/FS:.1f}秒）不足以排除{tau_transient}秒瞬态，已使用全部数据')
        return None, None
        n_transient = 0
    P_inst_max = np.max(P_inst[n_transient:])
    s = s[n_transient:]
    NUMOF_CLASSES = 10000
    bin_cnt, edges = np.histogram(s, NUMOF_CLASSES)
    cpf_cum_probability = 100 * (1 - np.cumsum(bin_cnt) / np.sum(bin_cnt))

    def get_percentile(cpf_cum_probability, cpf_edges, limit):
        idx = np.argmin(np.abs(cpf_cum_probability - limit))
        return cpf_edges[idx]

    p_50s = np.mean([get_percentile(cpf_cum_probability, edges, p) for p in [30, 50, 80]])
    p_10s = np.mean([get_percentile(cpf_cum_probability, edges, p) for p in [6, 8, 10, 13, 17]])
    p_3s = np.mean([get_percentile(cpf_cum_probability, edges, p) for p in [2.2, 3, 4]])
    p_1s = np.mean([get_percentile(cpf_cum_probability, edges, p) for p in [0.7, 1, 1.5]])
    p_0_1 = get_percentile(cpf_cum_probability, edges, 0.1)

    PstLM = np.sqrt(0.0314 * p_0_1 + 0.0525 * p_1s + 0.0657 * p_3s + 0.28 * p_10s + 0.08 * p_50s)
    PstLM = round(PstLM, 3)
    return PstLM, P_inst_max

# 您需要自己实现 bilinear 函数，它应该模仿 MATLAB 中 bilinear 函数的行为
from scipy.signal import bilinear as scipy_bilinear

def bilinear(num, den, fs):
    # 将模拟滤波器系数转换为数字滤波器系数
    b, a = scipy_bilinear(num, den, fs)
    return b, a

def filter_matlab(b,a,x):
    y = []
    y.append(b[0] * x[0])
    for i in range(1,len(x)):
        y.append(0)
        for j in range(len(b)):
            if i >= j :
                y[i] = y[i] + b[j] * x[i - j ]
                j += 1
        for l in range(len(b)-1 ):
            if i >l:
                y[i] = (y[i] - a[l+1] * y[i -l-1])
                l += 1
        i += 1
    return y

if __name__ == '__main__':


    # data = np.loadtxt(r'G:\老夏频闪数据\2023-12-19\929002298613\929002298613-003\CH0.txt')
    # data = data[data>0]
    # pst ,s  = light_flickermeter_metric_PstLM(data, 20000)
    # print(pst)
    # Fs = 20000
    # T = 180
    # delT = 1 / Fs
    # NS = Fs * T + 1
    # t = np.arange(0, NS) * delT
    # F = 100
    # MD = 25.63
    # M = MD / 100
    # WFselect = 1 + M * np.sin(2 * np.pi * F * t)
    # import numpy as np
    # import matplotlib.pyplot as plt
    # #
    # # # 设置频率
    # frequency = 50  # Hz
    #
    # # 设置采样频率
    # sampling_rate = 20000  # Hz
    # T = 1 / sampling_rate  # 采样间隔
    #
    # # 设置时间长度
    # time = 1  # 秒
    #
    # # 创建时间轴数据
    # t = np.arange(0, time, T)
    # # 创建正弦波信号
    # max_y = np.sin(2 * np.pi * frequency * t[0:int(0.114 * sampling_rate)]) * 230.3162500
    # max_y = abs(max_y)
    # min_y = np.sin(2 * np.pi * frequency * t[int(0.114 * sampling_rate):int(0.114 * sampling_rate) + int(0.114 * sampling_rate)]) * 229.6837500
    # min_y = abs(min_y)
    # # for i in min_y:
    # #     max_y = np.append(max_y, i)
    # # max_y 和 min_y 的长度不一样，所以要将 min_y 的最后一个元素添加到 max_y 中
    # max_y = np.append(max_y, min_y)
    # # 复制并拼接8.8份
    # replicated_list = np.tile(max_y, 8)  # 完整复制8份
    # fraction = max_y[:int(len(max_y) * 0.8)]  # 额外复制0.8份
    #
    # # 拼接完整的复制和部分复制
    # full_list = np.concatenate((replicated_list, fraction))
    #
    # replicated_list_all = np.tile(full_list, 180)  # 完整复制180份
    #
    # plt.plot(t, replicated_list_all[0:len(t)], label='50Hz')
    # plt.show()
    #
    # pst, s = light_flickermeter_metric_PstLM(replicated_list_all, 20000)
    # print(pst)

    # 绘制波形
    # plt.figure(figsize=(10, 4))
    # plt.plot(t[0:len(full_list)], full_list, label='50Hz')
    # plt.title('50Hz Sine Wave')
    # plt.xlabel('Time [s]')
    # plt.ylabel('Amplitude')
    # plt.grid(True)
    # plt.legend()
    # plt.show()

    import numpy as np
    import matplotlib.pyplot as plt

    #
    # # 设置频率
    frequency = 50  # Hz

    # 设置采样频率
    sampling_rate = 20000  # Hz
    T = 1 / sampling_rate  # 采样间隔

    # 设置时间长度
    time = 1  # 秒

    # 创建时间轴数据
    t = np.arange(0, time, T)
    # 创建正弦波信号
    max_y = np.sin(2 * np.pi * frequency * t)
    max_y = abs(max_y)
    # for i in min_y:
    #     max_y = np.append(max_y, i)
    # max_y 和 min_y 的长度不一样，所以要将 min_y 的最后一个元素添加到 max_y 中
    # 复制并拼接8.8份
    # replicated_list = np.tile(max_y, 8)  # 完整复制8份
    # fraction = max_y[:int(len(max_y) * 0.8)]  # 额外复制0.8份
    #
    # # 拼接完整的复制和部分复制
    # full_list = np.concatenate((replicated_list, fraction))
    #
    replicated_list_all = np.tile(max_y, 180)  # 完整复制180份
    t = np.tile(t, 180)  # 完整复制180份
    print(len(t))
    print(len(replicated_list_all))
    plt.plot(t, replicated_list_all)
    plt.show()

    pst, s = light_flickermeter_metric_PstLM(replicated_list_all, 20000)
    print(pst)