import numpy as np
from scipy.signal import find_peaks
from numpy.polynomial import Polynomial

def LRCFlickerMetric(waveform, Srate):
    '''
        LRCFlickerMetric: Calculate the LRC flicker metric for a given waveform
        waveform: 1D array of waveform values
        Srate: Sampling rate of waveform
    '''
    if len(waveform) % 2 != 0:
        waveform = waveform[:-1]  # Make length an even number

    # Modulation detection threshold spectrum
    SF = np.array([5, 10, 15, 20, 25, 40, 60])  # Frequencies, Hz
    ST = np.array([0.846, 0.568, 0.542, 0.759, 1.198, 5.677, 56.157]) / 100  # Threshold values

    # Interpolate sensitivity with 5th degree polynomial
    P = Polynomial.fit(SF, ST, deg=5)
    # print(P)
    # Fourier Transform
    X = np.abs(np.fft.fft(waveform))  # abs computes component amplitudes of complex fft

    # One-sided transform values expressed as a ratio of the dc value (Weber temporal contrast)
    M = 2 * X[:len(X) // 2 + 1] / X[0]    # 采集数据

    # Calculate Frequency Axis
    f = (Srate / 2) * np.linspace(0, 1, len(M))

    # Human sensitivity weighting, Modulation detection threshold (Mdth)
    Mdth = np.full_like(f, np.nan)  # Initialize array to not-a-number values

    for i in range(len(f)):
        if 5 <= f[i] <= 65:  # 65 is where extrapolated modulation=100%
            Mdth[i] = P(f[i])   # 人眼阈值

    # Apply human sensitivity weighting (i.e., divide by modulation detection threshold)
    Mpk = M / Mdth     # 采集数据和人眼阈值对比
    Mpk[np.isnan(Mdth)] = 0  # Replace not-a-number values with zero

    # Summation resulting in single-number metric
    Mp = np.sqrt(np.sum(Mpk**2))

    return Mp, f, Mpk, M



if __name__ == '__main__':

    # data = np.loadtxt("./Mp_Testing_data/idealHalfrectifiedSinWaveform.txt", delimiter='\t')
    # data.reshape(-1, 1)
    # waveForm = []
    # for i in data:
    #     waveForm.append(i[1])
    # waveForm = np.array(waveForm)
    # Mp, f, Mpk, M = LRCFlickerMetric(waveForm, 10000)
    # print(Mp)
    # print(f)
    # print(Mpk)
    # print(M)

    # Modulation detection threshold spectrum
    # SF = np.array([5, 10, 15, 20, 25, 40, 60])  # Frequencies, Hz
    # ST = np.array([0.846, 0.568, 0.542, 0.759, 1.198, 5.677, 56.157]) / 100  # Threshold values

    # Interpolate sensitivity with 5th degree polynomial
    # P = Polynomial.fit(SF, ST, deg=5)
    # frequency = np.arange(20, 2000, 1)
    # y = []
    # for i in frequency:
    #     y.append(P(i))
    # import matplotlib.pyplot as plt
    # plt.plot(frequency, y)
    # plt.xlabel("Frequency(Hz)")
    # plt.ylabel("Sensitivity")
    # plt.show()

    data = np.loadtxt('./230/2/CH0.txt')

    # 删除负值
    # data[data < 0] = 0
    data  = data[data > 0]
    data = data[0:100000]
    print(data.shape)
    Mp, f, Mpk, M = LRCFlickerMetric(data, 20000)
    Mp = round(Mp, 3)
    print(Mp)