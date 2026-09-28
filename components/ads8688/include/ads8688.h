/**
 * @file ads8688.h
 * @brief ESP32-S3 driver for Texas Instruments ADS8688 16-Bit 8-Channel SAR ADC
 * @note Designed for ESP-IDF v5.x
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Default Pin Definitions according to the schematic
 */
#define ADS8688_DEFAULT_PIN_CS      (GPIO_NUM_14)
#define ADS8688_DEFAULT_PIN_SCLK    (GPIO_NUM_15)
#define ADS8688_DEFAULT_PIN_SDO     (GPIO_NUM_12) // MISO
#define ADS8688_DEFAULT_PIN_SDI     (GPIO_NUM_13) // MOSI
#define ADS8688_DEFAULT_PIN_RST     (GPIO_NUM_11)
#define ADS8688_DEFAULT_PIN_DAISY   (GPIO_NUM_10)

/**
 * @brief ADS8688 Command Definitions (16-bit)
 */
#define ADS8688_CMD_NO_OP           0x0000
#define ADS8688_CMD_STANDBY         0x8200
#define ADS8688_CMD_PWR_DN          0x8300
#define ADS8688_CMD_RESET           0x8500
#define ADS8688_CMD_AUTO_RST        0xA000
#define ADS8688_CMD_MAN_CH0         0xC000
#define ADS8688_CMD_MAN_CH1         0xC400
#define ADS8688_CMD_MAN_CH2         0xC800
#define ADS8688_CMD_MAN_CH3         0xCC00
#define ADS8688_CMD_MAN_CH4         0xD000
#define ADS8688_CMD_MAN_CH5         0xD400
#define ADS8688_CMD_MAN_CH6         0xD800
#define ADS8688_CMD_MAN_CH7         0xDC00
#define ADS8688_CMD_MAN_AUX         0xE000

/**
 * @brief ADS8688 Program Register Addresses (7-bit)
 */
#define ADS8688_REG_AUTO_SEQ_EN     0x01
#define ADS8688_REG_CH_PWR_DN       0x02
#define ADS8688_REG_FEAT_SEL        0x03
#define ADS8688_REG_RANGE_CH0       0x05
#define ADS8688_REG_RANGE_CH1       0x06
#define ADS8688_REG_RANGE_CH2       0x07
#define ADS8688_REG_RANGE_CH3       0x08
#define ADS8688_REG_RANGE_CH4       0x09
#define ADS8688_REG_RANGE_CH5       0x0A
#define ADS8688_REG_RANGE_CH6       0x0B
#define ADS8688_REG_RANGE_CH7       0x0C
#define ADS8688_REG_ALARM_OVERVIEW  0x10
#define ADS8688_REG_ALARM_CH0_TRIP  0x11

/**
 * @brief ADS8688 Channels
 */
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

/**
 * @brief ADS8688 Programmable Input Ranges
 * Based on internal Vref = 4.096V
 */
typedef enum {
    ADS8688_RANGE_BIP_2V5_VREF   = 0x00, ///< ±2.5 x Vref (±10.24V)
    ADS8688_RANGE_BIP_1V25_VREF  = 0x01, ///< ±1.25 x Vref (±5.12V)
    ADS8688_RANGE_BIP_0V625_VREF = 0x02, ///< ±0.625 x Vref (±2.56V)
    ADS8688_RANGE_BIP_0V3125_VREF= 0x03, ///< ±0.3125 x Vref (±1.28V)
    ADS8688_RANGE_UNI_2V5_VREF   = 0x05, ///< 0 to 2.5 x Vref (0 to 10.24V)
    ADS8688_RANGE_UNI_1V25_VREF  = 0x06, ///< 0 to 1.25 x Vref (0 to 5.12V)
    ADS8688_RANGE_UNI_0V625_VREF = 0x07, ///< 0 to 0.625 x Vref (0 to 2.56V)
} ads8688_range_t;

/**
 * @brief ADS8688 Configuration Struct
 */
typedef struct {
    spi_host_device_t spi_host;     ///< SPI Host (e.g. SPI2_HOST)
    gpio_num_t pin_cs;              ///< CS pin (e.g. GPIO_NUM_14)
    gpio_num_t pin_sclk;            ///< SCLK pin (e.g. GPIO_NUM_15)
    gpio_num_t pin_sdo;             ///< SDO / MISO pin (e.g. GPIO_NUM_12)
    gpio_num_t pin_sdi;             ///< SDI / MOSI pin (e.g. GPIO_NUM_13)
    gpio_num_t pin_rst;             ///< Hardware Reset pin (e.g. GPIO_NUM_11, or -1 if unused)
    gpio_num_t pin_daisy;           ///< Daisy-chain pin (e.g. GPIO_NUM_10, or -1 if unused)
    int clock_speed_hz;             ///< SPI Clock Speed (e.g. 5000000 ~ 10000000 Hz, max 17MHz)
    float vref;                     ///< Reference voltage in Volts (typically 4.096f)
    ads8688_range_t default_range;  ///< Default voltage range applied to all channels
} ads8688_config_t;

/**
 * @brief ADS8688 device handle opaque type
 */
typedef struct ads8688_dev_t *ads8688_handle_t;

/**
 * @brief Initialize ADS8688 ADC device
 *
 * @param[in] config Pointer to ads8688_config_t configuration struct
 * @param[out] out_handle Output handle to the initialized device
 * @return ESP_OK on success, or appropriate esp_err_t
 */
esp_err_t ads8688_init(const ads8688_config_t *config, ads8688_handle_t *out_handle);

/**
 * @brief De-initialize ADS8688 device and free resources
 *
 * @param[in] handle Device handle
 * @return ESP_OK on success
 */
esp_err_t ads8688_deinit(ads8688_handle_t handle);

/**
 * @brief Perform hardware reset using the RST pin
 *
 * @param[in] handle Device handle
 * @return ESP_OK on success
 */
esp_err_t ads8688_hw_reset(ads8688_handle_t handle);

/**
 * @brief Perform software reset via SPI command
 *
 * @param[in] handle Device handle
 * @return ESP_OK on success
 */
esp_err_t ads8688_sw_reset(ads8688_handle_t handle);

/**
 * @brief Write 1 byte to ADS8688 Program Register
 *
 * @param[in] handle Device handle
 * @param[in] reg_addr Register address (0x00 ~ 0x7F)
 * @param[in] val Value to write
 * @return ESP_OK on success
 */
esp_err_t ads8688_write_reg(ads8688_handle_t handle, uint8_t reg_addr, uint8_t val);

/**
 * @brief Read 1 byte from ADS8688 Program Register
 *
 * @param[in] handle Device handle
 * @param[in] reg_addr Register address (0x00 ~ 0x7F)
 * @param[out] out_val Pointer to store read value
 * @return ESP_OK on success
 */
esp_err_t ads8688_read_reg(ads8688_handle_t handle, uint8_t reg_addr, uint8_t *out_val);

/**
 * @brief Set the input voltage range for a specific channel
 *
 * @param[in] handle Device handle
 * @param[in] ch Channel (ADS8688_CH0 ~ ADS8688_CH7)
 * @param[in] range Input range from ads8688_range_t
 * @return ESP_OK on success
 */
esp_err_t ads8688_set_channel_range(ads8688_handle_t handle, ads8688_channel_t ch, ads8688_range_t range);

/**
 * @brief Get the configured input voltage range for a specific channel
 *
 * @param[in] handle Device handle
 * @param[in] ch Channel
 * @param[out] out_range Pointer to store range
 * @return ESP_OK on success
 */
esp_err_t ads8688_get_channel_range(ads8688_handle_t handle, ads8688_channel_t ch, ads8688_range_t *out_range);

/**
 * @brief Read raw 16-bit ADC value from a specified channel (Manual Mode)
 *
 * @param[in] handle Device handle
 * @param[in] ch Channel to sample
 * @param[out] out_raw Pointer to store 16-bit raw ADC code (0~65535)
 * @return ESP_OK on success
 */
esp_err_t ads8688_read_channel_raw(ads8688_handle_t handle, ads8688_channel_t ch, uint16_t *out_raw);

/**
 * @brief Read voltage in Volts from a specified channel (Manual Mode)
 *
 * @param[in] handle Device handle
 * @param[in] ch Channel to sample
 * @param[out] out_volts Pointer to store calculated voltage in Volts
 * @return ESP_OK on success
 */
esp_err_t ads8688_read_channel_volts(ads8688_handle_t handle, ads8688_channel_t ch, float *out_volts);

/**
 * @brief Read raw 16-bit ADC values for all 8 channels
 *
 * @param[in] handle Device handle
 * @param[out] raw_values Array of size 8 to store raw values for CH0~CH7
 * @return ESP_OK on success
 */
esp_err_t ads8688_read_all_raw(ads8688_handle_t handle, uint16_t raw_values[8]);

/**
 * @brief Read voltages in Volts for all 8 channels
 *
 * @param[in] handle Device handle
 * @param[out] volt_values Array of size 8 to store voltage values for CH0~CH7
 * @return ESP_OK on success
 */
esp_err_t ads8688_read_all_volts(ads8688_handle_t handle, float volt_values[8]);

float ads8688_raw_to_voltage(uint16_t raw, ads8688_range_t range, float vref);

/**
 * @brief Set calibration parameters (gain and offset) for a specific channel
 *
 * Formula: V_calibrated = (V_raw_volts * gain) + offset_volts
 *
 * @param[in] handle Device handle
 * @param[in] ch Channel (ADS8688_CH0 ~ ADS8688_CH7)
 * @param[in] gain Gain multiplier (typically 1.0f)
 * @param[in] offset_volts Voltage offset compensation in Volts
 * @return ESP_OK on success
 */
esp_err_t ads8688_set_channel_calibration(ads8688_handle_t handle, ads8688_channel_t ch, float gain, float offset_volts);

#ifdef __cplusplus
}
#endif
