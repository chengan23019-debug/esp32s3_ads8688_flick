/**
 * @file ads8688_main.c
 * @brief High-Speed 220 kSPS Sampling with ADS8688 (±5.12V Bipolar) and Real-time USB Stream for ESP32-S3
 */

#include <stdio.h>
#include <inttypes.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_task_wdt.h"
#include "ads8688_fast.h"
#include "usb_stream.h"

static const char *TAG = "APP_MAIN";

#define SAMPLING_RATE_HZ   220000 // 220 kSPS high-speed single-channel target

static TaskHandle_t s_usb_tx_task_handle = NULL;
static TaskHandle_t s_adc_sample_task_handle = NULL;

/**
 * @brief Core 0 Task: Receives full buffers from Core 1 and sends them over USB
 */
static void usb_tx_task(void *pvParameters)
{
    ESP_LOGI(TAG, "USB TX Task started on Core 0 (waiting for ADC buffers)...");

    int64_t last_stat_time = esp_timer_get_time();
    uint32_t frames_sent_last_sec = 0;
    uint32_t bytes_sent_last_sec = 0;

    while (1) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        if (!usb_stream_is_connected()) {
            continue;
        }

        const uint16_t *ready_buf = NULL;
        uint8_t seq = 0;
        ads8688_fast_get_ready_buffer(&ready_buf, &seq);

        if (ready_buf != NULL) {
            int sent = usb_stream_send_frame(
                USB_STREAM_PKT_DATA,
                seq,
                ready_buf,
                ADS8688_FAST_BUFFER_SIZE * sizeof(uint16_t), // 1024 bytes
                pdMS_TO_TICKS(10)
            );

            if (sent > 0) {
                frames_sent_last_sec++;
                bytes_sent_last_sec += sent;
            }
        }

        int64_t now = esp_timer_get_time();
        if (now - last_stat_time >= 2000000) {
            double elapsed = (now - last_stat_time) / 1000000.0;
            double kb_s = (bytes_sent_last_sec / 1024.0) / elapsed;
            double mbps = (bytes_sent_last_sec * 8.0 / 1e6) / elapsed;
            double fps = frames_sent_last_sec / elapsed;

            ESP_LOGI(TAG, "[Real ADS8688 Live] Speed: %.2f KB/s (%.2f Mbps) | FPS: %.1f | Connected: %s",
                     kb_s, mbps, fps, usb_stream_is_connected() ? "YES" : "NO");

            frames_sent_last_sec = 0;
            bytes_sent_last_sec = 0;
            last_stat_time = now;
        }
    }
}

/**
 * @brief Core 1 Task: Dedicated high-priority ADC sampling loop
 */
static void adc_sample_task(void *pvParameters)
{
    ESP_LOGI(TAG, "ADC Sample Task started on Core 1 (Priority 24)...");

    // Unsubscribe IDLE1 from Task Watchdog so Core 1 can run 100% dedicated sampling
    TaskHandle_t idle1_handle = xTaskGetIdleTaskHandleForCPU(1);
    if (idle1_handle != NULL) {
        esp_task_wdt_delete(idle1_handle);
    }

    ads8688_fast_start_sampling_loop(s_usb_tx_task_handle);
    vTaskDelete(NULL);
}

void app_main(void)
{
    ESP_LOGI(TAG, "=== ESP32-S3 ADS8688 220 kSPS Real-Time Streaming System (±5.12V Range) ===");

    // 1. Initialize USB High-Speed Streaming
    ESP_LOGI(TAG, "Initializing USB Streaming driver...");
    esp_err_t err_usb = usb_stream_init(16384, 1024);
    if (err_usb != ESP_OK) {
        ESP_LOGE(TAG, "USB stream initialization failed: %s", esp_err_to_name(err_usb));
    } else {
        ESP_LOGI(TAG, "USB stream driver initialized successfully!");
    }

    // 2. Initialize ADS8688 Fast Register Driver with ±5.12V Range
    ads8688_fast_config_t fast_cfg = {
        .host           = SPI2_HOST,
        .pin_cs         = ADS8688_DEFAULT_PIN_CS,    // GPIO14
        .pin_sclk       = ADS8688_DEFAULT_PIN_SCLK,  // GPIO15
        .pin_miso       = ADS8688_DEFAULT_PIN_SDO,   // GPIO12
        .pin_mosi       = ADS8688_DEFAULT_PIN_SDI,   // GPIO13
        .pin_rst        = ADS8688_DEFAULT_PIN_RST,   // GPIO11
        .pin_daisy      = ADS8688_DEFAULT_PIN_DAISY, // GPIO10
        .clock_speed_hz = 10000000,                  // 10 MHz safe SPI SCLK
        .channel        = ADS8688_CH0,               // Channel 0
        .range          = ADS8688_RANGE_BIP_1V25_VREF,// ±1.25 x Vref = ±5.12V full-scale
        .sample_rate_hz = SAMPLING_RATE_HZ           // 220 kSPS
    };

    ESP_LOGI(TAG, "Initializing ADS8688 Fast Driver (±5.12V Bipolar, 10MHz SCLK)...");
    esp_err_t ret = ads8688_fast_init(&fast_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "ADS8688 fast init failed: %s", esp_err_to_name(ret));
    } else {
        ESP_LOGI(TAG, "ADS8688 fast driver initialized successfully!");
    }

    // 3. Create Core 0 USB TX Task
    xTaskCreatePinnedToCore(
        usb_tx_task,
        "usb_tx_task",
        6144,
        NULL,
        10,
        &s_usb_tx_task_handle,
        0
    );

    // 4. Create Core 1 ADC Sampling Task with High Priority (24)
    xTaskCreatePinnedToCore(
        adc_sample_task,
        "adc_sample_task",
        4096,
        NULL,
        24,
        &s_adc_sample_task_handle,
        1
    );

    ESP_LOGI(TAG, "System operational: Core 1 sampling CH0 at %d Hz, Core 0 streaming over USB.", SAMPLING_RATE_HZ);
}
