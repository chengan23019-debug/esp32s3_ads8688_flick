/**
 * @file usb_stream.h
 * @brief High-speed USB streaming communication for ESP32-S3
 */

#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define USB_STREAM_MAGIC_HEAD       0x55AA
#define USB_STREAM_MAGIC_TAIL       0xAA55

#define USB_STREAM_PKT_DATA         0x01
#define USB_STREAM_PKT_BENCHMARK    0x02

/**
 * @brief Frame Header (6 bytes)
 */
typedef struct __attribute__((packed)) {
    uint16_t magic_head;    ///< 0x55AA
    uint8_t  pkt_type;      ///< Packet type (0x01: Data, 0x02: Benchmark)
    uint8_t  seq_num;       ///< Frame sequence number (0~255)
    uint16_t payload_len;   ///< Length of payload data in bytes
} usb_stream_header_t;

/**
 * @brief Frame Footer (4 bytes)
 */
typedef struct __attribute__((packed)) {
    uint16_t checksum;      ///< Simple 16-bit additive checksum of payload
    uint16_t magic_tail;    ///< 0xAA55
} usb_stream_footer_t;

/**
 * @brief Initialize USB High-speed Streaming driver
 *
 * @param tx_buf_size Software TX buffer size in bytes (e.g. 8192 or 16384)
 * @param rx_buf_size Software RX buffer size in bytes
 * @return ESP_OK on success
 */
esp_err_t usb_stream_init(size_t tx_buf_size, size_t rx_buf_size);

/**
 * @brief Send raw bytes directly over USB
 *
 * @param data Pointer to buffer
 * @param len Number of bytes to send
 * @param timeout_ticks Max ticks to wait if buffer is full
 * @return Number of bytes actually written
 */
int usb_stream_write_raw(const void *data, size_t len, uint32_t timeout_ticks);

/**
 * @brief Package and send a high-speed data frame
 *
 * @param pkt_type Packet type
 * @param seq_num Sequence counter
 * @param payload Pointer to raw ADC samples (or simulated samples)
 * @param payload_len Payload length in bytes
 * @param timeout_ticks Max ticks to wait
 * @return Total bytes written, or negative on error
 */
int usb_stream_send_frame(uint8_t pkt_type, uint8_t seq_num, const void *payload, size_t payload_len, uint32_t timeout_ticks);

/**
 * @brief Check if USB is connected and ready for transfer
 *
 * @return true if connected
 */
bool usb_stream_is_connected(void);

#ifdef __cplusplus
}
#endif
