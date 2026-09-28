# ADS8688 16-Bit 8-Channel ADC ESP-IDF 驱动开发文档

本驱动是专为 **ESP32-S3** 芯片在 **ESP-IDF v5.x** 框架下开发的 **德州仪器（TI）ADS8688**（16 位、8 通道模数转换器）组件库。

---

## 目录
- [一、芯片特性概述](#一芯片特性概述)
- [二、硬件引脚与连接说明](#二硬件引脚与连接说明)
- [三、底层时序与协议核心机制](#三底层时序与协议核心机制)
- [四、驱动架构与工程集成](#四驱动架构与工程集成)
- [五、API 接口参考手册](#五api-接口参考手册)
- [六、应用代码示例](#六应用代码示例)
- [七、实战调试与排坑经验指南](#七实战调试与排坑经验指南)

---

## 一、芯片特性概述

ADS8688 是一款高集成度、高精度的 16 位逐次逼近型（SAR）模数转换器：
- **采样通道**：8 路单端/伪差分输入通道（AIN0 ~ AIN7）+ 1 路 AUX 通道。
- **输入阻抗**：集成模拟前端（AFE），全量程具有恒定的 **1 MΩ** 高输入阻抗。
- **可编程量程**：每个通道可独立配置双极性（±2.5×Vref、±1.25×Vref、±0.625×Vref、±0.3125×Vref）或单极性（0~2.5×Vref、0~1.25×Vref）。
- **通信接口**：标准 4 线 SPI 接口，支持高速传输（本驱动采用 SPI Mode 1，默认配置 1 MHz ~ 5 MHz）。
- **数据输出**：直读纯二进制码（Straight Binary），内置防过冲及通道过压保护（高达 ±20V）。

---

## 二、硬件引脚与连接说明

根据系统硬件原理图设计，ESP32-S3 与 ADS8688 的引脚对应关系如下：

| 原理图网络 | 信号功能 | ESP32-S3 GPIO | ADS8688 管脚 | 功能说明 |
| :--- | :--- | :--- | :--- | :--- |
| **CS_14** | `ADS_CS` | **GPIO 14** | Pin 38 (`CS#`) | SPI 片选信号，低电平使能，由 SPI 外设硬件自动控制 |
| **CLK_15** | `ADS_SCLK` | **GPIO 15** | Pin 37 (`SCLK`) | SPI 串行时钟信号 |
| **MISO_12** | `ADS_SDO` | **GPIO 12** | Pin 36 (`SDO`) | SPI 主机输入 / 从机输出（ADC 数据移位输出端） |
| **MOSI_13** | `ADS_SDI` | **GPIO 13** | Pin 1 (`SDI`) | SPI 主机输出 / 从机输入（ADC 命令与寄存器输入端） |
| **RST_11** | `ADS_RST` | **GPIO 11** | Pin 2 (`RST#/PD#`) | 硬件复位引脚，低电平复位，驱动启动时自动触发复位时序 |
| **DAISY_10**| `ADS_DAISY`| **GPIO 10** | Pin 3 (`DAISY`)| 菊花链输入端，单片模式必须为低电平（驱动初始化驱动为 0） |

### 电源与模拟参考引脚注意：
1. **模拟供电 AVDD (Pin 9, Pin 30)**：必须外接 **5.0V 模拟电源**（`VCC_5V`），若无 5V 供电内部模拟放大器不工作。
2. **数字供电 DVDD (Pin 34)**：连接 **3.3V**（`VCC_3V3`），与 ESP32-S3 的 3.3V 电平匹配。
3. **基准参考 REFIO (Pin 5)**：实测本硬件电平为 **`2.560 V`**，驱动已做精准标定适配。
4. **通道输入接地 AIN_nGND (Pin 13 等)**：各通道的负参考端经 100Ω 电阻连接至 `AGND`。

---

## 三、底层时序与协议核心机制

### 1. 32-Clock 周期时序规则（核心要点）
* 根据 ADS8688 芯片手册（*Section 8.4.2*），**通道选择与数据读取必须在连续的 32 个 SCLK 时钟周期内完成**。
* **前 16 周期（Bytes 0~1）**：主机在 SDI 上发送 16 位指令（如 `MAN_Ch_n`）；此阶段 SDO 处于空闲静音状态（输出全 0）。
* **后 16 周期（Bytes 2~3）**：主机发送哑码（`0x0000`），**ADS8688 此时在 SDO 上真正输出上一次采样的 16 位 ADC 转换数据**。
* **驱动实现**：驱动封装了 `ads8688_spi_transfer32()`，保证片选 CS 拉低后完整发送 32 个时钟脉冲，从接收缓冲区的第 3、4 字节捕获真实码值。

### 2. 24-Clock 寄存器访问时序
* 内部程序配置寄存器（Program Register，如通道量程寄存器 `RG_Ch_n`）读写固定为 **24 个 SCLK 周期**：
  * 第 1 字节：7位寄存器地址 + 读写控制位（`bit0=1` 为写，`bit0=0` 为读）；
  * 第 2 字节：写入数据或哑码；
  * 第 3 字节：只读时芯片在 SDO 移位输出寄存器内容。

### 3. SPI 模式与极性
* 模式：**SPI Mode 1**（`CPOL = 0`, `CPHA = 1`）。
* 传输顺序：**MSB First**（高位在前）。

---

## 四、驱动架构与工程集成

驱动以标准 ESP-IDF 组件（Component）组织：

```text
esp32s3_c1288ma_ads8688/
├── components/
│   └── ads8688/
│       ├── CMakeLists.txt        # 组件依赖管理声明
│       ├── README.md             # 本开发文档
│       ├── ads8688.c             # 驱动核心逻辑实现
│       └── include/
│           └── ads8688.h         # 公共头文件及 API 声明
└── main/
    ├── CMakeLists.txt            # 主工程引入 ads8688
    └── hello_world_main.c        # 8通道采集演示任务
```

### 组件引用（CMakeLists.txt）
在 `main/CMakeLists.txt` 中添加 `ads8688` 依赖即可直接使用：
```cmake
idf_component_register(SRCS "hello_world_main.c"
                    PRIV_REQUIRES spi_flash ads8688 driver esp_driver_spi esp_driver_gpio
                    INCLUDE_DIRS "")
```

---

## 五、API 接口参考手册

### 1. 类型定义与枚举

#### 通道枚举 `ads8688_channel_t`
```c
typedef enum {
    ADS8688_CH0 = 0,
    ADS8688_CH1 = 1,
    ADS8688_CH2 = 2,
    ADS8688_CH3 = 3,
    ADS8688_CH4 = 4,
    ADS8688_CH5 = 5,
    ADS8688_CH6 = 6,
    ADS8688_CH7 = 7,
    ADS8688_CH_AUX = 8,
    ADS8688_CH_MAX = 8
} ads8688_channel_t;
```

#### 量程枚举 `ads8688_range_t`
| 枚举宏 | 十六进制值 | 量程范围（Vref = 2.56V 实测） | 量程范围（Vref = 4.096V 标称） |
| :--- | :--- | :--- | :--- |
| `ADS8688_RANGE_BIP_2V5_VREF` | `0x00` | $\pm 6.400\text{ V}$ | $\pm 10.240\text{ V}$ |
| `ADS8688_RANGE_BIP_1V25_VREF`| `0x01` | $\pm 3.200\text{ V}$ | $\pm 5.120\text{ V}$ |
| `ADS8688_RANGE_BIP_0V625_VREF`| `0x02` | $\pm 1.600\text{ V}$ | $\pm 2.560\text{ V}$ |
| `ADS8688_RANGE_BIP_0V3125_VREF`| `0x03` | $\pm 0.800\text{ V}$ | $\pm 1.280\text{ V}$ |
| `ADS8688_RANGE_UNI_2V5_VREF` | `0x05` | $0 \sim +6.400\text{ V}$ | $0 \sim +10.240\text{ V}$ |
| `ADS8688_RANGE_UNI_1V25_VREF`| `0x06` | $0 \sim +3.200\text{ V}$ | $0 \sim +5.120\text{ V}$ |

#### 配置结构体 `ads8688_config_t`
```c
typedef struct {
    spi_host_device_t spi_host;     ///< SPI 外设主机（如 SPI2_HOST）
    gpio_num_t pin_cs;              ///< CS 片选引脚（GPIO 14）
    gpio_num_t pin_sclk;            ///< SCLK 时钟引脚（GPIO 15）
    gpio_num_t pin_sdo;             ///< SDO / MISO 引脚（GPIO 12）
    gpio_num_t pin_sdi;             ///< SDI / MOSI 引脚（GPIO 13）
    gpio_num_t pin_rst;             ///< RST 硬件复位引脚（GPIO 11，若无填 -1）
    gpio_num_t pin_daisy;           ///< DAISY 菊花链引脚（GPIO 10，自动拉低）
    int clock_speed_hz;             ///< SPI 时钟频率（推荐 1000000 ~ 5000000 Hz）
    float vref;                     ///< 基准电压（实测硬件填 2.560f，标称填 4.096f）
    ads8688_range_t default_range;  ///< 初始化各通道默认量程
} ads8688_config_t;
```

---

### 2. 核心函数接口

#### 初始化与销毁
- `esp_err_t ads8688_init(const ads8688_config_t *config, ads8688_handle_t *out_handle)`
  - 作用：初始化 GPIO、配置 SPI 总线、触发硬件复位、写各通道默认量程、执行寄存器回读自检。
- `esp_err_t ads8688_deinit(ads8688_handle_t handle)`
  - 作用：释放 SPI 设备句柄与互斥锁资源。

#### 通道配置与校准
- `esp_err_t ads8688_set_channel_range(ads8688_handle_t handle, ads8688_channel_t ch, ads8688_range_t range)`
  - 作用：向芯片寄存器写入指定通道的输入电压量程。
- `esp_err_t ads8688_set_channel_calibration(ads8688_handle_t handle, ads8688_channel_t ch, float gain, float offset_volts)`
  - 作用：设置通道校准参数，换算公式：$V_{calibrated} = (V_{raw\_volts} \times gain) + offset\_volts$。

#### 数据读取
- `esp_err_t ads8688_read_channel_raw(ads8688_handle_t handle, ads8688_channel_t ch, uint16_t *out_raw)`
  - 作用：读取指定通道 16 位原始 ADC 码（范围 `0` ~ `65535`）。
- `esp_err_t ads8688_read_channel_volts(ads8688_handle_t handle, ads8688_channel_t ch, float *out_volts)`
  - 作用：读取指定通道并自动换算为实际物理电压（单位：V）。
- `esp_err_t ads8688_read_all_volts(ads8688_handle_t handle, float volt_values[8])`
  - 作用：一次性读取全部 8 个通道（CH0 ~ CH7）的物理电压。

---

## 六、应用代码示例

```c
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "ads8688.h"

static ads8688_handle_t s_ads_dev = NULL;

void app_main(void)
{
    // 1. 引脚与参数配置
    ads8688_config_t cfg = {
        .spi_host       = SPI2_HOST,
        .pin_cs         = GPIO_NUM_14,
        .pin_sclk       = GPIO_NUM_15,
        .pin_sdo        = GPIO_NUM_12,
        .pin_sdi        = GPIO_NUM_13,
        .pin_rst        = GPIO_NUM_11,
        .pin_daisy      = GPIO_NUM_10,
        .clock_speed_hz = 1000000,                    // 1 MHz
        .vref           = 2.560f,                     // 实测 REFIO 基准 2.56V
        .default_range  = ADS8688_RANGE_BIP_2V5_VREF  // 满量程 ±6.4V
    };

    // 2. 初始化设备
    ESP_ERROR_CHECK(ads8688_init(&cfg, &s_ads_dev));

    // 3. 通道量程独立设置
    ads8688_set_channel_range(s_ads_dev, ADS8688_CH0, ADS8688_RANGE_BIP_2V5_VREF);
    ads8688_set_channel_range(s_ads_dev, ADS8688_CH6, ADS8688_RANGE_BIP_2V5_VREF);

    // 4. 零点偏置校准（补偿硬件地回路压降）
    ads8688_set_channel_calibration(s_ads_dev, ADS8688_CH6, 1.0f, 0.0875f);

    // 5. 循环采集
    while (1) {
        float ch6_volts = 0.0f;
        uint16_t ch6_raw = 0;

        if (ads8688_read_channel_raw(s_ads_dev, ADS8688_CH6, &ch6_raw) == ESP_OK &&
            ads8688_read_channel_volts(s_ads_dev, ADS8688_CH6, &ch6_volts) == ESP_OK) {
            ESP_LOGI("APP", "CH6 Raw: 0x%04X (%5u) | Voltage: %+.4f V", ch6_raw, ch6_raw, ch6_volts);
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
```

---

## 七、实战调试与排坑经验指南

### 1. 现象：读数恒为 `0x0000` 或 `0x0001`，电压为负满量程
* **原因 A（时钟周期不足）**：SPI 事务少于 32 周期，CS 过早拉高。ADS8688 必须在第 17~32 周期才会移位输出转换值。
* **原因 B（电源缺失）**：ADS8688 的 Pin 9/30（AVDD）必须供电 5V。若仅有 3.3V 供电，模拟核不工作输出恒 0。
* **原因 C（菊花链引脚悬空）**：Pin 3 (`DAISY`) 必须可靠拉低到地。

### 2. 现象：电压测量线性度极好，但固定偏大约 1.60 倍
* **原因**：内部基准引脚 Pin 5 (`REFIO`) 实测电平为 **2.560V**，而代码误代入标准 4.096V，导致公式放大了 $4.096 / 2.560 = 1.6000$ 倍。
* **解决**：在配置结构体中将 `.vref = 2.560f` 即可解决。

### 3. 现象：零点存在几十毫伏的微小偏置（如 87.5 mV）
* **原因**：ADS8688 是伪差分输入，负端引脚（如 `AIN_6GND`）串联了 100Ω 电阻到模拟地，与外部信号源参考地之间存在回流压差。
* **解决**：调用 `ads8688_set_channel_calibration(handle, ch, 1.0f, offset_volts)` 即可在软件中精准消除零点底噪。
