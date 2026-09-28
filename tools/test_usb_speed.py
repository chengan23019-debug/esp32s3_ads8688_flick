#!/usr/bin/env python3
"""
USB High-Speed Streaming Throughput & Packet Validation Test
Supports ESP32-S3 USB-Serial-JTAG / USB CDC communication.
"""

import sys
import time
import struct
import argparse
import serial
import serial.tools.list_ports

MAGIC_HEAD = 0x55AA
MAGIC_TAIL = 0xAA55
HEADER_FORMAT = "<HBBH"  # uint16 magic, uint8 type, uint8 seq, uint16 len
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
FOOTER_FORMAT = "<HH"    # uint16 checksum, uint16 magic_tail
FOOTER_SIZE = struct.calcsize(FOOTER_FORMAT)

def list_available_ports():
    ports = serial.tools.list_ports.comports()
    print("Available serial ports:")
    for p in ports:
        print(f"  {p.device:12s} - {p.description}")
    return [p.device for p in ports]

def open_serial_safe(port_name, baudrate=115200, timeout=1.0):
    ser = serial.Serial()
    ser.port = port_name
    ser.baudrate = baudrate
    ser.timeout = timeout

    # Windows usbser.sys driver does not support standard UART SetCommState parameters.
    # Safely ignore the configuration exception if it fails with code 31 (ERROR_GEN_FAILURE).
    orig_reconf = ser._reconfigure_port
    def safe_reconfigure_port():
        try:
            orig_reconf()
        except Exception:
            pass
    ser._reconfigure_port = safe_reconfigure_port

    ser.open()
    try:
        ser.dtr = True
        ser.rts = True
    except Exception:
        pass
    return ser

def run_benchmark(port_name, baudrate=115200, duration=10):
    print(f"\n[*] Opening port {port_name} (Baud: {baudrate})...")
    try:
        ser = open_serial_safe(port_name, baudrate=baudrate, timeout=1.0)
    except Exception as e:
        print(f"[!] Failed to open port {port_name}: {e}")
        return

    # Flush any leftover data
    ser.reset_input_buffer()
    print("[*] Port opened successfully. Listening for data frames...\n")

    rx_buffer = bytearray()
    total_bytes = 0
    total_frames = 0
    total_lost_frames = 0
    checksum_errors = 0
    last_seq = None

    start_time = time.time()
    last_stat_time = start_time
    bytes_since_last_stat = 0
    frames_since_last_stat = 0

    try:
        while True:
            current_time = time.time()
            if duration > 0 and (current_time - start_time) > duration:
                print(f"\n[*] Test duration of {duration}s reached. Stopping.")
                break

            # Read whatever is available
            in_waiting = ser.in_waiting
            read_size = in_waiting if in_waiting > 0 else 1024
            chunk = ser.read(read_size)
            if not chunk:
                continue

            rx_buffer.extend(chunk)
            chunk_len = len(chunk)
            total_bytes += chunk_len
            bytes_since_last_stat += chunk_len

            # Frame extraction loop
            while len(rx_buffer) >= (HEADER_SIZE + FOOTER_SIZE):
                # Look for MAGIC_HEAD (0xAA, 0x55 in little endian -> 0x55AA)
                head_idx = rx_buffer.find(b'\xaa\x55')
                if head_idx == -1:
                    # Keep the last byte in case it's part of a split magic word
                    rx_buffer = rx_buffer[-1:]
                    break

                if head_idx > 0:
                    # Discard bytes before head
                    del rx_buffer[:head_idx]

                if len(rx_buffer) < HEADER_SIZE:
                    break

                # Unpack header
                magic, pkt_type, seq, payload_len = struct.unpack(HEADER_FORMAT, rx_buffer[:HEADER_SIZE])
                full_frame_len = HEADER_SIZE + payload_len + FOOTER_SIZE

                if len(rx_buffer) < full_frame_len:
                    # Wait for more data to arrive
                    break

                # Extract payload & footer
                payload = rx_buffer[HEADER_SIZE : HEADER_SIZE + payload_len]
                footer_bytes = rx_buffer[HEADER_SIZE + payload_len : full_frame_len]
                checksum, magic_tail = struct.unpack(FOOTER_FORMAT, footer_bytes)

                # Validate magic tail
                if magic_tail == MAGIC_TAIL:
                    # Validate checksum
                    calc_sum = sum(payload) & 0xFFFF
                    if calc_sum != checksum:
                        checksum_errors += 1

                    # Check sequence continuity
                    if last_seq is not None:
                        expected_seq = (last_seq + 1) & 0xFF
                        if seq != expected_seq:
                            lost = (seq - expected_seq) & 0xFF
                            total_lost_frames += lost
                    last_seq = seq

                    # Parse first ADC sample of payload for real-time live preview
                    if len(payload) >= 2:
                        latest_sample = struct.unpack("<H", payload[:2])[0]
                        # ADS8688 Bipolar ±5.12V formula
                        raw_bip_volt = ((latest_sample - 32768) / 32768.0) * 5.12
                        # Calibrated true input voltage based on observed offset & 2x scale
                        cal_volt = (raw_bip_volt + 5.12) / 2.0
                        latest_volt = cal_volt

                    total_frames += 1
                    frames_since_last_stat += 1

                    # Consume frame
                    del rx_buffer[:full_frame_len]
                else:
                    # False sync header, skip 2 bytes and continue search
                    del rx_buffer[:2]

            # Periodic statistics output (every 0.5s)
            elapsed_stat = current_time - last_stat_time
            if elapsed_stat >= 0.5:
                speed_kb = (bytes_since_last_stat / 1024.0) / elapsed_stat
                speed_mbps = (bytes_since_last_stat * 8.0 / 1e6) / elapsed_stat
                fps = frames_since_last_stat / elapsed_stat
                
                elapsed_total = current_time - start_time
                avg_speed_kb = (total_bytes / 1024.0) / elapsed_total if elapsed_total > 0 else 0

                loss_rate = (total_lost_frames / (total_frames + total_lost_frames) * 100.0) if (total_frames + total_lost_frames) > 0 else 0.0

                sys.stdout.write(
                    f"\r[Speed: {speed_kb:7.2f} KB/s ({speed_mbps:5.2f} Mbps) | "
                    f"FPS: {fps:4.0f} | "
                    f"Raw: 0x{latest_sample:04X} ({latest_sample:5d}) | "
                    f"V_in: {latest_volt:+6.3f}V | "
                    f"Lost: {total_lost_frames:3d} ({loss_rate:4.2f}%) | "
                    f"ChkErr: {checksum_errors:2d}]"
                )
                sys.stdout.flush()

                bytes_since_last_stat = 0
                frames_since_last_stat = 0
                last_stat_time = current_time

    except KeyboardInterrupt:
        print("\n\n[!] Test stopped by user.")
    finally:
        ser.close()

    total_time = time.time() - start_time
    avg_speed_kb = (total_bytes / 1024.0) / total_time if total_time > 0 else 0
    avg_mbps = (total_bytes * 8.0 / 1e6) / total_time if total_time > 0 else 0
    print("\n\n========== Test Summary ==========")
    print(f"Total time elapsed   : {total_time:.2f} s")
    print(f"Total data received  : {total_bytes / 1024.0:.2f} KB ({total_bytes / (1024.0 * 1024.0):.2f} MB)")
    print(f"Average throughput   : {avg_speed_kb:.2f} KB/s ({avg_mbps:.2f} Mbps)")
    print(f"Total frames received: {total_frames}")
    print(f"Total frames lost    : {total_lost_frames}")
    print(f"Checksum errors      : {checksum_errors}")
    print("===================================\n")

def main():
    parser = argparse.ArgumentParser(description="ESP32-S3 High-Speed USB Communication Benchmark")
    parser.add_argument("-p", "--port", type=str, help="Serial port (e.g. COM3, /dev/ttyACM0)")
    parser.add_argument("-b", "--baud", type=int, default=2000000, help="Baud rate (default: 2000000)")
    parser.add_argument("-d", "--duration", type=float, default=0, help="Test duration in seconds (0 = run until Ctrl+C)")
    args = parser.parse_args()

    if not args.port:
        available = list_available_ports()
        if not available:
            print("[!] No serial ports found. Please connect your ESP32-S3.")
            return
        args.port = available[0]
        print(f"[*] Automatically selected port: {args.port}")

    run_benchmark(args.port, args.baud, args.duration)

if __name__ == "__main__":
    main()
