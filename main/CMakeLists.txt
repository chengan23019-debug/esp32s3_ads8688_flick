idf_component_register(SRCS "ads8688_main.c" "usb_stream.c"
                    PRIV_REQUIRES spi_flash ads8688 driver esp_driver_spi esp_driver_gpio esp_driver_usb_serial_jtag
                    INCLUDE_DIRS ".")
