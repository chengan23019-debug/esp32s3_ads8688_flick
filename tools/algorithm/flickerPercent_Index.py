import numpy as np


def flickerIndex(waveForm):
    avgl = np.mean(waveForm)
    w = waveForm-avgl
    w[w<0] = 0
    FI = np.sum(w)/np.sum(waveForm)
    return FI

def percent_flicker(waveForm):
    A = np.max(waveForm)
    B = np.min(waveForm)
    P_fl = 100*(A-B)/(A+B)
    return P_fl


if __name__ == '__main__':
    # numpy 读 csv
    data = np.loadtxt("E:\code\matlab\ASSIST_MP\dimmedLEDALampWaveform.txt", delimiter='\t')
    data.reshape(-1, 1)
    waveForm = []
    for i in data:
        waveForm.append(i[1])
    waveForm = np.array(waveForm)
    print(flickerIndex(waveForm))
    print(percent_flicker(waveForm))