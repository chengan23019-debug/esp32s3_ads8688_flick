import numpy as np
from scipy.signal import find_peaks
from test_and_verify.频闪数据分析 import deal_data
def getSensitivityStrobo(frequency):
    frequency = np.array(frequency)
    if frequency.all() < 0 or frequency.all() > 2000:
        print('Input frequency (in Hz) for the SVM threshold should be a positive value and less than or equal to 2000 Hz!')
    a = 0.00518
    b = 306.6
    threshold = 1 / (1 + np.exp(-a * (frequency - b))) + np.exp(-frequency / 10) * 20
    return threshold

def stroboVisibilityMeasure(waveForm, duration):
    '''
    计算SVM
    :param waveForm: 光信号波形
    :param duration: 光信号持续时间
    '''
    Ls = len(waveForm) # 采样点数
    Fs = Ls / duration # 采样率
    T = 1 / Fs # 采样周期
    ts = np.arange(0, Ls) * T # 采样时间序列
    Tmax = np.max(ts) # 采样时间序列最大值
    Df = 1 / Tmax  # 频率分辨率
    Fmax = Ls * Df / 2 # 最大频率
    FmaxSVM = 2000

    if len(waveForm.shape) > 1:
        waveForm = np.squeeze(waveForm)

    if Fs < FmaxSVM * 2:
        raise ValueError('Sampling frequency should be >= 2 * FmaxSVM')

    if np.any(waveForm < 0):
        print('Input light waveform contains negative values! Values must be >= 0!')

    if duration < 1:
        print('Duration of the light waveform is recommended to be at least 1 sec!')

    if duration <= 0:
        raise ValueError('Duration of the light waveform should be a positive number!')

    NFFT = int(2 ** (np.ceil(np.log2(Ls)) + 1)) # FFT点数
    attHann = np.sum(np.hanning(Ls)) / Ls
    Y = (1 / attHann) * np.fft.fft(np.hanning(Ls) * waveForm, NFFT) / Ls
    mag0 = np.abs(Y[0])  # 直流分量
    mag = np.abs(Y[0:NFFT // 2]) / mag0  # 除以直流分量
    f = Fmax * np.linspace(0, 1, NFFT // 2) # 频率序列
    FmaxSVMindex = int(np.round((NFFT // 2) * (FmaxSVM / Fmax))) # 最大频率对应的索引
    ff = f[1:FmaxSVMindex + 1] # 有效频率序列
    b = 2 * mag[1:FmaxSVMindex + 1] # 有效频率对应的幅值序列
    b_pks, i_pks = find_peaks(b)   #  计算峰值，b_pks为峰值的索引，
    i_pks= b[b_pks] # i_pks为峰值对应的幅值
    c_pks = i_pks / getSensitivityStrobo(ff[b_pks.astype(int)]) # 除以阈值
    SVM = np.sum(c_pks ** 3.7) ** (1 / 3.7) # SVM
    return SVM


def flickerIndex(waveForm):
    '''
    计算闪烁指数
    '''
    avgl = np.mean(waveForm)
    w = waveForm-avgl
    w[w<0] = 0
    FI = np.sum(w)/np.sum(waveForm)
    return FI

def percent_flicker(waveForm):
    '''
        闪烁百分比
    '''
    A = np.max(waveForm)
    B = np.min(waveForm)
    P_fl = 100*(A-B)/(A+B)
    return P_fl







if __name__ == '__main__':

    # Fs = 20000
    # T = 180
    # delT = 1 / Fs
    # NS = Fs * T + 1
    # t = np.arange(0, NS) * delT
    # F = 100
    # MD = 25.63
    # M = MD / 100
    # WFselect = 1 + M * np.sin(2 * np.pi * F * t)
    # SVM = stroboVisibilityMeasure(WFselect, T)
    # SVM = np.round(SVM, 3)
    # print(SVM)
    # FI = flickerIndex(WFselect)
    # FI = np.round(FI, 3)
    # print(FI)
    import matplotlib.pyplot as plt

    for i in range(1, 6):
        path = r'G:\老夏频闪数据\2023-12-20\929002307071\929002307071-00'
        data = np.loadtxt(path +str(i) + "/CH0.TXT")
        data = data[data > 0]
        # plt.plot(data[0:100000])
        # plt.show()
        # data = deal_data(data)
        # 删除负值
        # data[data < 0] = 0
        data = data[0:100000]
        SVM = stroboVisibilityMeasure(data, 5)
        SVM = np.round(SVM, 3)
        print(SVM)

    # path = r'G:\202312205'
    # data = np.loadtxt(path + "/CH2.TXT")
    # data = data[data > 0]
    # print(len(data))
    # # plt.plot(data[0:100000])
    # # plt.show()
    # data =deal_data(data)
    # # 删除负值
    # # data[data < 0] = 0
    # data = data[0:100000]
    # SVM = stroboVisibilityMeasure(data, 5)
    # SVM = np.round(SVM, 3)
    # print(SVM)
