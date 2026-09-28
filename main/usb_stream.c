/**
 * @file usb_stream.c
 * @brief High-speed USB streaming communication implementation for ESP32-S3
 */

#include "usb_stream.h"
#include <string.h>
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/usb_serial_jtag.h"

static const char *TAG = "USB_STREAM";
static bool s_is_initialized = false;

esp_err_t usb_stream_init(size_t tx_buf_size, size_t rx_buf_size)
{
    if (s_is_initialized) {
        return ESP_OK;
    }

    usb_serial_jtag_driver_config_t cfg = {
        .tx_buffer_size = (uint32_t)tx_buf_size,
        .rx_buffer_size = (uint32_t)rx_buf_size,
    };

    esp_err_t ret = usb_serial_jtag_driver_install(&cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to install usb_serial_jtag driver: %s", esp_err_to_name(ret));
        return ret;
    }

    s_is_initialized = true;
    ESP_LOGI(TAG, "USB Streaming driver installed (TX buf: %u bytes, RX buf: %u bytes)",
             (unsigned int)tx_buf_size, (unsigned int)rx_buf_size);
    return ESP_OK;
}

int usb_stream_write_raw(const void *data, size_t len, uint32_t timeout_ticks)
{
    if (!s_is_initialized || data == NULL || len == 0) {
        return -1;
    }
    return usb_serial_jtag_write_bytes(data, len, timeout_ticks);
}

int usb_stream_send_frame(uint8_t pkt_type, uint8_t seq_num, const void *payload, size_t payload_len, uint32_t timeout_ticks)
{
    if (!s_is_initialized || payload == NULL || payload_len == 0) {
        return -1;
    }

    // Static buffer for single-atomic frame transmission (up to 2048 bytes payload)
    // Frame = Header (6) + Payload (up to 2048) + Footer (4)
    static uint8_t s_frame_buf[6 + 2048 + 4];
    size_t total_frame_len = sizeof(usb_stream_header_t) + payload_len + sizeof(usb_stream_footer_t);

    if (total_frame_len > sizeof(s_frame_buf)) {
        return -4; // Payload too large for static buffer
    }

    // Calculate simple 16-bit additive checksum of payload
    const uint8_t *p = (const uint8_t *)payload;
    uint16_t sum = 0;
    for (size_t i = 0; i < payload_len; i++) {
        sum += p[i];
    }

    usb_stream_header_t *hdr = (usb_stream_header_t *)s_frame_buf;
    hdr->magic_head  = USB_STREAM_MAGIC_HEAD;
    hdr->pkt_type    = pkt_type;
    hdr->seq_num     = seq_num;
    hdr->payload_len = (uint16_t)payload_len;

    // Copy payload
    memcpy(s_frame_buf + sizeof(usb_stream_header_t), payload, payload_len);

    // Footer
    usb_stream_footer_t *ftr = (usb_stream_footer_t *)(s_frame_buf + sizeof(usb_stream_header_t) + payload_len);
    ftr->checksum   = sum;
    ftr->magic_tail = USB_STREAM_MAGIC_TAIL;

    // Single atomic write to USB-Serial-JTAG FIFO
    int written = usb_serial_jtag_write_bytes(s_frame_buf, total_frame_len, timeout_ticks);
    return written;
}

bool usb_stream_is_connected(void)
{
    return usb_serial_jtag_is_connected();
}
