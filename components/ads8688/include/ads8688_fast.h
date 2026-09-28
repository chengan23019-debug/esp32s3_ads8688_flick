/**
 * @file ads8688_fast.h
 * @brief High-speed direct register SPI driver for ADS8688 220 kSPS sampling on ESP32-S3
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"
#include "ads8688.h"

#ifdef __cplusplus
extern "C" {
#endif

#define ADS8688_FAST_BUFFER_SIZE  512 // 512 samples per frame (1024 bytes payload)

/**
 * @brief Configuration for ADS8688 Fast Driver
 */
typedef struct {
    spi_host_device_t host;         ///< SPI host (e.g. SPI2_HOST)
    int pin_cs;                     ///< GPIO14 (CS)
    int pin_sclk;                   ///< GPIO15 (SCLK)
    int pin_miso;                   ///< GPIO12 (SDO)
    int pin_mosi;                   ///< GPIO13 (SDI)
    int pin_rst;                    ///< GPIO11 (RST)
    int pin_daisy;                  ///< GPIO10 (DAISY)
    int clock_speed_hz;             ///< SPI SCLK speed (e.g. 16000000 or 20000000)
    ads8688_channel_t channel;      ///< Single channel to sample (e.g. ADS8688_CH0)
    ads8688_range_t range;          ///< Voltage range
    uint32_t sample_rate_hz;        ///< Sampling rate (e.g. 220000)
} ads8688_fast_config_t;

/**
 * @brief Initialize ADS8688 and setup hardware SPI2 register-level controller
 *
 * @param config Pointer to fast configuration
 * @return ESP_OK on success
 */
esp_err_t ads8688_fast_init(const ads8688_fast_config_t *config);

/**
 * @brief Single-shot atomic 32-bit transfer using direct SPI2 hardware registers
 *
 * @param cmd 16-bit command (MSB) + 16-bit dummy (0x0000)
 * @return 16-bit ADC raw conversion result from SDO
 */
uint16_t ads8688_fast_trans32(uint16_t cmd);

/**
 * @brief Core 1 sampling loop entry point with Ping-Pong double buffering
 *
 * @param notify_task_handle FreeRTOS task handle on Core 0 to receive frame notifications
 */
void ads8688_fast_start_sampling_loop(void *notify_task_handle);

/**
 * @brief Get the latest full buffer ready for USB transmission
 *
 * @param[out] out_buf Pointer to 512-sample buffer
 * @param[out] out_seq Frame sequence counter
 */
void ads8688_fast_get_ready_buffer(const uint16_t **out_buf, uint8_t *out_seq);

#ifdef __cplusplus
}
#endif
