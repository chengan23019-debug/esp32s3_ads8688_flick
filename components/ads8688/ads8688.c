/**
 * @file ads8688.c
 * @brief ESP32-S3 driver implementation for ADS8688 16-Bit 8-Channel ADC
 */

#include "ads8688.h"
#include <string.h>
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

static const char *TAG = "ADS8688";

/**
 * @brief Internal device structure
 */
struct ads8688_dev_t {
    ads8688_config_t config;
    spi_device_handle_t spi_dev;
    ads8688_range_t channel_ranges[8];
    float channel_gain[8];
    float channel_offset[8];
    SemaphoreHandle_t lock;
};

/* Channel command lookup table */
static const uint16_t s_channel_cmds[9] = {
    ADS8688_CMD_MAN_CH0,
    ADS8688_CMD_MAN_CH1,
    ADS8688_CMD_MAN_CH2,
    ADS8688_CMD_MAN_CH3,
    ADS8688_CMD_MAN_CH4,
    ADS8688_CMD_MAN_CH5,
    ADS8688_CMD_MAN_CH6,
    ADS8688_CMD_MAN_CH7,
    ADS8688_CMD_MAN_AUX
};

/* Range register lookup table */
static const uint8_t s_range_regs[8] = {
    ADS8688_REG_RANGE_CH0,
    ADS8688_REG_RANGE_CH1,
    ADS8688_REG_RANGE_CH2,
    ADS8688_REG_RANGE_CH3,
    ADS8688_REG_RANGE_CH4,
    ADS8688_REG_RANGE_CH5,
    ADS8688_REG_RANGE_CH6,
    ADS8688_REG_RANGE_CH7
};

/**
 * @brief Transfer 32-bit command/data via SPI
 * @note ADS8688 requires a full 32-SCLK cycle frame for command execution and data conversion.
 *       First 16 bits: Command on SDI
 *       Second 16 bits: 16-bit conversion data shifted out on SDO (Bytes 2 & 3)
 */
static esp_err_t ads8688_spi_transfer32(ads8688_handle_t handle, uint16_t cmd, uint16_t *out_val)
{
    uint8_t tx_buf[4];
    uint8_t rx_buf[4] = {0};

    // First 16 bits: Command on SDI
    tx_buf[0] = (uint8_t)(cmd >> 8);
    tx_buf[1] = (uint8_t)(cmd & 0xFF);
    // Next 16 bits: 0x0000 to clock out conversion data from SDO
    tx_buf[2] = 0x00;
    tx_buf[3] = 0x00;

    spi_transaction_t trans = {
        .flags = 0,
        .length = 32, // Exactly 32 SCLK cycles
        .tx_buffer = tx_buf,
        .rx_buffer = rx_buf,
    };

    esp_err_t ret = spi_device_polling_transmit(handle->spi_dev, &trans);
    if (ret == ESP_OK && out_val != NULL) {
        // In a 32-clock frame, SDO outputs the 16-bit conversion result in cycles 17~32 (Bytes 2 and 3)
        *out_val = ((uint16_t)rx_buf[2] << 8) | rx_buf[3];
    }
    return ret;
}

/**
 * @brief Transfer 24-bit program register command/data via SPI
 * @note ADS8688 Program Register access requires 24 SCLK cycles:
 *       Cycles 1..8: Address + R/W bit
 *       Cycles 9..16: Data (for write)
 *       Cycles 17..24: Data out on SDO (for read)
 */
static esp_err_t ads8688_spi_transfer24(ads8688_handle_t handle, const uint8_t tx_buf[3], uint8_t rx_buf[3])
{
    spi_transaction_t trans = {
        .flags = 0,
        .length = 24, // 24 SCLK cycles
        .tx_buffer = tx_buf,
        .rx_buffer = rx_buf,
    };

    return spi_device_polling_transmit(handle->spi_dev, &trans);
}

esp_err_t ads8688_write_reg(ads8688_handle_t handle, uint8_t reg_addr, uint8_t val)
{
    if (!handle) return ESP_ERR_INVALID_ARG;

    uint8_t tx_buf[3];
    // Protocol: 7-bit addr shifted by 1, Bit 0 is 1 for Write
    tx_buf[0] = (uint8_t)((reg_addr << 1) | 0x01);
    tx_buf[1] = val;
    tx_buf[2] = 0x00; // Dummy byte

    if (xSemaphoreTake(handle->lock, pdMS_TO_TICKS(100)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    esp_err_t ret = ads8688_spi_transfer24(handle, tx_buf, NULL);
    xSemaphoreGive(handle->lock);

    return ret;
}

esp_err_t ads8688_read_reg(ads8688_handle_t handle, uint8_t reg_addr, uint8_t *out_val)
{
    if (!handle || !out_val) return ESP_ERR_INVALID_ARG;

    uint8_t tx_buf[3];
    uint8_t rx_buf[3] = {0};

    // Protocol: 7-bit addr shifted by 1, Bit 0 is 0 for Read
    tx_buf[0] = (uint8_t)((reg_addr << 1) | 0x00);
    tx_buf[1] = 0x00;
    tx_buf[2] = 0x00;

    if (xSemaphoreTake(handle->lock, pdMS_TO_TICKS(100)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    esp_err_t ret = ads8688_spi_transfer24(handle, tx_buf, rx_buf);
    if (ret == ESP_OK) {
        // Output from SDO on cycles 17~24 (3rd byte)
        *out_val = rx_buf[2];
    }
    xSemaphoreGive(handle->lock);

    return ret;
}

esp_err_t ads8688_hw_reset(ads8688_handle_t handle)
{
    if (!handle) return ESP_ERR_INVALID_ARG;
    if (handle->config.pin_rst < 0) return ESP_ERR_NOT_SUPPORTED;

    gpio_set_level(handle->config.pin_rst, 0);
    esp_rom_delay_us(50);
    vTaskDelay(pdMS_TO_TICKS(10));
    gpio_set_level(handle->config.pin_rst, 1);
    vTaskDelay(pdMS_TO_TICKS(20)); // Wait for internal LDO and reference settling

    ESP_LOGI(TAG, "Hardware reset completed");
    return ESP_OK;
}

esp_err_t ads8688_sw_reset(ads8688_handle_t handle)
{
    if (!handle) return ESP_ERR_INVALID_ARG;

    if (xSemaphoreTake(handle->lock, pdMS_TO_TICKS(100)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    esp_err_t ret = ads8688_spi_transfer32(handle, ADS8688_CMD_RESET, NULL);
    xSemaphoreGive(handle->lock);

    if (ret == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(20));
        ESP_LOGI(TAG, "Software reset completed");
    }
    return ret;
}

esp_err_t ads8688_set_channel_range(ads8688_handle_t handle, ads8688_channel_t ch, ads8688_range_t range)
{
    if (!handle || ch >= ADS8688_CH_MAX) return ESP_ERR_INVALID_ARG;

    esp_err_t ret = ads8688_write_reg(handle, s_range_regs[ch], (uint8_t)(range & 0x0F));
    if (ret == ESP_OK) {
        handle->channel_ranges[ch] = range;
        ESP_LOGD(TAG, "Channel %d range configured to 0x%02X", ch, range);
    } else {
        ESP_LOGE(TAG, "Failed to set channel %d range: %s", ch, esp_err_to_name(ret));
    }
    return ret;
}

esp_err_t ads8688_get_channel_range(ads8688_handle_t handle, ads8688_channel_t ch, ads8688_range_t *out_range)
{
    if (!handle || ch >= ADS8688_CH_MAX || !out_range) return ESP_ERR_INVALID_ARG;
    *out_range = handle->channel_ranges[ch];
    return ESP_OK;
}

esp_err_t ads8688_read_channel_raw(ads8688_handle_t handle, ads8688_channel_t ch, uint16_t *out_raw)
{
    if (!handle || ch > ADS8688_CH_AUX || !out_raw) return ESP_ERR_INVALID_ARG;

    if (xSemaphoreTake(handle->lock, pdMS_TO_TICKS(100)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    esp_err_t ret = ESP_OK;

    /*
     * The ADS8688 manual channel acquisition requires a 32-clock frame:
     * Frame 1 (32 SCLKs): Host sends MAN_Ch_n command to select channel and sample it.
     * Frame 2 (32 SCLKs): Host sends NO_OP command to clock out the conversion result.
     *                     Bytes 2 & 3 in rx_buf contain the 16-bit ADC result.
     */
    ret = ads8688_spi_transfer32(handle, s_channel_cmds[ch], NULL);
    if (ret != ESP_OK) {
        xSemaphoreGive(handle->lock);
        return ret;
    }

    ret = ads8688_spi_transfer32(handle, ADS8688_CMD_NO_OP, out_raw);

    xSemaphoreGive(handle->lock);
    return ret;
}

esp_err_t ads8688_read_channel_volts(ads8688_handle_t handle, ads8688_channel_t ch, float *out_volts)
{
    if (!handle || ch >= ADS8688_CH_MAX || !out_volts) return ESP_ERR_INVALID_ARG;

    uint16_t raw = 0;
    esp_err_t ret = ads8688_read_channel_raw(handle, ch, &raw);
    if (ret != ESP_OK) {
        return ret;
    }

    float v = ads8688_raw_to_voltage(raw, handle->channel_ranges[ch], handle->config.vref);
    *out_volts = (v * handle->channel_gain[ch]) + handle->channel_offset[ch];
    return ESP_OK;
}

esp_err_t ads8688_read_all_raw(ads8688_handle_t handle, uint16_t raw_values[8])
{
    if (!handle || !raw_values) return ESP_ERR_INVALID_ARG;

    for (int i = 0; i < 8; i++) {
        esp_err_t ret = ads8688_read_channel_raw(handle, (ads8688_channel_t)i, &raw_values[i]);
        if (ret != ESP_OK) {
            return ret;
        }
    }
    return ESP_OK;
}

esp_err_t ads8688_read_all_volts(ads8688_handle_t handle, float volt_values[8])
{
    if (!handle || !volt_values) return ESP_ERR_INVALID_ARG;

    uint16_t raw[8];
    esp_err_t ret = ads8688_read_all_raw(handle, raw);
    if (ret != ESP_OK) {
        return ret;
    }

    for (int i = 0; i < 8; i++) {
        float v = ads8688_raw_to_voltage(raw[i], handle->channel_ranges[i], handle->config.vref);
        volt_values[i] = (v * handle->channel_gain[i]) + handle->channel_offset[i];
    }
    return ESP_OK;
}

esp_err_t ads8688_set_channel_calibration(ads8688_handle_t handle, ads8688_channel_t ch, float gain, float offset_volts)
{
    if (!handle || ch >= ADS8688_CH_MAX) return ESP_ERR_INVALID_ARG;
    handle->channel_gain[ch] = gain;
    handle->channel_offset[ch] = offset_volts;
    ESP_LOGI(TAG, "Channel %d calibration set: gain=%.4f, offset=%+.4f V", ch, gain, offset_volts);
    return ESP_OK;
}

float ads8688_raw_to_voltage(uint16_t raw, ads8688_range_t range, float vref)
{
    float full_scale = 0.0f;
    bool is_bipolar = false;

    switch (range) {
    case ADS8688_RANGE_BIP_2V5_VREF:
        full_scale = 2.5f * vref; // +/-10.24V
        is_bipolar = true;
        break;
    case ADS8688_RANGE_BIP_1V25_VREF:
        full_scale = 1.25f * vref; // +/-5.12V
        is_bipolar = true;
        break;
    case ADS8688_RANGE_BIP_0V625_VREF:
        full_scale = 0.625f * vref; // +/-2.56V
        is_bipolar = true;
        break;
    case ADS8688_RANGE_BIP_0V3125_VREF:
        full_scale = 0.3125f * vref; // +/-1.28V
        is_bipolar = true;
        break;
    case ADS8688_RANGE_UNI_2V5_VREF:
        full_scale = 2.5f * vref; // 0 to 10.24V
        is_bipolar = false;
        break;
    case ADS8688_RANGE_UNI_1V25_VREF:
        full_scale = 1.25f * vref; // 0 to 5.12V
        is_bipolar = false;
        break;
    case ADS8688_RANGE_UNI_0V625_VREF:
        full_scale = 0.625f * vref; // 0 to 2.56V
        is_bipolar = false;
        break;
    default:
        full_scale = 2.5f * vref;
        is_bipolar = true;
        break;
    }

    if (is_bipolar) {
        // Bipolar format is Straight Binary: 0x0000 = -FS, 0x8000 = 0V, 0xFFFF = +FS - 1LSB
        return (((float)raw - 32768.0f) / 32768.0f) * full_scale;
    } else {
        // Unipolar format is Straight Binary: 0x0000 = 0V, 0xFFFF = FS - 1LSB
        return ((float)raw / 65536.0f) * full_scale;
    }
}

esp_err_t ads8688_init(const ads8688_config_t *config, ads8688_handle_t *out_handle)
{
    if (!config || !out_handle) return ESP_ERR_INVALID_ARG;

    ads8688_handle_t dev = (ads8688_handle_t)calloc(1, sizeof(struct ads8688_dev_t));
    if (!dev) {
        return ESP_ERR_NO_MEM;
    }

    dev->config = *config;
    if (dev->config.vref <= 0.0f) {
        dev->config.vref = 4.096f; // Internal typical reference
    }
    if (dev->config.clock_speed_hz <= 0) {
        dev->config.clock_speed_hz = 1000000; // 1 MHz conservative default
    }

    dev->lock = xSemaphoreCreateMutex();
    if (!dev->lock) {
        free(dev);
        return ESP_ERR_NO_MEM;
    }

    // 1. Configure DAISY pin if provided (Drive low for standalone mode)
    if (dev->config.pin_daisy >= 0) {
        gpio_config_t daisy_io_conf = {
            .pin_bit_mask = (1ULL << dev->config.pin_daisy),
            .mode = GPIO_MODE_OUTPUT,
            .pull_up_en = GPIO_PULLUP_DISABLE,
            .pull_down_en = GPIO_PULLDOWN_ENABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        gpio_config(&daisy_io_conf);
        gpio_set_level(dev->config.pin_daisy, 0);
        ESP_LOGI(TAG, "DAISY pin (GPIO%d) configured LOW", dev->config.pin_daisy);
    }

    // 2. Configure RST pin if provided
    if (dev->config.pin_rst >= 0) {
        gpio_config_t rst_io_conf = {
            .pin_bit_mask = (1ULL << dev->config.pin_rst),
            .mode = GPIO_MODE_OUTPUT,
            .pull_up_en = GPIO_PULLUP_ENABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = GPIO_INTR_DISABLE,
        };
        gpio_config(&rst_io_conf);
        gpio_set_level(dev->config.pin_rst, 1);
    }

    // 3. Configure SPI Bus
    spi_bus_config_t buscfg = {
        .mosi_io_num = dev->config.pin_sdi,
        .miso_io_num = dev->config.pin_sdo,
        .sclk_io_num = dev->config.pin_sclk,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 32,
    };

    esp_err_t ret = spi_bus_initialize(dev->config.spi_host, &buscfg, SPI_DMA_CH_AUTO);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "Failed to initialize SPI bus: %s", esp_err_to_name(ret));
        vSemaphoreDelete(dev->lock);
        free(dev);
        return ret;
    }

    // 4. Attach ADS8688 to SPI bus (SPI Mode 1: CPOL=0, CPHA=1)
    spi_device_interface_config_t devcfg = {
        .command_bits = 0,
        .address_bits = 0,
        .dummy_bits = 0,
        .mode = 1, // SPI Mode 1
        .duty_cycle_pos = 128,
        .cs_ena_pretrans = 2,
        .cs_ena_posttrans = 2,
        .clock_speed_hz = dev->config.clock_speed_hz,
        .spics_io_num = dev->config.pin_cs,
        .queue_size = 1,
    };

    ret = spi_bus_add_device(dev->config.spi_host, &devcfg, &dev->spi_dev);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to add SPI device: %s", esp_err_to_name(ret));
        vSemaphoreDelete(dev->lock);
        free(dev);
        return ret;
    }

    // 5. Hardware reset if pin is available, otherwise software reset
    if (dev->config.pin_rst >= 0) {
        ads8688_hw_reset(dev);
    } else {
        ads8688_sw_reset(dev);
    }

    // 6. Configure default ranges and calibration for all channels
    for (int i = 0; i < 8; i++) {
        dev->channel_gain[i] = 1.0f;
        dev->channel_offset[i] = 0.0f;
        ads8688_set_channel_range(dev, (ads8688_channel_t)i, dev->config.default_range);
    }

    // 7. Communication Self-Test: Read back CH0 range register
    uint8_t readback_val = 0xFF;
    esp_err_t test_ret = ads8688_read_reg(dev, ADS8688_REG_RANGE_CH0, &readback_val);
    if (test_ret == ESP_OK) {
        ESP_LOGI(TAG, "SPI Self-test: Range CH0 readback = 0x%02X (Expected: 0x%02X)",
                 readback_val, (uint8_t)(dev->config.default_range & 0x0F));
        if ((readback_val & 0x0F) != (dev->config.default_range & 0x0F)) {
            ESP_LOGW(TAG, "WARNING: Register readback did not match expected value! Check 5V power supply and SPI wiring.");
        }
    }

    *out_handle = dev;
    ESP_LOGI(TAG, "ADS8688 initialized successfully (Clock: %d Hz, Vref: %.3f V)",
             dev->config.clock_speed_hz, dev->config.vref);
    return ESP_OK;
}

esp_err_t ads8688_deinit(ads8688_handle_t handle)
{
    if (!handle) return ESP_ERR_INVALID_ARG;

    if (handle->spi_dev) {
        spi_bus_remove_device(handle->spi_dev);
    }
    if (handle->lock) {
        vSemaphoreDelete(handle->lock);
    }
    free(handle);
    ESP_LOGI(TAG, "ADS8688 de-initialized");
    return ESP_OK;
}
