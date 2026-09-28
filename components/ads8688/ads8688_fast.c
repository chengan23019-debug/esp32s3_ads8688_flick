/**
 * @file ads8688_fast.c
 * @brief High-speed direct register SPI driver implementation for ADS8688 on ESP32-S3
 */

#include "ads8688_fast.h"
#include <string.h>
#include "esp_log.h"
#include "esp_cpu.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "hal/spi_ll.h"
#include "esp_private/esp_clk.h"

static const char *TAG = "ADS8688_FAST";

static spi_dev_t *s_hw = SPI_LL_GET_HW(SPI2_HOST);
static spi_device_handle_t s_spi_dev = NULL;
static ads8688_fast_config_t s_cfg;

// Double buffer for ping-pong sampling
static uint16_t s_buffer_a[ADS8688_FAST_BUFFER_SIZE];
static uint16_t s_buffer_b[ADS8688_FAST_BUFFER_SIZE];

static volatile uint16_t *s_active_buf = s_buffer_a;
static volatile const uint16_t *s_ready_buf = NULL;
static volatile uint8_t s_ready_seq = 0;
static volatile bool s_is_sampling = false;

static const uint16_t s_channel_cmds[8] = {
    ADS8688_CMD_MAN_CH0,
    ADS8688_CMD_MAN_CH1,
    ADS8688_CMD_MAN_CH2,
    ADS8688_CMD_MAN_CH3,
    ADS8688_CMD_MAN_CH4,
    ADS8688_CMD_MAN_CH5,
    ADS8688_CMD_MAN_CH6,
    ADS8688_CMD_MAN_CH7
};

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

IRAM_ATTR uint16_t ads8688_fast_trans32(uint16_t cmd)
{
    uint32_t tx_word = (uint32_t)(cmd >> 8) |
                       ((uint32_t)(cmd & 0xFF) << 8);

    s_hw->data_buf[0] = tx_word;
    s_hw->cmd.usr = 1;
    while (s_hw->cmd.usr) {
        // Hardware SPI transfer (~1.6 us at 20MHz)
    }

    uint32_t rx_word = s_hw->data_buf[0];
    uint8_t byte2 = (uint8_t)(rx_word >> 16);
    uint8_t byte3 = (uint8_t)(rx_word >> 24);

    return ((uint16_t)byte2 << 8) | byte3;
}

static esp_err_t ads8688_fast_write_reg(uint8_t reg_addr, uint8_t val)
{
    uint8_t tx_buf[3];
    tx_buf[0] = (uint8_t)((reg_addr << 1) | 0x01);
    tx_buf[1] = val;
    tx_buf[2] = 0x00;

    spi_transaction_t trans = {
        .length = 24,
        .tx_buffer = tx_buf,
    };
    return spi_device_polling_transmit(s_spi_dev, &trans);
}

esp_err_t ads8688_fast_init(const ads8688_fast_config_t *config)
{
    if (!config) return ESP_ERR_INVALID_ARG;
    memcpy(&s_cfg, config, sizeof(ads8688_fast_config_t));

    // Optional Hardware Reset Pin
    if (config->pin_rst >= 0) {
        gpio_config_t rst_cfg = {
            .pin_bit_mask = (1ULL << config->pin_rst),
            .mode = GPIO_MODE_OUTPUT,
            .pull_up_en = GPIO_PULLUP_ENABLE,
        };
        gpio_config(&rst_cfg);
        gpio_set_level(config->pin_rst, 0);
        esp_rom_delay_us(50);
        gpio_set_level(config->pin_rst, 1);
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    // Optional Daisy-chain pin
    if (config->pin_daisy >= 0) {
        gpio_config_t daisy_cfg = {
            .pin_bit_mask = (1ULL << config->pin_daisy),
            .mode = GPIO_MODE_OUTPUT,
        };
        gpio_config(&daisy_cfg);
        gpio_set_level(config->pin_daisy, 0);
    }

    // Initialize SPI2 Bus
    spi_bus_config_t bus_cfg = {
        .mosi_io_num = config->pin_mosi,
        .miso_io_num = config->pin_miso,
        .sclk_io_num = config->pin_sclk,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = 64,
    };
    esp_err_t ret = spi_bus_initialize(config->host, &bus_cfg, SPI_DMA_DISABLED);
    if (ret != ESP_OK && ret != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "spi_bus_initialize failed: %s", esp_err_to_name(ret));
        return ret;
    }

    // Add SPI device with configured clock, SPI Mode 1, and CS timing margins
    spi_device_interface_config_t dev_cfg = {
        .command_bits = 0,
        .address_bits = 0,
        .dummy_bits = 0,
        .mode = 1, // CPOL=0, CPHA=1 (ADS8688 SPI Mode 1)
        .clock_speed_hz = config->clock_speed_hz > 0 ? config->clock_speed_hz : 10000000,
        .spics_io_num = config->pin_cs,
        .queue_size = 1,
        .cs_ena_pretrans = 2,  // 2 cycles CS setup delay before SCLK
        .cs_ena_posttrans = 2, // 2 cycles CS hold delay after SCLK
    };
    ret = spi_bus_add_device(config->host, &dev_cfg, &s_spi_dev);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "spi_bus_add_device failed: %s", esp_err_to_name(ret));
        return ret;
    }

    // Set ADS8688 Channel Input Range for all channels
    uint8_t ch = (uint8_t)config->channel;
    for (int i = 0; i < 8; i++) {
        ads8688_fast_write_reg(s_range_regs[i], (uint8_t)config->range);
    }

    // Verify CH0 range register readback
    uint8_t read_rx[3] = {0};
    uint8_t read_tx[3] = {(uint8_t)(s_range_regs[ch] << 1), 0x00, 0x00};
    spi_transaction_t r_trans = {
        .length = 24,
        .tx_buffer = read_tx,
        .rx_buffer = read_rx,
    };
    spi_device_polling_transmit(s_spi_dev, &r_trans);
    ESP_LOGI(TAG, "Range CH%d readback: 0x%02X (Configured: 0x%02X)", ch, read_rx[2], (uint8_t)config->range);

    s_hw = SPI_LL_GET_HW(config->host);

    uint32_t clk_hz = (config->clock_speed_hz > 0) ? (uint32_t)config->clock_speed_hz : 10000000;
    int actual_clk = spi_ll_master_set_clock(s_hw, 80 * 1000 * 1000, clk_hz, 128);
    spi_ll_master_set_mode(s_hw, 1); // Mode 1 (CPOL=0, CPHA=1)
    spi_ll_master_select_cs(s_hw, 0); // Hardware CS0

    // Configure SPI2 for direct 32-bit user transfers
    spi_ll_set_mosi_bitlen(s_hw, 32);
    spi_ll_set_miso_bitlen(s_hw, 32);
    spi_ll_apply_config(s_hw);

    // Initial dummy conversions to prime ADS8688 pipeline on selected channel
    uint16_t ch_cmd = (ch < 8) ? s_channel_cmds[ch] : ADS8688_CMD_MAN_CH0;
    ads8688_fast_trans32(ch_cmd);
    esp_rom_delay_us(10);
    ads8688_fast_trans32(ADS8688_CMD_NO_OP);
    esp_rom_delay_us(10);

    // Benchmark pure hardware SPI transfer speed (1000 iterations)
    uint32_t bench_start = esp_cpu_get_cycle_count();
    for (int i = 0; i < 1000; i++) {
        ads8688_fast_trans32(ADS8688_CMD_NO_OP);
    }
    uint32_t bench_cycles = (esp_cpu_get_cycle_count() - bench_start) / 1000;
    uint32_t cur_cpu_hz = esp_clk_cpu_freq();
    float bench_us = ((float)bench_cycles / (float)cur_cpu_hz) * 1000000.0f;

    ESP_LOGI(TAG, "ADS8688 Fast Driver initialized! Actual SPI SCLK: %d Hz, Channel: CH%d, Target Rate: %" PRIu32 " Hz",
             actual_clk, ch, config->sample_rate_hz);
    ESP_LOGI(TAG, "Benchmark: Pure SPI32 transfer costs %u CPU cycles (%.2f us) on %" PRIu32 " MHz CPU",
             (unsigned int)bench_cycles, bench_us, cur_cpu_hz / 1000000);

    return ESP_OK;
}

void ads8688_fast_get_ready_buffer(const uint16_t **out_buf, uint8_t *out_seq)
{
    if (out_buf) *out_buf = (const uint16_t *)s_ready_buf;
    if (out_seq) *out_seq = s_ready_seq;
}

IRAM_ATTR void ads8688_fast_start_sampling_loop(void *notify_task_handle)
{
    TaskHandle_t notify_task = (TaskHandle_t)notify_task_handle;
    s_is_sampling = true;

    // Dynamically retrieve actual CPU clock frequency
    uint32_t cpu_freq = esp_clk_cpu_freq();
    uint32_t target_rate = s_cfg.sample_rate_hz > 0 ? s_cfg.sample_rate_hz : 220000;
    uint32_t cycles_per_sample = cpu_freq / target_rate; // e.g. 1091 cycles for 240MHz @ 220kSPS, 727 cycles for 160MHz @ 220kSPS

    size_t sample_idx = 0;
    uint8_t seq = 0;

    ESP_LOGI(TAG, "Starting dedicated Core 1 sampling loop in IRAM (CPU: %" PRIu32 " MHz, %u cycles/sample, Target: %" PRIu32 " Hz)...",
             cpu_freq / 1000000, (unsigned int)cycles_per_sample, target_rate);

    uint32_t next_cycle = esp_cpu_get_cycle_count() + cycles_per_sample;

    while (s_is_sampling) {
        // 1. High-precision hardware CPU spinwait in IRAM
        while ((int32_t)(esp_cpu_get_cycle_count() - next_cycle) < 0) {
            // Tight spinwait
        }
        next_cycle += cycles_per_sample;

        // Reset if drifted too far behind (e.g. initial connection)
        uint32_t current_cycle = esp_cpu_get_cycle_count();
        if ((int32_t)(current_cycle - next_cycle) > 5000) {
            next_cycle = current_cycle + cycles_per_sample;
        }

        // 2. Hardware direct 32-bit SPI transfer
        uint16_t raw_val = ads8688_fast_trans32(ADS8688_CMD_NO_OP);

        // 3. Store into active buffer
        s_active_buf[sample_idx++] = raw_val;

        // 4. Ping-Pong buffer swap when 512 samples are reached
        if (sample_idx >= ADS8688_FAST_BUFFER_SIZE) {
            sample_idx = 0;
            s_ready_buf = s_active_buf;
            s_ready_seq = seq++;

            // Swap active buffer
            s_active_buf = (s_active_buf == s_buffer_a) ? s_buffer_b : s_buffer_a;

            // Notify Core 0 USB task
            if (notify_task != NULL) {
                xTaskNotifyGive(notify_task);
            }
        }
    }
}
